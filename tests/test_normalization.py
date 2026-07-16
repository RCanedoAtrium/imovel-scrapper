from imovel_scrapper.normalization import (
    clean_numeric,
    extract_area_m2,
    extract_currency,
    normalize_neighborhood,
    normalize_street,
    parse_listing_address,
)


def test_brazilian_numbers_and_listing_fragments():
    assert clean_numeric("R$ 2.350.000,50") == 2_350_000.50
    assert extract_currency("R$ 3.500.000 Cond. R$ 2.300") == 3_500_000
    assert extract_area_m2("4 350m² 5+") == 350
    assert extract_area_m2("aproximadamente 70, 00m²") == 70
    assert extract_area_m2("3 quartos 2 vagas") == 0


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
