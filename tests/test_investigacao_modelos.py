import json

import pandas as pd
import numpy as np

from investigacao.comparar_modelos_sem_preco import ordinal_probabilities
from investigacao.avaliar_modelos_padrao import (
    PATTERNS,
    build_splits,
    read_itbi_sources,
    run_investigation,
)


def _write_itbi_sources(root):
    directory = root / "data" / "itbi"
    directory.mkdir(parents=True)
    for month in range(1, 13):
        competence = f"2025{month:02d}"
        rows = []
        for pattern_index, pattern in enumerate(PATTERNS):
            for item in range(4):
                rows.append({
                    "Endereco": "" if item == 0 else f"Rua {'Alpha' if item < 3 else 'Rara'} {100 + item} - 30100-000",
                    "Bairro": "São Bento" if item % 2 else "Anchieta",
                    "Area Construida Adquirida": f"{50 + pattern_index * 15},{item}",
                    "Padrao Acabamento (Unidade)": pattern,
                    "Descricao Tipo Ocupacao (Unidade)": "RESIDENCIAL",
                    "Valor Declarado": str(200_000 + pattern_index * 40_000),
                    "Valor Base Calculo": str(210_000 + pattern_index * 40_000),
                })
        rows.append({
            "Endereco": "Rua Ignorada 1", "Bairro": "Anchieta", "Area Construida Adquirida": "80",
            "Padrao Acabamento (Unidade)": "P3", "Descricao Tipo Ocupacao (Unidade)": "COMERCIAL",
            "Valor Declarado": "300000", "Valor Base Calculo": "300000",
        })
        frame = pd.DataFrame(rows)
        if month >= 10:
            frame = frame.rename(columns={"Endereco": "Endereco Completo"})
        frame.to_csv(directory / f"pda_itbi_relatorio_{competence}.csv", sep=";", index=False, encoding="utf-8-sig")


def _config(root):
    path = root / "config.json"
    path.write_text(json.dumps({"neighborhoods": ["ANCHIETA", "SÃO BENTO"]}), encoding="utf-8")
    return path


def test_investigation_reads_filters_and_builds_expected_splits(tmp_path):
    _write_itbi_sources(tmp_path)
    frame, sources = read_itbi_sources(tmp_path / "data" / "itbi")

    assert len(sources) == 12
    assert len(frame) == 12 * 20
    assert frame["transaction_price"].iloc[0] >= 210_000
    assert set(frame["finish_pattern"]) == set(PATTERNS)
    assert "" in set(frame["street"])

    splits, months, final_months = build_splits(frame, ["ANCHIETA", "SAO BENTO"])
    assert len(months) == 12
    assert final_months == ["2025-10", "2025-11", "2025-12"]
    assert all(set(split.test["neighborhood"]) <= {"ANCHIETA", "SAO BENTO"} for split in splits)
    temporal = splits[1]
    assert set(temporal.train["competence"]) == set(months[:-3])
    assert set(temporal.test["competence"]) == set(final_months)


def test_investigation_writes_semicolon_outputs_and_model_artifacts(tmp_path):
    _write_itbi_sources(tmp_path)
    config = _config(tmp_path)
    output = tmp_path / "investigacao" / "resultados"

    result = run_investigation(config, output)

    assert result["models"] == 8
    expected = {
        "itbi_historico_desde_2024.csv", "itbi_bairros_alvo_ultimos_12_meses.csv",
        "comparacao_modelos.csv", "metricas_por_padrao.csv", "matrizes_confusao.csv",
        "busca_limiar_rua.csv", "cobertura_ruas.csv", "manifesto.json",
    }
    assert expected <= {path.name for path in output.iterdir()}
    prepared = pd.read_csv(output / "itbi_bairros_alvo_ultimos_12_meses.csv", sep=";", encoding="utf-8-sig")
    assert list(prepared.columns) == [
        "competence", "neighborhood", "street", "area_m2", "transaction_price", "finish_pattern",
    ]
    assert not {"cep", "building_year", "price_m2"} & set(prepared.columns)
    comparison = pd.read_csv(output / "comparacao_modelos.csv", sep=";", encoding="utf-8-sig")
    assert set(comparison["split"]) == {
        "historico_sem_preco", "temporal_sem_preco", "temporal_com_preco", "aleatorio_com_preco",
    }
    coverage = pd.read_csv(output / "cobertura_ruas.csv", sep=";", encoding="utf-8-sig")
    assert set(coverage["street_status"]) == {"frequente", "rara", "ausente", "inedita"}


def test_ordinal_probabilities_are_valid_and_respect_class_order():
    probabilities = ordinal_probabilities(np.array([
        [0.6, 0.8, 0.2, 0.1],  # input nao monotono deve ser corrigido
        [0.9, 0.7, 0.4, 0.2],
    ]))

    assert probabilities.shape == (2, 5)
    assert np.allclose(probabilities.sum(axis=1), 1)
    assert np.all(probabilities >= 0)
