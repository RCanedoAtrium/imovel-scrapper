from __future__ import annotations

import csv
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from crawlee_scraper import RAW_LISTING_COLUMNS, CrawleeScraper

from .config import Settings
from .errors import PipelineError
from .models import StageResult

REQUIRED_CARD_FIELDS = [field for field in RAW_LISTING_COLUMNS if field not in {"number", "location"}]


def missing_card_fields(item: dict[str, Any]) -> list[str]:
    """Returns required card fields that have no usable value."""
    return [
        field for field in REQUIRED_CARD_FIELDS
        if item.get(field) is None or not str(item.get(field)).strip()
    ]


def best_card_sample(items: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]] | None:
    """Selects a complete card, or the card closest to complete."""
    if not items:
        return None
    ranked = sorted(
        ((item, missing_card_fields(item)) for item in items),
        key=lambda candidate: (len(candidate[1]), -len(str(candidate[0].get("title", "")))),
    )
    return ranked[0]


def _search_url(scraper: CrawleeScraper, neighborhood: str, site: str, max_price: float) -> str:
    requests = scraper.generate_start_urls(
        [neighborhood],
        sites=[site],
        price_filters={neighborhood: {"max": max_price}},
    )
    return str(requests[0]["url"])


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Search-card validation",
        "",
        f"- Run: {report['run_id']}",
        f"- Neighborhood: {report['parameters']['neighborhood']}",
        f"- Price cap: R$ {report['parameters']['max_price']:,.0f}".replace(",", "."),
        f"- Pages per attempt: {report['parameters']['pages']}",
        f"- Maximum attempts: {report['parameters']['attempts']}",
        "",
    ]
    for portal in report["portals"]:
        lines.extend([
            f"## {portal['site']}",
            "",
            f"- Status: **{portal['status']}**",
            f"- Search: [{portal['search_url']}]({portal['search_url']})",
            f"- Attempts run: {len(portal['attempts'])}",
        ])
        if portal.get("sample"):
            sample = portal["sample"]
            lines.append(f"- Listing: [{sample['link']}]({sample['link']})")
            lines.append(f"- Missing fields: {', '.join(portal['missing_fields']) or 'none'}")
            lines.extend(["", "Collected data:", json.dumps(sample, ensure_ascii=False, indent=2)])
        else:
            lines.append(f"- Reason: {portal.get('reason', 'no listings returned')}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_samples(path: Path, portals: list[dict[str, Any]]) -> None:
    columns = ["site", "status", "search_url", "missing_fields", "attempt", *RAW_LISTING_COLUMNS]
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for portal in portals:
            sample = portal.get("sample") or {}
            writer.writerow({
                "site": portal["site"],
                "status": portal["status"],
                "search_url": portal["search_url"],
                "missing_fields": ", ".join(portal.get("missing_fields", [])),
                "attempt": portal.get("selected_attempt", ""),
                **{column: sample.get(column, "") for column in RAW_LISTING_COLUMNS},
            })


def validate_cards(
    settings: Settings,
    neighborhood: str = "BELVEDERE",
    max_price: float = 800_000,
    pages: int = 4,
    attempts: int = 3,
    sites: list[str] | None = None,
    scraper_factory: Callable[..., CrawleeScraper] = CrawleeScraper,
) -> StageResult:
    """Validates card extraction without changing the production listing pointer."""
    if max_price <= 0 or pages < 1 or attempts < 1:
        raise PipelineError("max_price, pages, and attempts must be greater than zero")
    chosen = [site.lower() for site in (sites or settings.sites)]
    run_id = f"card-validation-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    output_dir = settings.paths.diagnostics / "card-validation" / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    portals: list[dict[str, Any]] = []
    all_pagination: list[dict[str, Any]] = []

    for site in chosen:
        attempts_data: list[dict[str, Any]] = []
        selected: tuple[dict[str, Any], list[str]] | None = None
        selected_attempt: int | str = ""
        search_url = ""
        for attempt in range(1, attempts + 1):
            raw_path = output_dir / f"{site}_attempt_{attempt}.csv"
            scraper = scraper_factory(raw_data_path=str(raw_path))
            try:
                search_url = _search_url(scraper, neighborhood, site, max_price)
                items = scraper.run(
                    [neighborhood],
                    max_pages_per_bairro=pages,
                    min_pages_per_bairro=pages,
                    min_new_listings_per_page=1,
                    sites=[site],
                    price_filters={neighborhood: {"max": max_price}},
                ) or []
                items = [
                    item for item in items
                    if 0 < CrawleeScraper.extract_price(item.get("price")) <= max_price
                ]
                candidate = best_card_sample(items)
                attempt_data = {
                    "attempt": attempt,
                    "listings": len(items),
                    "missing_fields": candidate[1] if candidate else REQUIRED_CARD_FIELDS,
                }
                attempts_data.append(attempt_data)
                all_pagination.extend([
                    {"site": site, "attempt": attempt, **row}
                    for row in scraper.pagination_stats
                ])
                if candidate and not candidate[1]:
                    selected = candidate
                    selected_attempt = attempt
                    break
                if candidate and (selected is None or len(candidate[1]) < len(selected[1])):
                    selected = candidate
                    selected_attempt = attempt
            except Exception as exc:
                attempts_data.append({"attempt": attempt, "listings": 0, "error": str(exc)})

        sample, missing = selected if selected else ({}, REQUIRED_CARD_FIELDS)
        if sample and not missing:
            status, reason = "complete", ""
        elif sample:
            status, reason = "incomplete card", "fields missing after all attempts"
        elif any("error" in attempt_data for attempt_data in attempts_data):
            status, reason = "access failure", "no listings could be analyzed"
        else:
            status, reason = "incomplete card", "no listings returned"
        portals.append({
            "site": site,
            "status": status,
            "reason": reason,
            "search_url": search_url,
            "selected_attempt": selected_attempt,
            "missing_fields": missing,
            "sample": sample,
            "attempts": attempts_data,
        })

    report = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "parameters": {
            "neighborhood": neighborhood,
            "max_price": max_price,
            "pages": pages,
            "attempts": attempts,
            "sites": chosen,
        },
        "portals": portals,
    }
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path = output_dir / "report.md"
    _write_markdown(markdown_path, report)
    samples_path = output_dir / "samples.csv"
    _write_samples(samples_path, portals)
    pagination_path = output_dir / "pagination.json"
    pagination_path.write_text(json.dumps(all_pagination, ensure_ascii=False, indent=2), encoding="utf-8")

    complete = sum(portal["status"] == "complete" for portal in portals)
    return StageResult(
        stage="validate-cards",
        status="success" if complete == len(chosen) else "warning",
        artifacts=[str(markdown_path), str(report_path), str(samples_path), str(pagination_path)],
        counts={"portals": len(chosen), "complete": complete},
        warnings=[
            f"{portal['site']}: {portal['status']}" for portal in portals
            if portal["status"] != "complete"
        ],
        details={"run_id": run_id, "output_dir": str(output_dir)},
    )
