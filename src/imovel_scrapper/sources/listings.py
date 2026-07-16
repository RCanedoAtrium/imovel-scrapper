from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from crawlee_scraper import RAW_LISTING_COLUMNS

from ..config import Settings
from ..errors import SourceError
from ..models import StageResult
from ..normalization import extract_area_m2, extract_currency, normalize_neighborhood
from ..storage import read_csv_auto, write_csv


SUPPORTED_SITES = {"vivareal", "zap", "olx", "quintoandar", "imovelweb"}


class ListingCollector:
    """Executa cada portal isoladamente e aplica um contrato minimo comum."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def collect(
        self,
        sites: list[str] | None = None,
        pages_per_neighborhood: int | None = None,
        run_id: str | None = None,
        max_price: float | None = None,
        neighborhoods: list[str] | None = None,
        base_run_id: str | None = None,
    ) -> StageResult:
        from crawlee_scraper import CrawleeScraper

        chosen = [site.lower() for site in (sites or self.settings.sites)]
        invalid = sorted(set(chosen).difference(SUPPORTED_SITES))
        if invalid:
            raise SourceError(f"Portais nao suportados: {', '.join(invalid)}")
        run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_dir = self.settings.paths.listings / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        target_neighborhoods = neighborhoods or self.settings.neighborhoods
        base_dir = self.settings.paths.listings / base_run_id if base_run_id else None
        if base_dir is not None and not base_dir.is_dir():
            raise SourceError(f"Execução-base não encontrada: {base_run_id}")
        pages = pages_per_neighborhood or self.settings.pages_per_neighborhood
        ceiling = max_price if max_price is not None else self.settings.max_price
        filters = {
            neighborhood: {"max": ceiling, "min_area_m2": self.settings.min_area_m2}
            for neighborhood in target_neighborhoods
            if ceiling is not None or self.settings.min_area_m2 is not None
        }
        artifacts: list[str] = []
        counts: dict[str, int] = {}
        failures: list[str] = []

        combined = run_dir / "_combined.csv"
        try:
            # An incremental run may target only one portal.  Preserve every
            # other portal's previous CSV so the new run remains complete when
            # it becomes the `latest` run.
            if base_dir is not None:
                for site in self.settings.sites:
                    if site.lower() in chosen:
                        continue
                    previous = base_dir / f"{site.lower()}.csv"
                    if not previous.exists():
                        continue
                    output = run_dir / previous.name
                    shutil.copy2(previous, output)
                    artifacts.append(str(output))
                    counts[site.lower()] = len(read_csv_auto(previous))

            scraper = CrawleeScraper(raw_data_path=str(combined))
            items = scraper.run(
                target_neighborhoods,
                max_pages_per_bairro=pages,
                min_pages_per_bairro=min(self.settings.min_pages_per_neighborhood, pages),
                min_new_listings_per_page=self.settings.min_new_listings_per_page,
                sites=chosen,
                price_filters=filters,
            )
            contract_items = [item for item in (items or []) if self._is_valid_item(item)]
            rejected_by_price = 0
            for site in chosen:
                portal_items = [
                    item for item in contract_items if self._canonical_site(item.get("site")) == site
                ]
                if not portal_items:
                    failures.append(f"{site}: nenhum anuncio valido retornado")
                    continue
                site_items = [item for item in portal_items if self._matches_price_filter(item, filters)]
                rejected_by_price += sum(
                    not self._matches_price_filter(item, filters)
                    for item in portal_items
                )
                existing = pd.DataFrame(columns=RAW_LISTING_COLUMNS)
                if base_dir is not None:
                    previous = base_dir / f"{site}.csv"
                    if previous.exists():
                        existing = read_csv_auto(previous, dtype=str, low_memory=False).fillna("")
                merged = pd.concat(
                    [existing, pd.DataFrame(site_items, columns=RAW_LISTING_COLUMNS)],
                    ignore_index=True,
                )
                if "link" in merged:
                    merged = merged.drop_duplicates(subset=["link"], keep="last")
                output = run_dir / f"{site}.csv"
                write_csv(merged, output)
                artifacts.append(str(output))
                counts[site] = len(merged)
            pagination_path = run_dir / "pagination.json"
            previous_pagination = []
            if base_dir is not None:
                previous_pagination_path = base_dir / "pagination.json"
                if previous_pagination_path.exists():
                    previous_pagination = json.loads(previous_pagination_path.read_text(encoding="utf-8"))
            pagination_path.write_text(
                json.dumps([*previous_pagination, *scraper.pagination_stats], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            artifacts.append(str(pagination_path))
        except Exception as exc:
            failures.append(f"crawler: {exc}")
        finally:
            combined.unlink(missing_ok=True)

        if failures:
            diagnostic = self.settings.paths.diagnostics / f"scrape_{run_id}.txt"
            diagnostic.write_text("\n".join(failures), encoding="utf-8")
            shutil.rmtree(run_dir, ignore_errors=True)
            raise SourceError("Falha em portal selecionado; coleta descartada. " + " | ".join(failures))

        latest = self.settings.paths.listings / "latest.txt"
        latest.write_text(run_id, encoding="utf-8")
        return StageResult(
            stage="scrape",
            artifacts=artifacts,
            counts={"portals": len(chosen), "listings": sum(counts.values()), **counts},
            details={
                "run_id": run_id,
                "base_run_id": base_run_id,
                "neighborhoods_collected": target_neighborhoods,
                "pages_per_neighborhood": pages,
                "min_pages_per_neighborhood": min(self.settings.min_pages_per_neighborhood, pages),
                "min_new_listings_per_page": self.settings.min_new_listings_per_page,
                "pages_crawled": len(scraper.pagination_stats),
                "queries_crawled": len({
                    (row["site"], row["neighborhood"]) for row in scraper.pagination_stats
                }),
                "max_price": ceiling,
                "rejected_by_price": rejected_by_price,
            },
        )

    @staticmethod
    def _is_valid_item(item: object) -> bool:
        if not isinstance(item, dict):
            return False
        link = str(item.get("url") or item.get("link") or "")
        price = str(item.get("price") or item.get("priceValue") or "")
        area = str(item.get("area") or item.get("usableAreaM2") or "")
        return link.startswith("http") and bool(re.search(r"\d", price)) and bool(re.search(r"\d", area))

    @staticmethod
    def _canonical_site(value: object) -> str:
        normalized = re.sub(r"[^a-z]", "", str(value or "").lower())
        if "vivareal" in normalized:
            return "vivareal"
        if normalized.startswith("zap"):
            return "zap"
        if "quintoandar" in normalized:
            return "quintoandar"
        if "imovelweb" in normalized:
            return "imovelweb"
        if "olx" in normalized:
            return "olx"
        return normalized

    @staticmethod
    def _matches_price_filter(item: dict, filters: dict[str, dict[str, float]]) -> bool:
        neighborhood = normalize_neighborhood(item.get("neighborhood"))
        normalized_filters = {normalize_neighborhood(name): limits for name, limits in filters.items()}
        limits = normalized_filters.get(neighborhood)
        if not limits:
            return True
        price = extract_currency(item.get("price"))
        minimum = float(limits.get("min", 0) or 0)
        maximum = float(limits.get("max", 0) or 0)
        minimum_area = float(limits.get("min_area_m2", 0) or 0)
        area = extract_area_m2(item.get("area") or item.get("usableAreaM2"))
        return (
            price > 0
            and (not minimum or price >= minimum)
            and (not maximum or price <= maximum)
            and (not minimum_area or area >= minimum_area)
        )
