import pandas as pd

from imovel_scrapper.analysis import build_cep_lookup, infer_listing_cep, match_building


def test_matching_evidence_levels():
    buildings = pd.DataFrame([{
        "cep": "30360250", "number": "130", "street": "PTOLOMEU", "neighborhood": "ANCHIETA",
        "area_min": 50.0, "area_max": 80.0, "finish_pattern": "P3", "building_year": 2000,
    }])
    listing = pd.Series({
        "cep": "30360250", "number": "130", "street": "PTOLOMEU", "neighborhood": "ANCHIETA", "area_m2": 60,
    })
    _, evidence, reason = match_building(listing, buildings)
    assert evidence == "confirmado"
    assert reason == "cep+numero_unico"


def test_matching_without_address_is_unmatched():
    buildings = pd.DataFrame(columns=["cep", "number", "street", "neighborhood", "area_min", "area_max"])
    listing = pd.Series({"cep": "", "number": "", "street": "", "neighborhood": "ANCHIETA", "area_m2": 60})
    matched, evidence, _ = match_building(listing, buildings)
    assert matched is None
    assert evidence == "sem_match"


def test_cep_is_inferred_from_normalized_street_and_number():
    transactions = pd.DataFrame([{
        "street": "ASSUNCAO", "number": "42", "neighborhood": "SAVASSI", "cep": "30112000",
    }])
    buildings = pd.DataFrame([{
        "street": "RUA ASSUNÇÃO", "number": "42", "neighborhood": "SAVASSI", "cep": "30112000",
    }])
    cep, method = infer_listing_cep(
        pd.Series({"street": "Rua Assuncao", "number": "42", "neighborhood": "Savassi", "cep": ""}),
        build_cep_lookup(transactions, buildings),
    )
    assert (cep, method) == ("30112000", "logradouro_numero")
