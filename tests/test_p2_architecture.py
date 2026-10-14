from pathlib import Path

import pandas as pd

from property_scraper.analysis import BuildingIndex, match_building
from property_scraper.config import RulesSettings, load_settings
from property_scraper.processing import _canonical_url
from property_scraper.sources.portals import (
    GrupoOLXAdapter,
    ImovelwebAdapter,
    OLXAdapter,
    QuintoAndarAdapter,
)
from property_scraper.storage import read_dataset, write_dataset


def test_building_index_candidate_lookups():
    buildings = pd.DataFrame([
        {
            "cep": "30112000",
            "number": "100",
            "street": "RUA DA BAHIA",
            "neighborhood": "LOURDES",
            "area_min": 60.0,
            "area_max": 90.0,
            "finish_pattern": "P4",
            "building_year": 2010,
        },
        {
            "cep": "30112000",
            "number": "200",
            "street": "RUA DA BAHIA",
            "neighborhood": "LOURDES",
            "area_min": 100.0,
            "area_max": 140.0,
            "finish_pattern": "P4",
            "building_year": 2015,
        },
    ])
    index = BuildingIndex(buildings)

    # CEP + number
    c1 = index.get_by_cep_number("30112000", "100")
    assert len(c1) == 1
    assert c1.iloc[0]["area_min"] == 60.0

    # Street + number
    c2 = index.get_by_street_number("RUA DA BAHIA", "200")
    assert len(c2) == 1
    assert c2.iloc[0]["area_max"] == 140.0

    # Street + neighborhood
    c3 = index.get_by_street_neighborhood("RUA DA BAHIA", "LOURDES")
    assert len(c3) == 2

    # Miss lookup
    assert index.get_by_cep_number("99999999", "999").empty

    # Test match_building with index
    listing = pd.Series({
        "cep": "30112000",
        "number": "100",
        "street": "RUA DA BAHIA",
        "neighborhood": "LOURDES",
        "area_m2": 75.0,
    })
    matched, evidence, reason = match_building(listing, buildings, building_index=index)
    assert matched is not None
    assert evidence == "confirmed"
    assert reason == "cep+unique_number"


def test_write_and_read_dataset_parquet(tmp_path: Path):
    df = pd.DataFrame({
        "id": [1, 2, 3],
        "neighborhood": ["SAVASSI", "LOURDES", "SION"],
        "price_m2": [10500.50, 12000.00, 9800.75],
    })
    csv_path = tmp_path / "test_data.csv"
    meta = {"source": "unit_test"}

    data_path, manifest_path = write_dataset(df, csv_path, meta)
    assert data_path.exists()
    assert manifest_path.exists()

    # Parquet file should also be created
    parquet_path = csv_path.with_suffix(".parquet")
    assert parquet_path.exists()

    # read_dataset should load the data accurately (from parquet)
    loaded = read_dataset(csv_path)
    pd.testing.assert_frame_equal(loaded, df)


def test_canonical_url_resolves_portal_domains():
    # Relative links should resolve against the respective site domain
    assert _canonical_url("/imovel/123", site="zap") == "https://www.zapimoveis.com.br/imovel/123"
    assert _canonical_url("/imovel/456", site="quintoandar") == "https://www.quintoandar.com.br/imovel/456"
    assert _canonical_url("/propriedades/789.html", site="imovelweb") == "https://www.imovelweb.com.br/propriedades/789.html"
    assert _canonical_url("/imovel/999", site="olx") == "https://www.olx.com.br/imovel/999"
    assert _canonical_url("/imovel/000", site="vivareal") == "https://www.vivareal.com.br/imovel/000"
    # Full absolute URLs remain unchanged
    assert _canonical_url("https://mg.olx.com.br/ad/123", site="olx") == "https://mg.olx.com.br/ad/123"


def test_portal_adapters_url_building_and_pagination():
    # GrupoOLX (VivaReal & ZAP)
    vr = GrupoOLXAdapter("vivareal", "VIVAREAL", "VivaReal")
    vr_url = vr.build_url("Savassi", {"min": 300000, "max": 700000})
    assert "vivareal.com.br" in vr_url
    assert "savassi" in vr_url
    assert "precoMinimo=300000" in vr_url
    assert "precoMaximo=700000" in vr_url
    assert vr.extract_current_page(vr_url) == 1
    next_vr = vr.build_next_page_url(vr_url, 2)
    assert "pagina=2" in next_vr

    zap = GrupoOLXAdapter("zap", "ZAP", "Zap Imóveis")
    zap_url = zap.build_url("Lourdes", {"max": 900000})
    assert "zapimoveis.com.br" in zap_url
    assert "lourdes" in zap_url
    next_zap = zap.build_next_page_url(zap_url, 3)
    assert "page=3" in next_zap

    # OLX
    olx = OLXAdapter()
    olx_url = olx.build_url("Funcionários", {"max": 650000})
    assert "olx.com.br" in olx_url
    assert "pe=650000" in olx_url
    next_olx = olx.build_next_page_url(olx_url, 2)
    assert "o=2" in next_olx

    # OLX location filtering
    listings = [
        {"title": "Card 1", "location": "Belo Horizonte, Savassi"},
        {"title": "Card 2", "location": "Contagem, Centro"},
    ]
    matching, rejected = olx.filter_listings(listings, "Savassi")
    assert len(matching) == 1
    assert rejected == 1
    assert matching[0]["title"] == "Card 1"

    # QuintoAndar
    qa = QuintoAndarAdapter()
    qa_url = qa.build_url("Sion", {"min": 200000, "max": 800000})
    assert "quintoandar.com.br" in qa_url
    assert "de-200000-a-800000-venda" in qa_url

    # Imovelweb
    iw = ImovelwebAdapter()
    iw_url = iw.build_url("Anchieta", {"max": 750000, "min_area_m2": 50})
    assert "imovelweb.com.br" in iw_url
    assert "-mais-50-m2" in iw_url
    assert "-menos-750000-reales.html" in iw_url
    next_iw = iw.build_next_page_url(iw_url, 2)
    assert "-pagina-2.html" in next_iw


def test_rules_settings_and_config_loading():
    settings = load_settings("config.json")
    rules = settings.rules
    assert isinstance(rules, RulesSettings)
    assert rules.min_valid_price == 20000.0
    assert rules.min_valid_area_m2 == 10.0
    assert rules.title_area_override_ratio == 1.35
    assert rules.max_publication_age_days == 180
    assert rules.typology_area_limit_m2 == 300.0
    assert rules.dedup_area_tolerance == 0.06
    assert rules.dedup_price_tolerance == 0.08
    assert rules.dedup_strict_tolerance == 0.03
    assert rules.pattern_arbitrage_multiplier == 1.15
