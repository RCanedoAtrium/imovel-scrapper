from __future__ import annotations

import pandas as pd

from .config import Settings
from .errors import DataQualityError
from .models import StageResult
from .normalization import normalize_neighborhood, normalize_number, normalize_street
from .pattern_model import VALID_PATTERNS, RandomForestPatternModel
from .storage import read_csv_auto, write_dataset

# Area warnings that need IPTU confirmation before a listing can be ranked.
DOUBTFUL_AREA_WARNINGS = frozenset({"area_mismatch_warning", "area_total_only"})


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
        "street_number": _unique_cep_map(references, ["street", "number"]),
        "street_neighborhood": _unique_cep_map(references, ["street", "neighborhood"]),
        "street": _unique_cep_map(references, ["street"]),
    }


def infer_listing_cep(listing: pd.Series, lookup: dict[str, dict[tuple[str, ...], str]]) -> tuple[str, str]:
    """Return an existing or uniquely inferred CEP and its audit method."""
    cep = "".join(char for char in str(listing.get("cep", "") or "") if char.isdigit())
    if len(cep) == 8:
        return cep, "listing"
    street = normalize_street(listing.get("street", ""))
    number = normalize_number(listing.get("number", ""))
    neighborhood = normalize_neighborhood(listing.get("neighborhood", ""))
    for method, key in (
        ("street_number", (street, number)),
        ("street_neighborhood", (street, neighborhood)),
        ("street", (street,)),
    ):
        if all(key) and key in lookup[method]:
            return lookup[method][key], method
    return "", "not_inferred"


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
            return candidates.iloc[0], "confirmed", "cep+unique_number"
        chosen = _choose_by_area(candidates, area)
        if chosen is not None:
            return chosen, "likely", "cep+ambiguous_number_resolved_by_area"

    if street and number:
        candidates = buildings[(buildings["street"] == street) & (buildings["number"] == number)]
        if neighborhood:
            same_neighborhood = candidates[candidates["neighborhood"] == neighborhood]
            if not same_neighborhood.empty:
                candidates = same_neighborhood
        chosen = _choose_by_area(candidates, area)
        if chosen is not None:
            return chosen, "likely", "street+number"

    if street and neighborhood:
        candidates = buildings[
            (buildings["street"] == street) & (buildings["neighborhood"] == neighborhood)
        ]
        compatible = candidates[(candidates["area_min"] <= area) & (candidates["area_max"] >= area)]
        if len(compatible) == 1:
            return compatible.iloc[0], "likely", "street+neighborhood+area"

    return None, "no_match", "insufficient_data_or_no_match"


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
            return "P3", 0.40, "heuristic_without_reference"
        if price_m2 <= reference["itbi_m2_p30"]:
            pattern = "P3"
        elif price_m2 <= reference["itbi_m2_p80"]:
            pattern = "P4"
        else:
            pattern = "P5" if area >= reference["median_area"] * 0.8 else "P4"
        return pattern, 0.70, "itbi_heuristic"

    def classify_many(self, listings: pd.DataFrame) -> list[tuple[str, float, str]] | None:
        if self.model is None:
            return None
        return [
            (prediction.pattern, prediction.confidence, prediction.method)
            for prediction in self.model.predict_batch(listings)
        ]


def deduplicate_physical_properties(df: pd.DataFrame) -> pd.DataFrame:
    """Group duplicate listings of the same physical unit across brokers and portals.

    Election rule:
    - The most recent listing (by collected_at / collected_date) is elected canonical.
    - Metadata added: duplicate_count, min_advertised_price, alternate_urls.
    - Missing street/parking information is enriched from other brokers in the cluster.
    - The lowest advertised price qualifies the physical property if it meets the criteria.
    """
    if df.empty:
        return df

    records = df.to_dict("records")
    records.sort(
        key=lambda r: (str(r.get("collected_at", "")), str(r.get("collected_date", ""))),
        reverse=True,
    )

    by_neigh: dict[str, list[dict[str, object]]] = {}
    for item in records:
        neigh = str(item.get("neighborhood", "")).strip().upper()
        by_neigh.setdefault(neigh, []).append(item)

    clusters: list[list[dict[str, object]]] = []
    for _, neigh_records in by_neigh.items():
        neigh_clusters: list[list[dict[str, object]]] = []
        for item in neigh_records:
            street = str(item.get("street", "")).strip().upper()
            bedrooms = float(item.get("bedrooms", 0) or 0)
            area = float(item.get("area_m2", 0) or 0)
            price = float(item.get("price", 0) or 0)

            matched_cluster = None
            for cluster in neigh_clusters:
                rep = cluster[0]
                r_street = str(rep.get("street", "")).strip().upper()
                r_bedrooms = float(rep.get("bedrooms", 0) or 0)
                r_area = float(rep.get("area_m2", 0) or 0)
                r_price = float(rep.get("price", 0) or 0)

                if street and r_street and street != r_street:
                    continue

                # Bedrooms: allow +- 1 difference (common divergence when counting DCE/closet/reversivel)
                if bedrooms > 0 and r_bedrooms > 0 and abs(bedrooms - r_bedrooms) > 1:
                    continue

                max_area = max(area, r_area)
                if max_area > 0 and abs(area - r_area) / max_area > 0.06:
                    continue

                max_price = max(price, r_price)
                if max_price > 0 and abs(price - r_price) / max_price > 0.08:
                    continue

                if not street and not r_street:
                    if max_area > 0 and abs(area - r_area) / max_area > 0.03:
                        continue
                    if max_price > 0 and abs(price - r_price) / max_price > 0.03:
                        continue

                matched_cluster = cluster
                break

            if matched_cluster is not None:
                matched_cluster.append(item)
            else:
                neigh_clusters.append([item])
        clusters.extend(neigh_clusters)

    output_rows: list[dict[str, object]] = []
    for cluster in clusters:
        rep = dict(cluster[0])

        # Distinct concurrent ads (separate broker URLs or source/title/price keys).
        # Snapshots of the exact same ad over multiple dates do not count as duplicates.
        unique_ads: set[object] = set()
        for it in cluster:
            u = str(it.get("url", "")).strip()
            if u:
                unique_ads.add(u)
            else:
                unique_ads.add((str(it.get("source", "")), str(it.get("title", "")), float(it.get("price", 0) or 0)))
        duplicate_count = max(1, len(unique_ads))

        if not rep.get("street"):
            for it in cluster:
                if it.get("street"):
                    rep["street"] = it["street"]
                    if it.get("number"):
                        rep["number"] = it["number"]
                    break

        if rep.get("parking_review_status") != "approved":
            for it in cluster:
                if it.get("parking_review_status") == "approved":
                    rep["parking_review_status"] = "approved"
                    rep["parking_type"] = it.get("parking_type", rep.get("parking_type"))
                    rep["parking_evidence"] = it.get("parking_evidence", rep.get("parking_evidence"))
                    rep["parking_confidence"] = it.get("parking_confidence", rep.get("parking_confidence"))
                    if float(rep.get("parking_spaces", 0) or 0) <= 0 and float(it.get("parking_spaces", 0) or 0) > 0:
                        rep["parking_spaces"] = it.get("parking_spaces")
                    break

        prices = [float(it.get("price", 0) or 0) for it in cluster if float(it.get("price", 0) or 0) > 0]
        min_price = min(prices) if prices else float(rep.get("price", 0) or 0)
        rep_url = str(rep.get("url", "")).strip()
        seen_urls: list[str] = []
        for it in cluster:
            u = str(it.get("url", "")).strip()
            if u and u != rep_url and u not in seen_urls:
                seen_urls.append(u)

        rep["duplicate_count"] = duplicate_count
        rep["min_advertised_price"] = min_price
        rep["alternate_urls"] = ", ".join(seen_urls)

        # Re-evaluate discount if a competing broker advertised a lower price
        area = float(rep.get("area_m2", 0) or 0)
        conservative_ref = float(rep.get("conservative_ref_m2", 0) or 0)
        if min_price > 0 and min_price < float(rep.get("price", 0) or 0) and area > 0 and conservative_ref > 0:
            effective_price_m2 = min_price / area
            effective_discount = (conservative_ref - effective_price_m2) / conservative_ref
            if effective_discount > float(rep.get("discount_vs_conservative_ref", 0) or 0):
                rep["discount_vs_conservative_ref"] = effective_discount
                mean_m2 = float(rep.get("itbi_m2_neighborhood_mean", 0) or 0)
                if mean_m2 > 0:
                    rep["discount_vs_neighborhood_mean"] = (mean_m2 - effective_price_m2) / mean_m2
                    rep["discount_vs_mean"] = rep["discount_vs_neighborhood_mean"]
                pat_mean_m2 = float(rep.get("itbi_m2_neighborhood_pattern_mean", 0) or 0)
                if pat_mean_m2 > 0:
                    rep["discount_vs_neighborhood_pattern_mean"] = (pat_mean_m2 - effective_price_m2) / pat_mean_m2

        # Check if any broker ad qualified the physical property
        if rep.get("opportunity_status") not in {"confirmed", "estimated"}:
            for it in cluster:
                if it.get("opportunity_status") in {"confirmed", "estimated"}:
                    rep["opportunity_status"] = it.get("opportunity_status")
                    if "conservative_discount" not in str(rep.get("opportunity_criteria", "")):
                        criteria = [c for c in str(rep.get("opportunity_criteria", "")).split(",") if c]
                        criteria.insert(0, "conservative_discount")
                        rep["opportunity_criteria"] = ",".join(criteria)
                    break

        # Re-evaluate ranking status with final opportunity status & parking
        opp_status = rep.get("opportunity_status")
        review_status = str(rep.get("parking_review_status", "pending_review"))
        has_parking = float(rep.get("parking_spaces", 0) or 0) >= 1 or review_status == "approved"
        if opp_status not in {"confirmed", "estimated"}:
            rep["ranking_status"] = "not_qualified"
            rep["ranking_priority"] = 0
        elif review_status == "rejected":
            rep["ranking_status"] = "parking_excluded"
            rep["ranking_priority"] = 1
            rep["parking_exclusion_reason"] = str(rep.get("parking_type", "") or "rejected")
        elif not has_parking:
            rep["ranking_status"] = "parking_excluded"
            rep["ranking_priority"] = 1
            rep["parking_exclusion_reason"] = "no_parking_space"
        elif review_status == "approved":
            rep["ranking_status"] = "parking_eligible"
            rep["ranking_priority"] = 3
            rep["parking_exclusion_reason"] = ""
        else:
            rep["ranking_status"] = "parking_pending_review"
            rep["ranking_priority"] = 2
            rep["parking_exclusion_reason"] = ""

        output_rows.append(rep)

    return pd.DataFrame(output_rows)


def analyze(settings: Settings, classifier_mode: str | None = None, discount_threshold: float | None = None) -> StageResult:
    paths = settings.paths.processed
    required = [
        paths / "transactions.csv", paths / "neighborhood_stats.csv",
        paths / "buildings.csv", paths / "listings.csv",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise DataQualityError("Prepared datasets are missing: " + ", ".join(missing))
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
        area_warning = str(listing.get("area_warning", "") or "")
        area_val = float(listing.get("area_m2", 0) or 0)
        if matched is not None and str(matched.get("finish_pattern", "")) in VALID_PATTERNS:
            pattern = str(matched["finish_pattern"])
            confidence = 1.0 if evidence == "confirmed" else 0.85
            method = "iptu_deterministic"
            building_year = int(matched.get("building_year", 0) or 0)
        else:
            if batch_predictions is not None:
                pattern, confidence, method = batch_predictions[position]
            else:
                pattern, confidence, method = classifier.classify(
                    str(listing["neighborhood"]), str(listing["street"]),
                    float(listing["area_m2"]), float(listing["price_m2"]),
                )
            evidence = "estimated"

        # The street+neighborhood+area match is chosen *by* area, so it cannot
        # confirm the area. Only address matches (CEP/street + number) can.
        address_match = matched is not None and match_reason != "street+neighborhood+area"
        area_in_iptu_range = False
        if matched is not None:
            b_min = float(matched.get("area_min", 0) or 0)
            b_max = float(matched.get("area_max", 0) or 0)
            if b_min > 0 and b_max > 0:
                area_in_iptu_range = b_min <= area_val <= b_max
                if not area_in_iptu_range and area_val > b_max * 1.5:
                    area_warning = "area_mismatch_warning"
        if area_warning in DOUBTFUL_AREA_WARNINGS:
            if address_match and area_in_iptu_range:
                area_warning = "area_confirmed_by_iptu"
            elif area_warning == "area_total_only":
                area_warning = "area_unverified"

        neighborhood = str(listing["neighborhood"])
        reference = stats_index.get(neighborhood, {})
        mean_m2 = float(reference.get("itbi_m2_mean", 0) or 0)
        median_m2 = float(reference.get("itbi_m2_median", 0) or 0)
        sales_count = int(reference.get("itbi_sales_count", 0) or 0)
        pattern_reference = pattern_stats.get((neighborhood, pattern), {})
        pattern_mean_m2 = float(pattern_reference.get("mean", 0) or 0)
        pattern_median_m2 = float(pattern_reference.get("median", 0) or 0)
        discount_mean = (mean_m2 - float(listing["price_m2"])) / mean_m2 if mean_m2 else 0.0
        discount_pattern_mean = (
            (pattern_mean_m2 - float(listing["price_m2"])) / pattern_mean_m2
            if pattern_mean_m2 else 0.0
        )
        discount_median = (median_m2 - float(listing["price_m2"])) / median_m2 if median_m2 else 0.0
        p3_median = float(p3_medians.get(neighborhood, 0) or 0)
        pattern_arbitrage = pattern == "P5" and p3_median > 0 and float(listing["price_m2"]) <= p3_median * 1.15
        if pattern_mean_m2 > 0 and mean_m2 > 0:
            conservative_ref_m2 = min(pattern_mean_m2, mean_m2)
        else:
            conservative_ref_m2 = pattern_mean_m2 or mean_m2

        discount_conservative = (
            (conservative_ref_m2 - float(listing["price_m2"])) / conservative_ref_m2
            if conservative_ref_m2 else 0.0
        )
        # GEMINI.md §4: only the conservative reference qualifies a listing.
        # The remaining criteria are kept as explanatory tags.
        qualifies = conservative_ref_m2 > 0 and discount_conservative >= threshold
        criteria = []
        if qualifies:
            criteria.append("conservative_discount")
        if mean_m2 > 0 and discount_mean >= threshold:
            criteria.append("neighborhood_discount")
        if pattern_mean_m2 > 0 and discount_pattern_mean >= threshold:
            criteria.append("neighborhood_pattern_discount")
        if pattern_arbitrage:
            criteria.append("finish_pattern_arbitrage")

        has_unverified_area = area_warning in DOUBTFUL_AREA_WARNINGS | {"area_unverified"}
        if qualifies and not has_unverified_area:
            opportunity_status = "confirmed" if evidence in {"confirmed", "likely"} else "estimated"
        elif qualifies and has_unverified_area:
            opportunity_status = "unverified_area"
        else:
            opportunity_status = "not_qualified"

        # GEMINI.md §4: the main ranking requires at least one garage space,
        # either from the structured count or from explicit text evidence.
        parking_review_status = str(listing.get("parking_review_status", "pending_review"))
        has_parking_space = (
            float(listing.get("parking_spaces", 0) or 0) >= 1 or parking_review_status == "approved"
        )
        parking_exclusion_reason = ""
        if opportunity_status not in {"confirmed", "estimated"}:
            ranking_status = "not_qualified"
            ranking_priority = 0
        elif parking_review_status == "rejected":
            ranking_status = "parking_excluded"
            ranking_priority = 1
            parking_exclusion_reason = str(listing.get("parking_type", "") or "rejected")
        elif not has_parking_space:
            ranking_status = "parking_excluded"
            ranking_priority = 1
            parking_exclusion_reason = "no_parking_space"
        elif parking_review_status == "approved":
            ranking_status = "parking_eligible"
            ranking_priority = 3
        else:
            ranking_status = "parking_pending_review"
            ranking_priority = 2
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
            "conservative_ref_m2": conservative_ref_m2,
            "discount_vs_neighborhood_mean": discount_mean,
            "discount_vs_neighborhood_pattern_mean": discount_pattern_mean,
            "discount_vs_conservative_ref": discount_conservative,
            "discount_vs_mean": discount_mean,
            "discount_vs_median": discount_median,
            "opportunity_criteria": ",".join(criteria),
            "opportunity_status": opportunity_status,
            "ranking_status": ranking_status,
            "ranking_priority": ranking_priority,
            "parking_exclusion_reason": parking_exclusion_reason,
            "area_warning": area_warning,
        })
        output.append(record)
    raw_analysis = pd.DataFrame(output)
    analysis = deduplicate_physical_properties(raw_analysis)
    analysis["listing_m2_neighborhood_pattern_mean"] = analysis.groupby(
        ["neighborhood", "finish_pattern"]
    )["price_m2"].transform("mean")
    analysis["listing_m2_neighborhood_mean"] = analysis.groupby("neighborhood")["price_m2"].transform("mean")
    metric_columns = [
        "itbi_m2_neighborhood_mean",
        "itbi_m2_neighborhood_pattern_mean",
        "conservative_ref_m2",
        "listing_m2_neighborhood_mean",
        "listing_m2_neighborhood_pattern_mean",
        "itbi_m2_neighborhood_median",
        "itbi_neighborhood_sales_count",
        "itbi_m2_neighborhood_pattern_median",
    ]
    leading_columns = [
        "source", "source_file", "collected_at", "collected_date", "listing_age_days", "requested_neighborhood",
        "cep", "cep_original", "cep_inference_method", "evidence_level", "match_reason",
        "classification_method", "classification_confidence", "classifier_version",
        "opportunity_criteria", "opportunity_status", "ranking_status", "ranking_priority",
        "duplicate_count", "min_advertised_price", "alternate_urls", "area_warning",
        "neighborhood_method", "title", "url",
        "neighborhood", "price", "area_m2", "price_m2", "bedrooms", "bathrooms",
        "parking_spaces", "parking_type", "parking_evidence", "parking_confidence",
        "parking_review_status", "condominium", "advertised_iptu", "street", "number",
        "finish_pattern", "building_year",
    ]
    remaining_columns = [column for column in analysis.columns if column not in leading_columns + metric_columns]
    analysis = analysis[leading_columns + metric_columns + remaining_columns]
    analysis.sort_values(
        by=["ranking_priority", "discount_vs_conservative_ref"], ascending=[False, False], inplace=True
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
    duplicate_merged = int((analysis["duplicate_count"] - 1).sum()) if "duplicate_count" in analysis else 0
    return StageResult(
        stage="analyze",
        artifacts=[str(path), str(manifest)],
        counts={
            "listings_input": len(raw_analysis),
            "physical_properties": len(analysis),
            "duplicate_listings_merged": duplicate_merged,
            "confirmed_opportunities": int((analysis["opportunity_status"] == "confirmed").sum()),
            "estimated_opportunities": int((analysis["opportunity_status"] == "estimated").sum()),
            "unverified_area_listings": int((analysis["opportunity_status"] == "unverified_area").sum()),
            "confirmed_matches": int((analysis["evidence_level"] == "confirmed").sum()),
        },
        details={
            "classifier": classifier.version,
            "discount_threshold": threshold,
            "model": classifier.metadata,
        },
    )

