from pathlib import Path

import pandas as pd

from imovel_scrapper.sources.pbh import (
    ITBI_REQUIRED,
    parse_iptu_competence,
    parse_itbi_competence,
    select_iptu_resource,
    select_itbi_resources,
    validate_csv_schema,
)


def test_resource_competence_and_selection():
    iptu = [
        {"format": "CSV", "name": "20260401_regional_centro_sul_cadastro_imobiliario", "url": "https://x/20260401_regional_centro_sul_cadastro_imobiliario.csv"},
        {"format": "CSV", "name": "20260504_regional_centro_sul_cadastro_imobiliario", "url": "https://x/20260504_regional_centro_sul_cadastro_imobiliario.csv"},
    ]
    assert parse_iptu_competence(iptu[0]) == "2026-04"
    assert select_iptu_resource(iptu)[0] == "2026-05"
    itbi = [
        {"format": "CSV", "name": "2026 - Maio - ITBI Relatórios", "url": "https://x/pda_itbi_relatorio_202605.csv"},
        {"format": "CSV", "name": "2026 - Junho - ITBI Relatórios", "url": "https://x/pda_itbi_relatorio_202606.csv"},
    ]
    assert parse_itbi_competence(itbi[0]) == "2026-05"
    assert sorted(select_itbi_resources(itbi, "2026-01")) == ["2026-05", "2026-06"]


def test_schema_accepts_historical_itbi_column_variants(tmp_path):
    path = tmp_path / "itbi.csv"
    columns = [
        "Endereco", "Bairro", "Area Construida Adquirida", "Descricao Tipo Ocupacao Unidade",
        "Valor Declarado", "Valor Base Calculo",
    ]
    pd.DataFrame(columns=columns).to_csv(path, sep=";", index=False)
    validate_csv_schema(path, ITBI_REQUIRED)
