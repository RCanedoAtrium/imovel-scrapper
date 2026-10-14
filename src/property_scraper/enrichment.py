from __future__ import annotations

import json
import re
from typing import Any, Callable

import pandas as pd

from .normalization import clean_numeric, extract_currency
from .parking import classify_parking


class DetailEnricher:
    """Enriches shortlisted listing candidate cards by parsing full listing detail page content."""

    @staticmethod
    def parse_detail_html(html: str, url: str = "", site: str = "") -> dict[str, Any]:
        """Extracts granular parking, area typology, dates, and carrying costs from page HTML."""
        if not html:
            return {}

        result: dict[str, Any] = {}

        # 1. Structured JSON-LD extraction
        json_ld_matches = re.findall(r'<script[^>]*type=[\'"]application/ld\+json[\'"][^>]*>(.*?)</script>', html, re.DOTALL | re.I)
        for match in json_ld_matches:
            try:
                data = json.loads(match.strip())
                if isinstance(data, list):
                    items = data
                else:
                    items = [data]
                for item in items:
                    if isinstance(item, dict):
                        if "datePosted" in item:
                            result["publication_date"] = str(item["datePosted"])
                        if "dateModified" in item:
                            result["updated_date"] = str(item["dateModified"])
                        if "floorSize" in item:
                            val = item["floorSize"]
                            if isinstance(val, dict) and "value" in val:
                                result["area_m2"] = clean_numeric(val["value"])
                            elif isinstance(val, (int, float, str)):
                                result["area_m2"] = clean_numeric(val)
            except Exception:
                pass

        # 2. Parking analysis
        parking_info = classify_parking(html)
        if parking_info.get("parking_review_status") == "approved":
            result["parking_review_status"] = "approved"
            result["parking_type"] = parking_info.get("parking_type", "")
            result["parking_confidence"] = parking_info.get("parking_confidence", 0.95)
            result["parking_evidence"] = parking_info.get("parking_evidence", "")
        elif parking_info.get("parking_review_status") == "rejected":
            result["parking_review_status"] = "rejected"
            result["parking_type"] = parking_info.get("parking_type", "")
            result["parking_confidence"] = parking_info.get("parking_confidence", 0.90)

        # 3. Area breakdown: útil vs total
        util_match = (
            re.search(r"(?:[aá]rea\s+)?(?:[úu]til|privativ\w*)\s*:?\s*(\d+(?:[.,]\d+)?)\s*m[²2]?", html, re.I)
            or re.search(r"(\d+(?:[.,]\d+)?)\s*m[²2]\s*(?:[úu]til|privativ\w*)", html, re.I)
        )
        if util_match:
            result["area_m2"] = clean_numeric(util_match.group(1))
            result["area_warning"] = "area_confirmed_by_detail"

        total_match = (
            re.search(r"(?:[aá]rea\s+)?(?:tot(?:al)?|terreno)\s*:?\s*(\d+(?:[.,]\d+)?)\s*m[²2]?", html, re.I)
            or re.search(r"(\d+(?:[.,]\d+)?)\s*m[²2]\s*(?:tot(?:al)?|terreno)", html, re.I)
        )
        if total_match:
            result["total_area_m2"] = clean_numeric(total_match.group(1))

        # 4. Carrying costs
        condo_match = re.search(r"condom[ií]nio\s*:?\s*R\$\s*([\d.]+(?:,\d{2})?)", html, re.I)
        if condo_match:
            result["condominium"] = extract_currency(condo_match.group(1))

        iptu_match = re.search(r"iptu\s*:?\s*R\$\s*([\d.]+(?:,\d{2})?)", html, re.I)
        if iptu_match:
            result["advertised_iptu"] = extract_currency(iptu_match.group(1))

        # 5. Publication dates in text
        if "publication_date" not in result:
            date_match = re.search(r"(?:publicado|criado|anunciado)\s*(?:em)?:?\s*(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})", html, re.I)
            if date_match:
                result["publication_date"] = date_match.group(1)

        return result

    @classmethod
    def enrich_shortlist(
        cls,
        listings: pd.DataFrame,
        max_items: int = 50,
        fetcher: Callable[[str], str] | None = None,
    ) -> pd.DataFrame:
        """Visits detail pages only for shortlisted listings to enrich parking, area, and costs."""
        if listings.empty or fetcher is None:
            return listings

        enriched = listings.copy()
        # Candidate filter: listings with pending parking review, unverified area, or qualifying discount
        mask = (
            enriched.get("ranking_status", pd.Series()).isin(["parking_pending_review", "parking_eligible"])
            | enriched.get("opportunity_status", pd.Series()).isin(["unverified_area", "confirmed", "estimated"])
        )
        candidates = enriched[mask].head(max_items)

        for idx, row in candidates.iterrows():
            url = str(row.get("url", "")).strip()
            if not url or not url.startswith("http"):
                continue
            try:
                html = fetcher(url)
                if not html:
                    continue
                detail = cls.parse_detail_html(html, url=url, site=str(row.get("source", "")))
                for key, val in detail.items():
                    if val is not None and val != "":
                        enriched.at[idx, key] = val
            except Exception:
                continue

        return enriched
