import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from crawlee_scraper import RAW_LISTING_COLUMNS, CrawleeScraper
from property_scraper.analysis import analyze
from property_scraper.config import load_settings
from property_scraper.processing import prepare_all, prepare_listings
from property_scraper.reporting import generate_report
from property_scraper.sources.listings import ListingCollector


def _write_sources(root: Path):
    itbi_dir = root / "data" / "itbi"
    iptu_dir = root / "data" / "iptu"
    listing_dir = root / "data" / "listings" / "fixture"
    for directory in (itbi_dir, iptu_dir, listing_dir):
        directory.mkdir(parents=True, exist_ok=True)
    rows = []
    for index in range(12):
        rows.append({
            "Endereco Completo": "Rua Ptolomeu 130 - 30360250",
            "Bairro": "ANCHIETA",
            "Ano de Construcao (Unidade)": "2000",
            "Area Construida Adquirida": "50,00",
            "Padrao Acabamento (Unidade)": "P3" if index < 6 else "P4",
            "Descrição Tipo Ocupacao (Unidade)": "RESIDENCIAL",
            "Valor Declarado": str(250_000 + index * 1_000),
            "Valor Base Calculo": str(240_000 + index * 1_000),
        })
    pd.DataFrame(rows).to_csv(itbi_dir / "pda_itbi_relatorio_202601.csv", sep=";", index=False)
    pd.DataFrame([{
        "ID_IPTU_CTM": "1", "AREA_CONSTRUCAO": "60.0", "TIPO_CONSTRUTIVO": "APARTAMENTO",
        "PADRAO_ACABAMENTO": "P3", "NOME_LOGRADOURO": "PTOLOMEU", "NUMERO_IMOVEL": "130",
        "CEP": "30360250",
    }]).to_csv(iptu_dir / "20260101_regional_centro_sul_cadastro_imobiliario.csv", index=False)
    pd.DataFrame([{
        "title": "Apartamento à venda em Rua Ptolomeu 130, Anchieta, Belo Horizonte 30360-250 com 1 vaga livre",
        "price": "R$ 100.000", "area": "60 m²", "link": "https://example.test/1",
        "neighborhood": "ANCHIETA", "site": "Fixture",
    }]).to_csv(listing_dir / "fixture.csv", index=False)
    (root / "data" / "listings" / "latest.txt").write_text("fixture", encoding="utf-8")


def test_pipeline_without_network(project):
    root, config_path = project
    _write_sources(root)
    settings = load_settings(config_path)
    prepared = prepare_all(settings)
    assert prepared.counts["transactions"] == 12
    assert prepared.counts["buildings"] == 1
    analyzed = analyze(settings, classifier_mode="heuristics")
    assert analyzed.counts["confirmed_opportunities"] == 1
    analysis = pd.read_csv(
        root / "data" / "processed" / "analysis.csv", sep=";", decimal=",", encoding="utf-8-sig"
    )
    record = analysis.iloc[0]
    assert record["itbi_m2_neighborhood_mean"] == 5110
    assert record["itbi_m2_neighborhood_pattern_mean"] == 5050
    assert record["listing_m2_neighborhood_mean"] == record["price_m2"]
    assert record["listing_m2_neighborhood_pattern_mean"] == record["price_m2"]
    assert record["itbi_m2_neighborhood_median"] == 5110
    assert record["itbi_neighborhood_sales_count"] == 12
    assert record["itbi_m2_neighborhood_pattern_median"] == 5050
    assert record["parking_type"] == "independent"
    assert record["ranking_status"] == "parking_eligible"
    expected_leading_columns = [
        "source", "source_file", "collected_at", "collected_date", "listing_age_days", "requested_neighborhood",
        "cep", "cep_original", "cep_inference_method", "evidence_level", "match_reason",
        "classification_method", "classification_confidence", "classifier_version",
        "opportunity_criteria", "opportunity_status", "ranking_status", "ranking_priority",
        "duplicate_count", "min_advertised_price", "alternate_urls", "area_warning",
        "neighborhood_method", "title", "url",
        "neighborhood", "price", "area_m2", "price_m2", "bedrooms", "bathrooms",
        "parking_spaces", "parking_type", "parking_evidence", "parking_confidence",
        "parking_review_status", "condominium", "advertised_iptu", "street", "number",
        "finish_pattern", "building_year", "itbi_m2_neighborhood_mean",
        "itbi_m2_neighborhood_pattern_mean", "conservative_ref_m2", "listing_m2_neighborhood_mean",
        "listing_m2_neighborhood_pattern_mean", "itbi_m2_neighborhood_median",
        "itbi_neighborhood_sales_count", "itbi_m2_neighborhood_pattern_median",
    ]
    assert list(analysis.columns[:len(expected_leading_columns)]) == expected_leading_columns
    assert record["discount_vs_neighborhood_pattern_mean"] < record["discount_vs_neighborhood_mean"]
    assert (root / "data" / "processed" / "analysis.csv").exists()
    report = generate_report(settings, run_id="test")
    report_path = Path(report.artifacts[0])
    assert report_path.exists()
    with pd.ExcelFile(report_path) as xls:
        assert set(xls.sheet_names) == {
            "Parking_Eligible", "Parking_Pending", "Parking_Excluded", "Neighborhood_Summary",
            "All_Listings", "Data_Quality", "Run",
        }


def test_listing_contract():
    assert ListingCollector._is_valid_item({
        "link": "https://example.test/1", "price": "R$ 500.000", "area": "80 m²",
    })
    assert not ListingCollector._is_valid_item({"link": "", "price": "R$ 500.000", "area": "80 m²"})


def test_olx_location_filter_keeps_only_the_requested_bh_neighborhood():
    scraper = CrawleeScraper()
    assert scraper.is_olx_location_match("Belo Horizonte, Belvedere", "BELVEDERE")
    assert not scraper.is_olx_location_match("Santa Luzia, Belvedere", "BELVEDERE")
    assert not scraper.is_olx_location_match("Belo Horizonte, Savassi", "BELVEDERE")


def test_raw_listing_contract_has_all_structured_card_fields():
    listing = CrawleeScraper.listing_with_contract({
        "title": "Apartamento", "price": "R$ 800.000", "area": "50 m²",
        "link": "https://example.test/1",
    })
    assert list(listing) == RAW_LISTING_COLUMNS
    assert listing["bathrooms"] == ""
    assert listing["condominium"] == ""
    assert listing["advertised_iptu"] == ""


def test_prepare_listings_prefers_structured_card_values(project):
    root, config_path = project
    run_dir = root / "data" / "listings" / "card-fields"
    run_dir.mkdir(parents=True)
    pd.DataFrame([{
        "title": "Apartamento com 9 quartos em Rua Texto 999, Anchieta",
        "street": "Rua Estruturada", "neighborhood": "ANCHIETA",
        "price": "R$ 800.000", "area": "50 m²", "bedrooms": "1",
        "bathrooms": "2", "parking_spaces": "3", "condominium": "R$ 275",
        "advertised_iptu": "R$ 125", "number": "395", "link": "https://example.test/structured",
        "site": "VivaReal",
    }], columns=RAW_LISTING_COLUMNS).to_csv(
        run_dir / "vivareal.csv", index=False, sep=";", encoding="utf-8-sig"
    )
    (root / "data" / "listings" / "latest.txt").write_text("card-fields", encoding="utf-8")

    listings = prepare_listings(load_settings(config_path), ["ANCHIETA"])
    record = listings.iloc[0]
    assert record["street"] == "ESTRUTURADA"
    assert record["number"] == "395"
    assert record["bedrooms"] == 1
    assert record["bathrooms"] == 2
    assert record["parking_spaces"] == 3
    assert record["condominium"] == 275
    assert record["advertised_iptu"] == 125


def test_price_filter_is_sent_to_portals_and_enforced_locally():
    scraper = CrawleeScraper()
    requests = scraper.generate_start_urls(
        ["SAVASSI"],
        sites=["vivareal", "zap", "olx", "quintoandar", "imovelweb"],
        price_filters={"Savassi": {"max": 800_000, "min_area_m2": 40}},
    )
    urls = {request["userData"]["site"]: request["url"] for request in requests}
    assert "precoMaximo=800000" in urls["VivaReal"]
    assert "precoMaximo=800000" in urls["Zap Imóveis"]
    assert "pe=800000" in urls["OLX"]
    assert urls["QuintoAndar"].endswith(
        "/de-150000-a-800000-venda"
    )
    assert urls["Imovelweb"].endswith(
        "/apartamentos-venda-savassi-belo-horizonte-mais-40-m2-menos-800000-reales.html"
    )

    filters = {"SAVASSI": {"max": 800_000}}
    assert ListingCollector._matches_price_filter(
        {"neighborhood": "Savassi", "price": "R$ 799.999"}, filters,
    )
    assert not ListingCollector._matches_price_filter(
        {"neighborhood": "Savassi", "price": "R$ 800.001"}, filters,
    )
    assert ListingCollector._matches_price_filter(
        {"neighborhood": "Lourdes", "price": "R$ 2.000.000"}, filters,
    )

    assert not ListingCollector._matches_price_filter(
        {"neighborhood": "Savassi", "price": "R$ 700.000", "area": "35 m²"},
        {"SAVASSI": {"max": 800_000, "min_area_m2": 40}},
    )


def test_imovelweb_accepts_only_cards_from_the_requested_bh_neighborhood():
    scraper = CrawleeScraper()

    assert scraper.is_imovelweb_location_match("Savassi, Belo Horizonte", "SAVASSI")
    assert scraper.is_imovelweb_location_match("São Bento, Belo Horizonte", "SÃO BENTO")
    assert not scraper.is_imovelweb_location_match("Sion, Belo Horizonte", "SAVASSI")
    assert not scraper.is_imovelweb_location_match("Savassi, Nova Lima", "SAVASSI")


def test_quintoandar_accepts_only_cards_from_the_requested_bh_neighborhood():
    scraper = CrawleeScraper()

    assert scraper.is_quintoandar_location_match("Rua Alagoas, Savassi · Belo Horizonte", "SAVASSI")
    assert not scraper.is_quintoandar_location_match("Rua Venezuela, Sion · Belo Horizonte", "SAVASSI")


def test_adaptive_pagination_stops_on_low_yield_after_minimum_pages():
    scraper = CrawleeScraper()
    filters = {"SAVASSI": {"max": 800_000}}
    page_one = [
        {"link": f"https://example.test/{index}", "price": "R$ 700.000"}
        for index in range(5)
    ] + [{"link": "https://example.test/expensive", "price": "R$ 1.200.000"}]
    new_items, continued = scraper.register_page(
        page_one, "VivaReal", "Savassi", 1, filters, 10, 2, 3,
    )
    assert len(new_items) == 6
    assert continued
    assert scraper.pagination_stats[-1]["new_within_price"] == 5

    page_two = [
        page_one[0],
        {"link": "https://example.test/next-1", "price": "R$ 750.000"},
        {"link": "https://example.test/next-2", "price": "R$ 780.000"},
    ]
    new_items, continued = scraper.register_page(
        page_two, "VivaReal", "Savassi", 2, filters, 10, 2, 3,
    )
    assert len(new_items) == 2
    assert not continued
    assert scraper.pagination_stats[-1]["decision"] == "low_yield"


def test_single_page_config_uses_compatible_minimum(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"pages_per_neighborhood": 1}), encoding="utf-8")
    settings = load_settings(config_path)
    assert settings.pages_per_neighborhood == 1
    assert settings.min_pages_per_neighborhood == 1


def test_multiple_runs_accumulate_daily_history(project):
    root, config_path = project
    settings = load_settings(config_path)

    run_1_dir = root / "data" / "listings" / "run_2026_08_13"
    run_1_dir.mkdir(parents=True)
    pd.DataFrame([{
        "title": "Apartamento em Rua Ptolomeu 130, Anchieta",
        "price": "R$ 400.000", "area": "50 m²", "link": "https://example.test/ad1",
        "neighborhood": "ANCHIETA", "site": "VivaReal",
        "collected_at": "2026-08-13T10:00:00+00:00",
        "collected_date": "2026-08-13",
    }]).to_csv(run_1_dir / "vivareal.csv", index=False, sep=";", encoding="utf-8-sig")

    run_2_dir = root / "data" / "listings" / "run_2026_08_14"
    run_2_dir.mkdir(parents=True)
    pd.DataFrame([
        {
            "title": "Apartamento em Rua Ptolomeu 130, Anchieta",
            "price": "R$ 390.000", "area": "50 m²", "link": "https://example.test/ad1",
            "neighborhood": "ANCHIETA", "site": "VivaReal",
            "collected_at": "2026-08-14T10:00:00+00:00",
            "collected_date": "2026-08-14",
        },
        {
            "title": "Apartamento Novo em Rua Ptolomeu 140, Anchieta",
            "price": "R$ 450.000", "area": "60 m²", "link": "https://example.test/ad2",
            "neighborhood": "ANCHIETA", "site": "VivaReal",
            "collected_at": "2026-08-14T10:00:00+00:00",
            "collected_date": "2026-08-14",
        },
    ]).to_csv(run_2_dir / "vivareal.csv", index=False, sep=";", encoding="utf-8-sig")

    now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    listings = prepare_listings(settings, ["ANCHIETA"], now=now)
    assert len(listings) == 3
    assert set(listings["collected_date"]) == {"2026-08-13", "2026-08-14"}
    assert set(listings["url"]) == {"https://example.test/ad1", "https://example.test/ad2"}
    ad1_records = listings[listings["url"] == "https://example.test/ad1"]
    assert len(ad1_records) == 2
    assert set(ad1_records["collected_date"]) == {"2026-08-13", "2026-08-14"}

