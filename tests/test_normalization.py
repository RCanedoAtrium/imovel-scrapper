from property_scraper.normalization import (
    clean_numeric,
    detect_area_warning,
    extract_area_m2,
    extract_currency,
    normalize_neighborhood,
    normalize_street,
    parse_listing_address,
    validate_typology_coherence,
)


def test_brazilian_numbers_and_listing_fragments():
    assert clean_numeric("R$ 2.350.000,50") == 2_350_000.50
    assert extract_currency("R$ 3.500.000 Cond. R$ 2.300") == 3_500_000
    assert extract_area_m2("4 350m² 5+") == 350
    assert extract_area_m2("aproximadamente 70, 00m²") == 70
    assert extract_area_m2("3 quartos 2 vagas") == 0


def test_area_util_vs_total_extraction():
    assert extract_area_m2("78 m² útil 444 m² tot.") == 78
    assert extract_area_m2("120 m² privativa 250 m² total") == 120
    assert extract_area_m2("444 m² tot.") == 444


def test_typology_coherence_validation():
    # Large area without keyword should fail
    coherent, warning = validate_typology_coherence("Apartamento 3 quartos no Belvedere", 450)
    assert not coherent
    assert warning == "area_mismatch_warning"

    # Large area with cobertura/privativa should pass
    coherent_cob, _ = validate_typology_coherence("Cobertura duplex com vista definitiva", 450)
    assert coherent_cob

    coherent_priv, _ = validate_typology_coherence("Apartamento com área privativa 3 quartos", 350)
    assert coherent_priv

    # Standard area should pass
    coherent_std, _ = validate_typology_coherence("Apartamento 2 quartos", 80)
    assert coherent_std


def test_detect_area_warning():
    assert detect_area_warning("444 m² tot.", "Apartamento 3 quartos", 444) == "area_total_only"
    assert detect_area_warning("85 m² útil", "Apartamento 3 quartos", 85) == ""
    assert detect_area_warning("400 m²", "Apartamento 3 quartos", 400) == "area_mismatch_warning"


def test_address_and_text_normalization():
    assert normalize_street("Av. Luiz Paulo Franco") == "LUIZ PAULO FRANCO"
    assert normalize_street("Ave. Álvares Cabral") == "ALVARES CABRAL"
    assert normalize_street("Rua Assunção") == "ASSUNCAO"
    assert normalize_street("assuncao") == "ASSUNCAO"
    assert normalize_neighborhood("São Bento") == "SAO BENTO"
    street, number, cep = parse_listing_address(
        "Apartamento à venda em Rua Ptolomeu 130, Anchieta, Belo Horizonte 30360-250"
    )
    assert (street, number, cep) == ("PTOLOMEU", "130", "30360250")

