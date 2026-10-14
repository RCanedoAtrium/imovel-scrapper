"""Tests for the ranking rules documented in GEMINI.md."""
from pathlib import Path

import pandas as pd

from property_scraper.analysis import analyze
from property_scraper.config import load_settings
from property_scraper.normalization import (
    find_excluded_region,
    validate_typology_coherence,
)
from property_scraper.processing import prepare_all, prepare_listings

ADDRESS_TITLE = "Apartamento à venda em Rua Ptolomeu 130, Anchieta, Belo Horizonte 30360-250"


def _write_reference(root: Path) -> None:
    """ITBI: ANCHIETA mean 5110/m², P3 mean 5050/m², P4 mean 5170/m². IPTU: one P3 building."""
    itbi_dir = root / "data" / "itbi"
    iptu_dir = root / "data" / "iptu"
    for directory in (itbi_dir, iptu_dir):
        directory.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([
        {
            "Endereco Completo": "Rua Ptolomeu 130 - 30360250",
            "Bairro": "ANCHIETA",
            "Ano de Construcao (Unidade)": "2000",
            "Area Construida Adquirida": "50,00",
            "Padrao Acabamento (Unidade)": "P3" if index < 6 else "P4",
            "Descrição Tipo Ocupacao (Unidade)": "RESIDENCIAL",
            "Valor Declarado": str(250_000 + index * 1_000),
            "Valor Base Calculo": str(240_000 + index * 1_000),
        }
        for index in range(12)
    ]).to_csv(itbi_dir / "pda_itbi_relatorio_202601.csv", sep=";", index=False)
    pd.DataFrame([{
        "ID_IPTU_CTM": "1", "AREA_CONSTRUCAO": "60.0", "TIPO_CONSTRUTIVO": "APARTAMENTO",
        "PADRAO_ACABAMENTO": "P3", "NOME_LOGRADOURO": "PTOLOMEU", "NUMERO_IMOVEL": "130",
        "CEP": "30360250",
    }]).to_csv(iptu_dir / "20260101_regional_centro_sul_cadastro_imobiliario.csv", index=False)


def _write_listings(root: Path, listings: list[dict]) -> None:
    listing_dir = root / "data" / "listings" / "fixture"
    listing_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        {"neighborhood": "ANCHIETA", "site": "Fixture", "link": f"https://example.test/{index}", **listing}
        for index, listing in enumerate(listings)
    ]
    pd.DataFrame(rows).to_csv(listing_dir / "fixture.csv", index=False)
    (root / "data" / "listings" / "latest.txt").write_text("fixture", encoding="utf-8")


def _analyze_single(project, **listing) -> pd.Series:
    root, config_path = project
    _write_reference(root)
    _write_listings(root, [listing])
    settings = load_settings(config_path)
    prepare_all(settings)
    analyze(settings, classifier_mode="heuristics")
    analysis = pd.read_csv(
        root / "data" / "processed" / "analysis.csv", sep=";", decimal=",", encoding="utf-8-sig",
        keep_default_na=False,
    )
    assert len(analysis) == 1
    return analysis.iloc[0]


# --- §4 Conservative reference -------------------------------------------------

def test_listing_must_beat_the_conservative_reference(project):
    # 4060/m² is 20.5% below the neighborhood mean (5110) but only 19.6% below
    # the P3 mean (5050), so it must not qualify.
    record = _analyze_single(
        project, title=f"{ADDRESS_TITLE} com 1 vaga livre", price="R$ 243.600", area="60 m²",
    )
    assert record["conservative_ref_m2"] == 5050
    assert "neighborhood_discount" in record["opportunity_criteria"]
    assert "conservative_discount" not in record["opportunity_criteria"]
    assert record["opportunity_status"] == "not_qualified"
    assert record["ranking_status"] == "not_qualified"


def test_listing_below_conservative_reference_qualifies(project):
    record = _analyze_single(
        project, title=f"{ADDRESS_TITLE} com 1 vaga livre", price="R$ 100.000", area="60 m²",
    )
    assert "conservative_discount" in record["opportunity_criteria"]
    assert record["opportunity_status"] == "confirmed"
    assert record["ranking_status"] == "parking_eligible"


# --- §4 Garage requirement -----------------------------------------------------

def test_listing_without_any_parking_space_is_excluded_from_ranking(project):
    record = _analyze_single(project, title=ADDRESS_TITLE, price="R$ 100.000", area="60 m²")
    assert record["opportunity_status"] == "confirmed"
    assert record["ranking_status"] == "parking_excluded"
    assert record["parking_exclusion_reason"] == "no_parking_space"


def test_structured_parking_count_without_type_goes_to_review(project):
    record = _analyze_single(
        project, title=ADDRESS_TITLE, price="R$ 100.000", area="60 m²", parking_spaces="1",
    )
    assert record["ranking_status"] == "parking_pending_review"


def test_tandem_parking_is_excluded(project):
    record = _analyze_single(
        project, title=f"{ADDRESS_TITLE} com 2 vagas em linha", price="R$ 100.000", area="60 m²",
    )
    assert record["ranking_status"] == "parking_excluded"
    assert record["parking_exclusion_reason"] == "tandem"


# --- §1 Area reconciliation ----------------------------------------------------

def test_total_only_area_is_confirmed_by_iptu_address_match(project):
    record = _analyze_single(
        project, title=f"{ADDRESS_TITLE} com 1 vaga livre", price="R$ 100.000", area="60 m² tot.",
    )
    assert record["area_warning"] == "area_confirmed_by_iptu"
    assert record["opportunity_status"] == "confirmed"


def test_total_only_area_without_iptu_match_is_unverified(project):
    record = _analyze_single(
        project, title="Apartamento no Anchieta com 1 vaga livre", price="R$ 100.000", area="60 m² tot.",
    )
    assert record["area_warning"] == "area_unverified"
    assert record["opportunity_status"] == "unverified_area"
    assert record["ranking_status"] == "not_qualified"


def test_typology_rule_targets_conventional_apartments():
    assert validate_typology_coherence("Apartamento 3 quartos", 450, bedrooms=3) == (False, "area_mismatch_warning")
    # Unknown bedroom count: read from the title, else treated as conventional.
    assert validate_typology_coherence("Apartamento 4 quartos", 450) == (False, "area_mismatch_warning")
    assert validate_typology_coherence("Apartamento amplo", 450) == (False, "area_mismatch_warning")
    # Outside the 2-4 bedroom range the rule does not apply.
    assert validate_typology_coherence("Apartamento 6 quartos", 450, bedrooms=6) == (True, "")
    # Non-apartment words no longer exempt a large area.
    assert validate_typology_coherence("Apartamento tipo casa 3 quartos", 450) == (False, "area_mismatch_warning")
    assert validate_typology_coherence("Apartamento 3 quartos com terreno", 450) == (False, "area_mismatch_warning")
    # Documented exemptions still apply.
    assert validate_typology_coherence("Cobertura 3 quartos", 450) == (True, "")
    assert validate_typology_coherence("Apartamento garden 3 quartos", 450) == (True, "")


# --- §3 Regional contamination -------------------------------------------------

def test_find_excluded_region_handles_slugs_and_accents():
    regions = ["RIBEIRAO DAS NEVES", "SANTA MONICA", "CONTAGEM"]
    assert find_excluded_region("https://x.test/venda/ribeirao-das-neves/apto-1", regions) == "RIBEIRAO DAS NEVES"
    assert find_excluded_region("Apartamento no bairro Santa Mônica", regions) == "SANTA MONICA"
    assert find_excluded_region("Eldorado, Contagem - MG", regions) == "CONTAGEM"
    assert find_excluded_region("Apartamento em Santa Lúcia, Belo Horizonte", regions) == ""
    assert find_excluded_region("Rua Contagemx 10", regions) == ""


def test_prepare_listings_drops_out_of_area_regions(project):
    root, config_path = project
    _write_listings(root, [
        {"title": "Apartamento no Anchieta", "price": "R$ 400.000", "area": "60 m²"},
        {"title": "Apartamento 3 quartos", "price": "R$ 400.000", "area": "60 m²",
         "location": "Eldorado, Contagem"},
        {"title": "Apartamento 2 quartos", "price": "R$ 300.000", "area": "55 m²",
         "link": "https://example.test/imovel/betim-mg-apto"},
    ])
    listings = prepare_listings(load_settings(config_path), ["ANCHIETA"])
    assert list(listings["url"]) == ["https://example.test/0"]


def test_configured_target_neighborhood_is_never_blocked(project):
    root, _ = project
    config_path = root / "config.json"
    config_path.write_text(
        '{"neighborhoods": ["BARREIRO"], "excluded_regions": ["BARREIRO", "CONTAGEM"]}', encoding="utf-8",
    )
    _write_listings(root, [
        {"title": "Apartamento no Barreiro", "price": "R$ 300.000", "area": "60 m²", "neighborhood": "BARREIRO"},
    ])
    listings = prepare_listings(load_settings(config_path), ["BARREIRO"])
    assert len(listings) == 1
