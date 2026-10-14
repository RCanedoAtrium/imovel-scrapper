from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pandas as pd

from .config import Settings
from .errors import DataQualityError
from .history import ListingHistoryStore
from .models import StageResult
from .normalization import (
    clean_numeric,
    detect_area_warning,
    extract_area_m2,
    extract_currency,
    extract_listing_id,
    extract_nested,
    find_excluded_region,
    is_legacy_listing,
    normalize_cep,
    normalize_column_name,
    normalize_neighborhood,
    normalize_number,
    normalize_street,
    parse_listing_address,
)
from .parking import classify_parking
from .sources.pbh import IPTU_REQUIRED, ITBI_REQUIRED, validate_csv_schema
from .storage import read_csv_auto, write_dataset


def _read_csv(path: Path, sep: str | None = None) -> pd.DataFrame:
    failures: list[str] = []
    if sep is None:
        with path.open("r", encoding="utf-8-sig", errors="replace") as f:
            first_line = f.readline()
        sep = ";" if first_line.count(";") > first_line.count(",") else ","
    for encoding in ("utf-8-sig", "latin1"):
        try:
            return pd.read_csv(path, sep=sep, encoding=encoding, dtype=str, low_memory=False)
        except (UnicodeDecodeError, pd.errors.ParserError) as exc:
            failures.append(f"{encoding}: {exc}")
    raise DataQualityError(f"Could not read {path}: {' | '.join(failures)}")


def _parse_itbi_address(value: object) -> tuple[str, str, str]:
    if value is None or pd.isna(value):
        return "", "", ""
    text = str(value)
    cep_match = re.search(r"\b\d{5}-?\d{3}\b", text)
    cep = normalize_cep(cep_match.group(0)) if cep_match else ""
    first = text.split("-")[0].strip()
    match = re.match(r"(.+?)\s+(\d{1,6})\s*$", first)
    if not match:
        return normalize_street(first), "", cep
    return normalize_street(match.group(1)), normalize_number(match.group(2)), cep


def _canonicalize_itbi_columns(frame: pd.DataFrame) -> pd.DataFrame:
    aliases = {
        "ANO DE CONSTRUCAO UNIDADE": "ANO DE CONSTRUCAO (UNIDADE)",
        "PADRAO ACABAMENTO UNIDADE": "PADRAO ACABAMENTO (UNIDADE)",
        "DESCRICAO TIPO OCUPACAO UNIDADE": "DESCRICAO TIPO OCUPACAO (UNIDADE)",
    }
    return frame.rename(columns={column: aliases.get(column, column) for column in frame.columns})


def _competence_from_name(path: Path) -> str:
    match = re.search(r"(20\d{2})(0[1-9]|1[0-2])", path.name)
    return f"{match.group(1)}-{match.group(2)}" if match else ""


def prepare_itbi(settings: Settings) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, str], dict[tuple[str, str], int]]:
    files = sorted(settings.paths.itbi.glob("*.csv"))
    if not files:
        raise DataQualityError(f"No ITBI CSV files found in {settings.paths.itbi}")
    frames: list[pd.DataFrame] = []
    for path in files:
        validate_csv_schema(path, ITBI_REQUIRED)
        frame = _read_csv(path)
        frame.columns = [normalize_column_name(column) for column in frame.columns]
        frame = _canonicalize_itbi_columns(frame)
        frame["SOURCE_FILE"] = path.name
        frame["COMPETENCE"] = _competence_from_name(path)
        frames.append(frame)
    raw = pd.concat(frames, ignore_index=True)
    occupation = "DESCRICAO TIPO OCUPACAO (UNIDADE)"
    raw = raw[raw[occupation].fillna("").str.upper() == "RESIDENCIAL"].copy()
    raw["area_m2"] = raw["AREA CONSTRUIDA ADQUIRIDA"].map(clean_numeric)
    raw["declared_price"] = raw["VALOR DECLARADO"].map(clean_numeric)
    raw["base_price"] = raw["VALOR BASE CALCULO"].map(clean_numeric)
    raw["transaction_price"] = raw[["declared_price", "base_price"]].max(axis=1)
    raw["neighborhood"] = raw["BAIRRO"].map(normalize_neighborhood)
    min_price = settings.rules.min_valid_price
    min_area = settings.rules.min_valid_area_m2
    raw = raw[(raw["transaction_price"] > min_price) & (raw["area_m2"] > min_area) & (raw["neighborhood"] != "")].copy()
    raw["price_m2"] = raw["transaction_price"] / raw["area_m2"]
    addresses = raw.get("ENDERECO", pd.Series("", index=raw.index)).map(_parse_itbi_address)
    raw["street"] = [value[0] for value in addresses]
    raw["number"] = [value[1] for value in addresses]
    raw["cep"] = [value[2] for value in addresses]
    raw["building_year"] = raw.get(
        "ANO DE CONSTRUCAO (UNIDADE)", pd.Series(0, index=raw.index)
    ).map(clean_numeric).astype(int)
    raw["finish_pattern"] = raw.get(
        "PADRAO ACABAMENTO (UNIDADE)", pd.Series("", index=raw.index)
    ).fillna("").str.upper()
    transactions = raw[[
        "COMPETENCE", "SOURCE_FILE", "neighborhood", "street", "number", "cep",
        "area_m2", "declared_price", "base_price", "transaction_price", "price_m2",
        "building_year", "finish_pattern",
    ]].rename(columns={"COMPETENCE": "competence", "SOURCE_FILE": "source_file"})

    stats = transactions.groupby("neighborhood").agg(
        itbi_m2_mean=("price_m2", "mean"),
        itbi_m2_median=("price_m2", "median"),
        itbi_m2_p30=("price_m2", lambda values: values.quantile(0.30)),
        itbi_m2_p80=("price_m2", lambda values: values.quantile(0.80)),
        median_area=("area_m2", "median"),
        itbi_sales_count=("price_m2", "count"),
        itbi_total_volume=("transaction_price", "sum"),
    ).reset_index()
    stats = stats[stats["itbi_sales_count"] >= settings.min_transactions].copy()
    cep_map = (
        transactions[transactions["cep"] != ""]
        .groupby("cep")["neighborhood"]
        .agg(lambda values: values.mode().iloc[0] if not values.mode().empty else "")
        .to_dict()
    )
    valid_years = transactions[transactions["building_year"] > 1800]
    year_map = valid_years.groupby(["street", "number"])["building_year"].median().astype(int).to_dict()
    return transactions, stats, cep_map, year_map


def _latest_iptu_file(settings: Settings) -> Path:
    files = list(settings.paths.iptu.glob("*.csv"))
    if not files:
        raise DataQualityError(f"No IPTU CSV files found in {settings.paths.iptu}")
    dated = [(_competence_from_name(path), path) for path in files]
    return max(dated, key=lambda item: (item[0], item[1].name))[1]


def prepare_iptu(settings: Settings, cep_map: dict[str, str], year_map: dict[tuple[str, str], int]) -> pd.DataFrame:
    path = _latest_iptu_file(settings)
    validate_csv_schema(path, IPTU_REQUIRED)
    raw = _read_csv(path)
    raw.columns = [normalize_column_name(column) for column in raw.columns]
    raw = raw[raw["TIPO_CONSTRUTIVO"].fillna("").str.upper() == "APARTAMENTO"].copy()
    raw["area_m2"] = raw["AREA_CONSTRUCAO"].map(clean_numeric)
    raw["number"] = raw["NUMERO_IMOVEL"].map(normalize_number)
    raw["cep"] = raw["CEP"].map(normalize_cep)
    raw["street"] = raw["NOME_LOGRADOURO"].map(normalize_street)
    raw["finish_pattern"] = raw["PADRAO_ACABAMENTO"].fillna("").str.upper().str.strip()
    raw = raw[(raw["street"] != "") & (raw["number"] != "")].copy()
    records: list[dict[str, object]] = []
    for (street, number, cep), group in raw.groupby(["street", "number", "cep"], dropna=False):
        patterns = group.loc[group["finish_pattern"] != "", "finish_pattern"]
        records.append({
            "street": street,
            "number": number,
            "cep": cep,
            "neighborhood": cep_map.get(cep, ""),
            "finish_pattern": patterns.mode().iloc[0] if not patterns.mode().empty else "",
            "area_min": group["area_m2"].min(),
            "area_max": group["area_m2"].max(),
            "building_year": year_map.get((street, number), 0),
            "unit_count": len(group),
            "source_file": path.name,
        })
    return pd.DataFrame(records)


PORTAL_DOMAINS = {
    "vivareal": "https://www.vivareal.com.br",
    "zap": "https://www.zapimoveis.com.br",
    "zapimoveis": "https://www.zapimoveis.com.br",
    "olx": "https://www.olx.com.br",
    "quintoandar": "https://www.quintoandar.com.br",
    "imovelweb": "https://www.imovelweb.com.br",
}


def _canonical_url(value: object, site: str = "") -> str:
    if not isinstance(value, str) or not value:
        return ""
    if not value.startswith("http"):
        clean_site = re.sub(r"[^a-z]", "", str(site).lower())
        domain = PORTAL_DOMAINS.get(clean_site, "https://www.vivareal.com.br")
        prefix = "" if value.startswith("/") else "/"
        value = f"{domain}{prefix}{value}"
    parts = urlsplit(value)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def prepare_listings(
    settings: Settings,
    known_neighborhoods: list[str] | None = None,
    now: datetime | None = None,
) -> pd.DataFrame:
    listing_root = settings.paths.listings
    if not listing_root.exists():
        raise DataQualityError(f"Listing directory is missing: {listing_root}")

    run_dirs = sorted([d for d in listing_root.iterdir() if d.is_dir() and not d.name.startswith(".")])
    if not run_dirs:
        latest_pointer = listing_root / "latest.txt"
        if latest_pointer.exists():
            latest_run = latest_pointer.read_text(encoding="utf-8").strip()
            target = listing_root / latest_run
            if target.is_dir():
                run_dirs = [target]
    if not run_dirs:
        raise DataQualityError(f"No listing CSV files found in {listing_root}")

    rules = settings.rules
    records: list[dict[str, object]] = []
    now_utc = now if now is not None else datetime.now(timezone.utc)
    candidates = sorted(
        {normalize_neighborhood(value) for value in (known_neighborhoods or settings.neighborhoods) if value},
        key=len,
        reverse=True,
    )
    candidate_patterns = [
        (candidate, re.compile(rf"(?<!\w){re.escape(candidate)}(?!\w)"))
        for candidate in candidates
    ]
    target_neighborhoods = {normalize_neighborhood(value) for value in settings.neighborhoods}
    excluded_regions = [
        region for region in settings.excluded_regions
        if normalize_neighborhood(region) not in target_neighborhoods
    ]

    for run_dir in run_dirs:
        files = sorted(run_dir.glob("*.csv"))
        for path in files:
            frame = read_csv_auto(path, dtype=str, low_memory=False)
            file_mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            for _, row in frame.iterrows():
                source = row.to_dict()
                row_collected_at = str(source.get("collected_at") or "").strip()
                row_collected_date = str(source.get("collected_date") or "").strip()
                if row_collected_at:
                    try:
                        collected_datetime = datetime.fromisoformat(row_collected_at.replace("Z", "+00:00"))
                    except ValueError:
                        collected_datetime = file_mtime
                else:
                    collected_datetime = file_mtime

                collected_at = collected_datetime.isoformat()
                collected_date = row_collected_date or collected_datetime.strftime("%Y-%m-%d")
                age_days = max(0, (now_utc - collected_datetime).days)
                if age_days > settings.listing_freshness_days:
                    continue

                native_pub_date = extract_nested(source, [
                    "publication_date", "created_at", "listing.createdAt", "published_at",
                    "listing.updatedAt", "data_publicacao", "publish_date",
                ])
                if native_pub_date:
                    try:
                        pub_dt = datetime.fromisoformat(str(native_pub_date).replace("Z", "+00:00"))
                        if (now_utc - pub_dt).days > rules.max_publication_age_days:
                            continue
                    except ValueError:
                        pass

                price_value = extract_nested(source, ["price", "listing.price", "listing.prices.price", "priceValue"])
                if isinstance(price_value, dict):
                    price_value = price_value.get("value", price_value.get("amount", 0))
                area_value = extract_nested(source, ["area", "usableAreaM2", "usableAreas", "usable_area", "listing.usableAreas"])
                if isinstance(area_value, list):
                    area_value = area_value[0] if area_value else 0
                title = str(extract_nested(source, ["title", "listing.title", "name"]) or "Apartamento a venda").strip()
                price_text = str(price_value or "")
                if re.search(r"/\s*m[eê]s|\balugar\b|\bloca[cç][aã]o\b", f"{price_text} {title}", re.I):
                    continue
                if re.search(r"\bcasa\b", title, re.I):
                    continue
                price = extract_currency(price_value)
                area = extract_area_m2(area_value)
                title_area = extract_area_m2(title)
                if title_area > rules.min_valid_area_m2 and (
                    area <= rules.min_valid_area_m2 or max(area, title_area) / min(area, title_area) > rules.title_area_override_ratio
                ):
                    area = title_area
                if price <= rules.min_valid_price or area <= rules.min_valid_area_m2:
                    continue
                neighborhood_value = extract_nested(source, [
                    "neighborhood", "address.neighborhood", "listing.address.neighborhood", "listing.address.bairro", "bairro",
                ])
                if isinstance(neighborhood_value, dict):
                    neighborhood_value = neighborhood_value.get("name", neighborhood_value.get("neighborhood", ""))
                source_site = str(extract_nested(source, ["site", "source"]) or path.stem)
                link = _canonical_url(extract_nested(source, ["url", "link", "listing.url", "listing.link"]), site=source_site)
                listing_id = str(extract_nested(source, ["listing_id", "id", "listing.id"]) or "").strip()
                if not listing_id:
                    listing_id = extract_listing_id(link, site=source_site)
                if is_legacy_listing(listing_id, site=source_site):
                    continue
                parsed_street, parsed_number, parsed_cep = parse_listing_address(title)
                direct_street = extract_nested(source, ["street", "address.street", "listing.address.street"])
                street = normalize_street(direct_street) if direct_street else parsed_street
                direct_number = extract_nested(source, ["number", "address.number", "listing.address.number"])
                number = normalize_number(direct_number) if direct_number else parsed_number
                cep = parsed_cep
                text = f"{title} {price_value or ''}"
                def detail(pattern: str) -> int:
                    match = re.search(pattern, text, re.IGNORECASE)
                    return int(match.group(1)) if match else 0
                condo_match = re.search(r"Cond\.?\s*R\$\s*([\d.]+)", str(price_value), re.I)
                iptu_match = re.search(r"IPTU\s*R\$\s*([\d.]+)", str(price_value), re.I)
                bedrooms = clean_numeric(extract_nested(source, ["bedrooms", "listing.bedrooms"])) or detail(r"(\d+)\s*(?:dormitorio|dormitório|quarto)")
                area_warning = detect_area_warning(area_value, title, area, bedrooms, area_limit=rules.typology_area_limit_m2)
                requested_neighborhood = normalize_neighborhood(neighborhood_value)
                location_text = str(extract_nested(source, ["location", "address", "listing.address"]) or "")
                if find_excluded_region(f"{location_text} {title} {link}", excluded_regions):
                    continue
                evidence_text = normalize_neighborhood(f"{location_text} {title} {link}")
                detected_neighborhood = ""
                for candidate, pattern in candidate_patterns:
                    if pattern.search(evidence_text):
                        detected_neighborhood = candidate
                        break
                if detected_neighborhood and detected_neighborhood not in target_neighborhoods:
                    continue
                final_neighborhood = detected_neighborhood or requested_neighborhood
                parking = classify_parking(title)
                records.append({
                    "source": str(extract_nested(source, ["site", "source"]) or path.stem),
                    "source_file": str(path.relative_to(settings.paths.root)),
                    "listing_id": listing_id,
                    "collected_at": collected_at,
                    "collected_date": collected_date,
                    "listing_age_days": age_days,
                    "neighborhood": final_neighborhood,
                    "requested_neighborhood": requested_neighborhood,
                    "neighborhood_method": "content" if detected_neighborhood else "search_context",
                    "title": title,
                    "price": price,
                    "area_m2": area,
                    "price_m2": price / area,
                    "url": link,
                    "bedrooms": bedrooms,
                    "bathrooms": clean_numeric(extract_nested(source, ["bathrooms", "listing.bathrooms"]))
                        or detail(r"(\d+)\s*(?:banheiro|banho)"),
                    "parking_spaces": clean_numeric(extract_nested(source, ["parking_spaces", "parking", "listing.parking_spaces"]))
                        or detail(r"(\d+)\s*(?:vaga|garagem)"),
                    **parking,
                    "condominium": clean_numeric(extract_nested(source, ["condominium", "listing.condominium"]))
                        or (clean_numeric(condo_match.group(1)) if condo_match else 0.0),
                    "advertised_iptu": clean_numeric(extract_nested(source, ["advertised_iptu", "iptu", "listing.iptu"]))
                        or (clean_numeric(iptu_match.group(1)) if iptu_match else 0.0),
                    "street": street,
                    "number": number,
                    "cep": cep,
                    "area_warning": area_warning,
                })
    listings = pd.DataFrame(records)
    if listings.empty:
        raise DataQualityError("No valid listings after normalization")
    listings["street"] = listings["street"].map(normalize_street)
    with_url = listings["url"] != ""
    url_records = listings[with_url].drop_duplicates(subset=["url", "collected_date"], keep="last")
    no_url_records = listings[~with_url].drop_duplicates(
        subset=["neighborhood", "street", "number", "area_m2", "price", "collected_date"], keep="last"
    )
    combined = pd.concat([url_records, no_url_records], ignore_index=True)
    history_store = ListingHistoryStore(settings.paths.data / "history.db")
    return history_store.record_observations(combined, now=now_utc)


def prepare_reference_data(settings: Settings) -> StageResult:
    """Normalize the already downloaded ITBI and IPTU reference files."""
    transactions, stats, cep_map, year_map = prepare_itbi(settings)
    buildings = prepare_iptu(settings, cep_map, year_map)
    artifacts: list[str] = []
    datasets = [
        (transactions, settings.paths.processed / "transactions.csv", {"source": "PBH ITBI"}),
        (stats, settings.paths.processed / "neighborhood_stats.csv", {"source": "PBH ITBI"}),
        (buildings, settings.paths.processed / "buildings.csv", {"source": "PBH IPTU"}),
    ]
    for frame, path, metadata in datasets:
        data_path, manifest_path = write_dataset(frame, path, metadata)
        artifacts.extend([str(data_path), str(manifest_path)])
    return StageResult(
        stage="prepare_reference_data",
        artifacts=artifacts,
        counts={
            "transactions": len(transactions),
            "neighborhoods": len(stats),
            "buildings": len(buildings),
        },
    )


def prepare_scraped_listings(settings: Settings, now: datetime | None = None) -> StageResult:
    """Normalize local scraper runs within the configured freshness window, without network access."""
    stats_path = settings.paths.processed / "neighborhood_stats.csv"
    known_neighborhoods: list[str] | None = None
    if stats_path.exists():
        stats = read_csv_auto(stats_path)
        if "neighborhood" in stats:
            known_neighborhoods = stats["neighborhood"].dropna().astype(str).tolist()
    listings = prepare_listings(settings, known_neighborhoods, now=now)
    path, manifest = write_dataset(
        listings,
        settings.paths.processed / "listings.csv",
        {"source": "listing collectors"},
    )
    return StageResult(
        stage="prepare_scraped_listings",
        artifacts=[str(path), str(manifest)],
        counts={"listings": len(listings)},
    )


def prepare_all(settings: Settings, now: datetime | None = None) -> StageResult:
    """Prepare local reference files and the latest scraper run."""
    reference = prepare_reference_data(settings)
    listings = prepare_scraped_listings(settings, now=now)
    return StageResult(
        stage="prepare",
        artifacts=reference.artifacts + listings.artifacts,
        counts={**reference.counts, **listings.counts},
        details={"stages": [reference.to_dict(), listings.to_dict()]},
    )
