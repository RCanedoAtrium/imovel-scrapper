from __future__ import annotations

import pandas as pd

from .config import Settings
from .errors import DataQualityError
from .models import StageResult
from .normalization import normalize_neighborhood, normalize_number, normalize_street
from .pattern_model import RandomForestPatternModel, VALID_PATTERNS
from .storage import read_csv_auto, write_dataset


def _unique_cep_map(reference: pd.DataFrame, keys: list[str]) -> dict[tuple[str, ...], str]:
    usable = reference.copy()
    for column, normalizer in {
        "street": normalize_street,
        "number": normalize_number,
        "neighborhood": normalize_neighborhood,
    }.items():
        if column in usable:
            usable[column] = usable[column].map(normalizer)
    usable["cep"] = usable["cep"].astype(str).str.replace(r"\D", "", regex=True)
    usable = usable[usable["cep"].str.len() == 8]
    for key in keys:
        usable = usable[usable[key] != ""]
    result: dict[tuple[str, ...], str] = {}
    for values, group in usable.groupby(keys, dropna=False):
        unique_ceps = group["cep"].unique()
        if len(unique_ceps) == 1:
            result[values if isinstance(values, tuple) else (values,)] = str(unique_ceps[0])
    return result


def build_cep_lookup(transactions: pd.DataFrame, buildings: pd.DataFrame) -> dict[str, dict[tuple[str, ...], str]]:
    """Create conservative CEP lookups from the two municipal sources."""
    columns = ["street", "number", "neighborhood", "cep"]
    references = pd.concat(
        [transactions.reindex(columns=columns), buildings.reindex(columns=columns)],
        ignore_index=True,
    )
    return {
        "logradouro_numero": _unique_cep_map(references, ["street", "number"]),
        "logradouro_bairro": _unique_cep_map(references, ["street", "neighborhood"]),
        "logradouro": _unique_cep_map(references, ["street"]),
    }


def infer_listing_cep(listing: pd.Series, lookup: dict[str, dict[tuple[str, ...], str]]) -> tuple[str, str]:
    """Return an existing or uniquely inferred CEP and its audit method."""
    cep = "".join(char for char in str(listing.get("cep", "") or "") if char.isdigit())
    if len(cep) == 8:
        return cep, "anuncio"
    street = normalize_street(listing.get("street", ""))
    number = normalize_number(listing.get("number", ""))
    neighborhood = normalize_neighborhood(listing.get("neighborhood", ""))
    for method, key in (
        ("logradouro_numero", (street, number)),
        ("logradouro_bairro", (street, neighborhood)),
        ("logradouro", (street,)),
    ):
        if all(key) and key in lookup[method]:
            return lookup[method][key], method
    return "", "nao_inferido"


def _choose_by_area(candidates: pd.DataFrame, area: float) -> pd.Series | None:
    if candidates.empty:
        return None
    compatible = candidates[(candidates["area_min"] <= area) & (candidates["area_max"] >= area)]
    if len(compatible) == 1:
        return compatible.iloc[0]
    if not compatible.empty:
        candidates = compatible
    midpoint = (candidates["area_min"] + candidates["area_max"]) / 2
    return candidates.loc[(midpoint - area).abs().idxmin()]


def match_building(listing: pd.Series, buildings: pd.DataFrame) -> tuple[pd.Series | None, str, str]:
    cep = listing.get("cep", "")
    number = listing.get("number", "")
    street = listing.get("street", "")
    neighborhood = listing.get("neighborhood", "")
    area = float(listing.get("area_m2", 0) or 0)

    if cep and number:
        candidates = buildings[(buildings["cep"] == cep) & (buildings["number"] == number)]
        if len(candidates) == 1:
            return candidates.iloc[0], "confirmado", "cep+numero_unico"
        chosen = _choose_by_area(candidates, area)
        if chosen is not None:
            return chosen, "provavel", "cep+numero_ambiguo_resolvido_por_area"

    if street and number:
        candidates = buildings[(buildings["street"] == street) & (buildings["number"] == number)]
        if neighborhood:
            same_neighborhood = candidates[candidates["neighborhood"] == neighborhood]
            if not same_neighborhood.empty:
                candidates = same_neighborhood
        chosen = _choose_by_area(candidates, area)
        if chosen is not None:
            return chosen, "provavel", "logradouro+numero"

    if street and neighborhood:
        candidates = buildings[
            (buildings["street"] == street) & (buildings["neighborhood"] == neighborhood)
        ]
        compatible = candidates[(candidates["area_min"] <= area) & (candidates["area_max"] >= area)]
        if len(compatible) == 1:
            return compatible.iloc[0], "provavel", "logradouro+bairro+area"

    return None, "sem_match", "dados_insuficientes_ou_sem_correspondencia"


class PatternClassifier:
    def __init__(self, transactions: pd.DataFrame, stats: pd.DataFrame, mode: str):
        self.stats = stats.set_index("neighborhood").to_dict("index") if not stats.empty else {}
        self.mode = mode
        self.model: RandomForestPatternModel | None = None
        self.version = "heuristic-v1"
        self.metadata: dict[str, object] = {
            "version": self.version,
            "features": ["neighborhood", "price_m2"],
        }
        if mode == "ml":
            self.model = RandomForestPatternModel(transactions)
            self.version = self.model.version
            self.metadata = self.model.metadata

    def classify(
        self,
        neighborhood: str,
        street: str,
        area: float,
        price_m2: float,
    ) -> tuple[str, float, str]:
        if self.model is not None:
            prediction = self.model.predict(neighborhood, street, area)
            return prediction.pattern, prediction.confidence, prediction.method
        reference = self.stats.get(neighborhood)
        if not reference:
            return "P3", 0.40, "heuristica_sem_referencia"
        if price_m2 <= reference["itbi_m2_p30"]:
            pattern = "P3"
        elif price_m2 <= reference["itbi_m2_p80"]:
            pattern = "P4"
        else:
            pattern = "P5" if area >= reference["median_area"] * 0.8 else "P4"
        return pattern, 0.70, "itbi_heuristica"

    def classify_many(self, listings: pd.DataFrame) -> list[tuple[str, float, str]] | None:
        if self.model is None:
            return None
        return [
            (prediction.pattern, prediction.confidence, prediction.method)
            for prediction in self.model.predict_batch(listings)
        ]


def analyze(settings: Settings, classifier_mode: str | None = None, discount_threshold: float | None = None) -> StageResult:
    paths = settings.paths.processed
    required = [
        paths / "transactions.csv", paths / "neighborhood_stats.csv",
        paths / "buildings.csv", paths / "listings.csv",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise DataQualityError("Datasets preparados ausentes: " + ", ".join(missing))
    transactions = read_csv_auto(required[0])
    stats = read_csv_auto(required[1])
    buildings = read_csv_auto(required[2])
    listings = read_csv_auto(required[3])
    for frame in (transactions, buildings):
        frame["street"] = frame["street"].map(normalize_street)
        frame["number"] = frame["number"].map(normalize_number)
        frame["neighborhood"] = frame["neighborhood"].map(normalize_neighborhood)
        frame["cep"] = frame["cep"].fillna("").astype(str).str.replace(r"\D", "", regex=True)
    cep_lookup = build_cep_lookup(transactions, buildings)
    classifier = PatternClassifier(transactions, stats, classifier_mode or settings.classifier)
    batch_predictions = classifier.classify_many(listings)
    threshold = settings.discount_threshold if discount_threshold is None else discount_threshold
    stats_index = stats.set_index("neighborhood").to_dict("index")
    p3_medians = (
        transactions[transactions["finish_pattern"] == "P3"]
        .groupby("neighborhood")["price_m2"]
        .median()
        .to_dict()
    )
    pattern_stats = (
        transactions[transactions["finish_pattern"].isin(VALID_PATTERNS)]
        .groupby(["neighborhood", "finish_pattern"])["price_m2"]
        .agg(["mean", "median", "count"])
        .to_dict("index")
    )
    output: list[dict[str, object]] = []
    for position, (_, listing) in enumerate(listings.iterrows()):
        listing = listing.copy()
        listing["street"] = normalize_street(listing.get("street", ""))
        listing["number"] = normalize_number(listing.get("number", ""))
        original_cep = str(listing.get("cep", "") or "")
        inferred_cep, cep_inference_method = infer_listing_cep(listing, cep_lookup)
        listing["cep"] = inferred_cep
        matched, evidence, match_reason = match_building(listing, buildings)
        pattern = ""
        confidence = 0.0
        method = ""
        building_year = 0
        if matched is not None and str(matched.get("finish_pattern", "")) in VALID_PATTERNS:
            pattern = str(matched["finish_pattern"])
            confidence = 1.0 if evidence == "confirmado" else 0.85
            method = "iptu_deterministico"
            building_year = int(matched.get("building_year", 0) or 0)
        else:
            if batch_predictions is not None:
                pattern, confidence, method = batch_predictions[position]
            else:
                pattern, confidence, method = classifier.classify(
                    str(listing["neighborhood"]), str(listing["street"]),
                    float(listing["area_m2"]), float(listing["price_m2"]),
                )
            evidence = "estimado"
        neighborhood = str(listing["neighborhood"])
        reference = stats_index.get(neighborhood, {})
        mean_m2 = float(reference.get("itbi_m2_mean", 0) or 0)
        median_m2 = float(reference.get("itbi_m2_median", 0) or 0)
        sales_count = int(reference.get("itbi_sales_count", 0) or 0)
        pattern_reference = pattern_stats.get((neighborhood, pattern), {})
        pattern_mean_m2 = float(pattern_reference.get("mean", 0) or 0)
        pattern_median_m2 = float(pattern_reference.get("median", 0) or 0)
        pattern_sales_count = int(pattern_reference.get("count", 0) or 0)
        discount_mean = (mean_m2 - float(listing["price_m2"])) / mean_m2 if mean_m2 else 0.0
        discount_pattern_mean = (
            (pattern_mean_m2 - float(listing["price_m2"])) / pattern_mean_m2
            if pattern_mean_m2 else 0.0
        )
        discount_median = (median_m2 - float(listing["price_m2"])) / median_m2 if median_m2 else 0.0
        p3_median = float(p3_medians.get(neighborhood, 0) or 0)
        pattern_arbitrage = pattern == "P5" and p3_median > 0 and float(listing["price_m2"]) <= p3_median * 1.15
        discount_neighborhood_opportunity = mean_m2 > 0 and discount_mean >= threshold
        discount_pattern_opportunity = pattern_mean_m2 > 0 and discount_pattern_mean >= threshold
        criteria = []
        if discount_neighborhood_opportunity:
            criteria.append("desconto_bairro")
        if discount_pattern_opportunity:
            criteria.append("desconto_bairro_padrao")
        if pattern_arbitrage:
            criteria.append("arbitragem_padrao")
        if criteria:
            opportunity_status = "confirmada" if evidence in {"confirmado", "provavel"} else "estimada"
        else:
            opportunity_status = "nao"
        record = listing.to_dict()
        record.update({
            "cep_original": original_cep,
            "cep_inference_method": cep_inference_method,
            "evidence_level": evidence,
            "match_reason": match_reason,
            "finish_pattern": pattern,
            "building_year": building_year,
            "classification_method": method,
            "classification_confidence": confidence,
            "classifier_version": classifier.version,
            "itbi_m2_neighborhood_mean": mean_m2,
            "itbi_m2_neighborhood_pattern_mean": pattern_mean_m2,
            "itbi_m2_neighborhood_median": median_m2,
            "itbi_neighborhood_sales_count": sales_count,
            "itbi_m2_neighborhood_pattern_median": pattern_median_m2,
            "discount_vs_neighborhood_mean": discount_mean,
            "discount_vs_neighborhood_pattern_mean": discount_pattern_mean,
            "discount_vs_mean": discount_mean,
            "discount_vs_median": discount_median,
            "opportunity_criteria": ",".join(criteria),
            "opportunity_status": opportunity_status,
        })
        output.append(record)
    analysis = pd.DataFrame(output)
    analysis["listing_m2_neighborhood_pattern_mean"] = analysis.groupby(
        ["neighborhood", "finish_pattern"]
    )["price_m2"].transform("mean")
    analysis["listing_m2_neighborhood_mean"] = analysis.groupby("neighborhood")["price_m2"].transform("mean")
    metric_columns = [
        "itbi_m2_neighborhood_mean",
        "itbi_m2_neighborhood_pattern_mean",
        "listing_m2_neighborhood_mean",
        "listing_m2_neighborhood_pattern_mean",
        "itbi_m2_neighborhood_median",
        "itbi_neighborhood_sales_count",
        "itbi_m2_neighborhood_pattern_median",
    ]
    leading_columns = [
        "source", "source_file", "collected_at", "listing_age_days", "requested_neighborhood",
        "cep", "cep_original", "cep_inference_method", "evidence_level", "match_reason",
        "classification_method", "classification_confidence", "classifier_version",
        "opportunity_criteria", "opportunity_status", "neighborhood_method", "title", "url",
        "neighborhood", "price", "area_m2", "price_m2", "bedrooms", "bathrooms",
        "parking_spaces", "condominium", "advertised_iptu", "street", "number",
        "finish_pattern", "building_year",
    ]
    remaining_columns = [column for column in analysis.columns if column not in leading_columns + metric_columns]
    analysis = analysis[leading_columns + metric_columns + remaining_columns]
    analysis.sort_values(
        by=["opportunity_status", "discount_vs_mean"], ascending=[True, False], inplace=True
    )
    path, manifest = write_dataset(
        analysis,
        paths / "analysis.csv",
        {
            "classifier": classifier.version,
            "classifier_mode": classifier_mode or settings.classifier,
            "model": classifier.metadata,
            "discount_threshold": threshold,
            "sources": [path.name for path in required],
            "m2_metrics": {
                "itbi": [
                    "itbi_m2_neighborhood_mean", "itbi_m2_neighborhood_pattern_mean",
                    "itbi_m2_neighborhood_median", "itbi_m2_neighborhood_pattern_median",
                ],
                "listings": ["listing_m2_neighborhood_mean", "listing_m2_neighborhood_pattern_mean"],
            },
        },
    )
    return StageResult(
        stage="analyze",
        artifacts=[str(path), str(manifest)],
        counts={
            "listings": len(analysis),
            "confirmed_opportunities": int((analysis["opportunity_status"] == "confirmada").sum()),
            "estimated_opportunities": int((analysis["opportunity_status"] == "estimada").sum()),
            "confirmed_matches": int((analysis["evidence_level"] == "confirmado").sum()),
        },
        details={
            "classifier": classifier.version,
            "discount_threshold": threshold,
            "model": classifier.metadata,
        },
    )
