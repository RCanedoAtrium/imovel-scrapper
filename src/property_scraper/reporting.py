from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .config import Settings
from .errors import DataQualityError
from .models import StageResult
from .storage import read_csv_auto


def _read_manifest(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _quality_table(settings: Settings) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for name in ("transactions", "buildings", "listings", "neighborhood_stats", "analysis"):
        manifest = _read_manifest(settings.paths.processed / f"{name}.manifest.json")
        rows.append({
            "dataset": name,
            "rows": manifest.get("rows", 0),
            "generated_at": manifest.get("generated_at", ""),
            "sha256": manifest.get("sha256", ""),
            "source": manifest.get("source", ""),
        })
    return pd.DataFrame(rows)


def generate_report(settings: Settings, run_id: str = "") -> StageResult:
    analysis_path = settings.paths.processed / "analysis.csv"
    stats_path = settings.paths.processed / "neighborhood_stats.csv"
    if not analysis_path.exists() or not stats_path.exists():
        raise DataQualityError("Run analyze before generating the report")
    analysis = _round_for_report(read_csv_auto(analysis_path))
    stats = _round_for_report(
        read_csv_auto(stats_path).sort_values("itbi_m2_mean", ascending=False)
    )
    eligible = analysis[analysis["ranking_status"] == "parking_eligible"].copy()
    pending = analysis[analysis["ranking_status"] == "parking_pending_review"].copy()
    excluded_parking = analysis[analysis["ranking_status"] == "parking_excluded"].copy()
    quality = _quality_table(settings)
    execution = pd.DataFrame([{
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "latest_iptu_file": _latest_name(settings.paths.iptu),
        "latest_itbi_file": _latest_name(settings.paths.itbi),
        "discount_threshold": settings.discount_threshold,
        "classifier": settings.classifier,
        "sites": ", ".join(settings.sites),
        "neighborhoods": ", ".join(settings.neighborhoods),
        "max_price": settings.max_price,
        "max_pages": settings.pages_per_neighborhood,
        "min_pages": settings.min_pages_per_neighborhood,
        "min_new_per_page": settings.min_new_listings_per_page,
    }])
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = settings.paths.reports / f"bh_property_report_{timestamp}.xlsx"
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        sheets = {
            "Parking_Eligible": eligible,
            "Parking_Pending": pending,
            "Parking_Excluded": excluded_parking,
            "Neighborhood_Summary": stats,
            "All_Listings": analysis,
            "Data_Quality": quality,
            "Run": execution,
        }
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name[:31], index=False)
            _format_sheet(writer, name[:31], frame)
    latest = settings.paths.reports / "latest_report.txt"
    latest.write_text(output.name, encoding="utf-8")
    for previous in settings.paths.reports.glob("bh_property_report_*.xlsx"):
        if previous != output:
            previous.unlink()
    return StageResult(
        stage="report",
        artifacts=[str(output), str(latest)],
        counts={
            "eligible_parking_opportunities": len(eligible),
            "pending_parking_opportunities": len(pending),
            "excluded_parking_opportunities": len(excluded_parking),
            "all_listings": len(analysis),
        },
    )


def _latest_name(directory: Path) -> str:
    files = sorted(directory.glob("*.csv"), key=lambda path: path.name)
    return files[-1].name if files else ""


def _round_for_report(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    money_columns = {
        "price", "condominium", "advertised_iptu", "transaction_price",
        "itbi_m2_mean", "itbi_m2_median", "itbi_m2_p30", "itbi_m2_p80",
        "itbi_m2_neighborhood_mean", "itbi_m2_neighborhood_pattern_mean",
        "itbi_m2_neighborhood_median", "itbi_m2_neighborhood_pattern_median",
        "listing_m2_neighborhood_mean", "listing_m2_neighborhood_pattern_mean",
        "itbi_total_volume", "price_m2",
    }
    area_columns = {"area_m2", "median_area", "area_min", "area_max"}
    ratio_columns = {
        "discount_vs_mean", "discount_vs_median", "classification_confidence",
        "discount_vs_neighborhood_mean", "discount_vs_neighborhood_pattern_mean",
    }
    for column in result.columns.intersection(money_columns | area_columns):
        result[column] = pd.to_numeric(result[column], errors="coerce").round(2)
    for column in result.columns.intersection(ratio_columns):
        result[column] = pd.to_numeric(result[column], errors="coerce").round(4)
    return result


def _format_sheet(writer: pd.ExcelWriter, name: str, frame: pd.DataFrame) -> None:
    workbook = writer.book
    worksheet = writer.sheets[name]
    header = workbook.add_format({
        "bold": True, "font_color": "white", "bg_color": "#1B365D",
        "align": "center", "valign": "vcenter", "border": 1,
    })
    currency = workbook.add_format({"num_format": 'R$ #,##0.00'})
    price_m2 = workbook.add_format({"num_format": 'R$ #,##0.00"/m²"'})
    percent = workbook.add_format({"num_format": "0.0%"})
    for index, column in enumerate(frame.columns):
        worksheet.write(0, index, column, header)
        width = min(max(len(str(column)) + 2, 12), 38)
        if column == "title":
            width = 38
        elif column in {"url", "source_file"}:
            width = 28
        elif column in {"latest_iptu_file", "latest_itbi_file", "sites", "neighborhoods"}:
            width = 38
        elif column in {"sha256", "source"}:
            width = 24
        elif column in {"neighborhood", "requested_neighborhood", "generated_at", "collected_at"}:
            width = 22
        fmt = None
        if column in {
            "price", "condominium", "advertised_iptu", "itbi_m2_mean", "itbi_m2_median",
            "itbi_m2_neighborhood_mean", "itbi_m2_neighborhood_pattern_mean",
            "itbi_m2_neighborhood_median", "itbi_m2_neighborhood_pattern_median",
            "listing_m2_neighborhood_mean", "listing_m2_neighborhood_pattern_mean",
            "itbi_m2_p30", "itbi_m2_p80", "itbi_total_volume",
        }:
            fmt = currency
        elif column == "price_m2":
            fmt = price_m2
        elif column in {
            "discount_vs_mean", "discount_vs_median", "classification_confidence",
            "discount_vs_neighborhood_mean", "discount_vs_neighborhood_pattern_mean",
        }:
            fmt = percent
        worksheet.set_column(index, index, width, fmt)
    worksheet.freeze_panes(1, 0)
    worksheet.autofilter(0, 0, max(len(frame), 1), max(len(frame.columns) - 1, 0))
    if "opportunity_status" in frame.columns and len(frame):
        status_col = frame.columns.get_loc("opportunity_status")
        worksheet.conditional_format(1, status_col, len(frame), status_col, {
            "type": "text", "criteria": "containing", "value": "confirmed",
            "format": workbook.add_format({"bg_color": "#D4EDDA", "font_color": "#155724"}),
        })
