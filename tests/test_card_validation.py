import json
from pathlib import Path

from crawlee_scraper import RAW_LISTING_COLUMNS
from imovel_scrapper.card_validation import (
    best_card_sample,
    missing_card_fields,
    validate_cards,
)
from imovel_scrapper.config import load_settings


def _card(**overrides):
    item = {column: f"{column}-value" for column in RAW_LISTING_COLUMNS}
    item.update({
        "price": "R$ 800.000",
        "area": "50 m²",
        "link": "https://example.test/anuncio",
        "site": "fixture",
    })
    item.update(overrides)
    return item


def test_card_completeness_and_best_sample():
    partial = _card(bathrooms="")
    complete = _card(link="https://example.test/complete")
    assert missing_card_fields(partial) == ["bathrooms"]
    sample, missing = best_card_sample([partial, complete])
    assert sample["link"] == "https://example.test/complete"
    assert missing == []


def test_validation_writes_evidence_and_keeps_production_pointers(project):
    root, config_path = project
    settings = load_settings(config_path)
    latest = root / "data" / "listings" / "latest.txt"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text("production-run", encoding="utf-8")
    calls = []

    class FakeScraper:
        def __init__(self, raw_data_path):
            self.raw_data_path = raw_data_path
            self.pagination_stats = [{"page": 1, "raw_listings": 1}]

        def generate_start_urls(self, neighborhoods, sites, price_filters):
            return [{"url": f"https://example.test/search/{sites[0]}"}]

        def run(self, neighborhoods, **kwargs):
            site = kwargs["sites"][0]
            calls.append((site, kwargs))
            if site == "vivareal":
                return [_card(site="VivaReal")]
            return [_card(site="Zap Imóveis", bathrooms="")]

    result = validate_cards(
        settings,
        sites=["vivareal", "zap"],
        attempts=3,
        scraper_factory=FakeScraper,
    )

    assert result.status == "warning"
    assert result.counts == {"portals": 2, "complete": 1}
    assert latest.read_text(encoding="utf-8") == "production-run"
    assert len([call for call in calls if call[0] == "vivareal"]) == 1
    assert len([call for call in calls if call[0] == "zap"]) == 3
    assert all(call[1]["max_pages_per_bairro"] == 4 for call in calls)
    assert all(call[1]["min_pages_per_bairro"] == 4 for call in calls)

    markdown, report, samples, pagination = map(Path, result.artifacts)
    assert all(path.exists() for path in (markdown, report, samples, pagination))
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["portals"][0]["status"] == "completo"
    assert payload["portals"][1]["status"] == "sem card completo"
    assert payload["portals"][1]["missing_fields"] == ["bathrooms"]
