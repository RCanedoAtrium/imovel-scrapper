from imovel_scrapper.cli import build_parser


def test_all_public_commands_are_registered():
    parser = build_parser()
    for command in ("sync-pbh", "scrape", "validate-cards", "prepare", "analyze", "report", "run"):
        parsed = parser.parse_args([command])
        assert parsed.command == command


def test_scrape_price_filter_arguments():
    parsed = build_parser().parse_args(["scrape", "--max-price", "900000"])
    assert parsed.max_price == 900000


def test_scrape_incremental_arguments():
    parsed = build_parser().parse_args([
        "scrape", "--neighborhoods", "SERRA,SAVASSI", "--append-latest",
    ])
    assert parsed.neighborhoods == "SERRA,SAVASSI"
    assert parsed.append_latest is True


def test_run_can_reuse_recent_reference_and_scraper_data():
    parsed = build_parser().parse_args(["run", "--skip-reference-sync", "--skip-scrape"])
    assert parsed.skip_reference_sync is True
    assert parsed.skip_scrape is True
