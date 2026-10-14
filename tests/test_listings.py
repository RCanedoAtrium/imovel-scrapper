import pandas as pd
import pytest

from crawlee_scraper import RAW_LISTING_COLUMNS
from property_scraper.config import load_settings
from property_scraper.errors import SourceError
from property_scraper.sources.listings import ListingCollector


class MockCrawler:
    def __init__(self, items=None, pagination_stats=None, raise_exc=None, **kwargs):
        self._items = items or []
        self.pagination_stats = pagination_stats or [{"site": "vivareal", "neighborhood": "SAVASSI"}]
        self._raise_exc = raise_exc

    def run(self, neighborhoods, **kwargs):
        if self._raise_exc:
            raise self._raise_exc
        return self._items


def _make_listing(site, link, price="R$ 500.000", area="60 m²"):
    return {column: f"{column}-val" for column in RAW_LISTING_COLUMNS} | {
        "site": site,
        "link": link,
        "url": link,
        "price": price,
        "area": area,
        "neighborhood": "SAVASSI",
    }


def test_collector_partial_portal_failure_retains_success(project):
    root, config_path = project
    settings = load_settings(config_path)

    items = [
        _make_listing("vivareal", "https://example.test/vr1"),
        _make_listing("vivareal", "https://example.test/vr2"),
    ]

    collector = ListingCollector(settings)
    result = collector.collect(
        neighborhoods=["SAVASSI"],
        sites=["vivareal", "imovelweb"],
        run_id="run_partial_test",
        scraper_factory=lambda *args, **kwargs: MockCrawler(items=items),
    )

    assert result.status == "warning"
    assert result.counts["vivareal"] == 2
    assert result.details["portal_status"]["vivareal"] == "success"
    assert result.details["portal_status"]["imovelweb"] == "failed_no_listings"
    assert any("imovelweb" in warn for warn in result.warnings)

    run_dir = settings.paths.listings / "run_partial_test"
    assert (run_dir / "vivareal.csv").exists()
    assert (settings.paths.listings / "latest.txt").read_text(encoding="utf-8") == "run_partial_test"
    assert (settings.paths.diagnostics / "scrape_run_partial_test.txt").exists()


def test_collector_carries_over_prior_csv_when_portal_fails(project):
    root, config_path = project
    settings = load_settings(config_path)

    base_dir = settings.paths.listings / "base_run"
    base_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            _make_listing("imovelweb", "https://example.test/iw1"),
            _make_listing("imovelweb", "https://example.test/iw2"),
            _make_listing("imovelweb", "https://example.test/iw3"),
        ]
    ).to_csv(base_dir / "imovelweb.csv", index=False, sep=";", encoding="utf-8-sig")

    items = [
        _make_listing("vivareal", "https://example.test/vr1"),
    ]

    collector = ListingCollector(settings)
    result = collector.collect(
        neighborhoods=["SAVASSI"],
        sites=["vivareal", "imovelweb"],
        run_id="run_carry_over",
        base_run_id="base_run",
        scraper_factory=lambda *args, **kwargs: MockCrawler(items=items),
    )

    assert result.status == "warning"
    assert result.counts["vivareal"] == 1
    assert result.counts["imovelweb"] == 3
    assert result.details["portal_status"]["imovelweb"] == "carried_over_no_new_listings"

    run_dir = settings.paths.listings / "run_carry_over"
    assert (run_dir / "imovelweb.csv").exists()
    iw_df = pd.read_csv(run_dir / "imovelweb.csv", sep=";")
    assert len(iw_df) == 3


def test_collector_total_failure_discards_run_and_raises_source_error(project):
    root, config_path = project
    settings = load_settings(config_path)

    collector = ListingCollector(settings)
    with pytest.raises(SourceError, match="All selected portals failed"):
        collector.collect(
            neighborhoods=["SAVASSI"],
            sites=["vivareal", "imovelweb"],
            run_id="run_fail_test",
            scraper_factory=lambda *args, **kwargs: MockCrawler(items=[]),
        )

    run_dir = settings.paths.listings / "run_fail_test"
    assert not run_dir.exists()


def test_collector_saves_failed_pages_and_retry_stats(project):
    import json

    root, config_path = project
    settings = load_settings(config_path)

    items = [_make_listing("vivareal", "https://example.test/vr1")]
    failed_mock = [{"url": "https://example.test/failed1", "site": "imovelweb", "neighborhood": "SAVASSI"}]
    retry_stats_mock = {"initial_failures": 1, "recovered": 0, "permanent_failures": 1}

    class MockCrawlerWithFailures(MockCrawler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.failed_pages = failed_mock
            self.retry_stats = retry_stats_mock

    collector = ListingCollector(settings)
    result = collector.collect(
        neighborhoods=["SAVASSI"],
        sites=["vivareal"],
        run_id="run_with_failures",
        scraper_factory=lambda *args, **kwargs: MockCrawlerWithFailures(items=items),
    )

    run_dir = settings.paths.listings / "run_with_failures"
    failed_file = run_dir / "failed_pages.json"
    assert failed_file.exists()
    data = json.loads(failed_file.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["site"] == "imovelweb"
    assert result.details["failed_pages_count"] == 1
    assert result.details["retry_stats"]["permanent_failures"] == 1


def test_collector_retry_failed_from_previous_run(project):
    import json

    root, config_path = project
    settings = load_settings(config_path)

    # Setup a previous run with failed_pages.json
    prev_dir = settings.paths.listings / "prev_run_id"
    prev_dir.mkdir(parents=True, exist_ok=True)
    (prev_dir / "failed_pages.json").write_text(
        json.dumps([{"url": "https://example.test/fail", "site": "vivareal", "neighborhood": "SAVASSI"}]),
        encoding="utf-8",
    )
    (settings.paths.listings / "latest.txt").write_text("prev_run_id", encoding="utf-8")

    items = [_make_listing("vivareal", "https://example.test/vr_retry")]
    collector = ListingCollector(settings)
    result = collector.collect(
        run_id="run_retry_success",
        retry_failed=True,
        scraper_factory=lambda *args, **kwargs: MockCrawler(items=items),
    )

    assert result.status == "success"
    assert result.counts["vivareal"] == 1
    assert result.details["base_run_id"] == "prev_run_id"
    assert "SAVASSI" in result.details["neighborhoods_collected"]
