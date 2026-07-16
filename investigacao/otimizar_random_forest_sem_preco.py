"""Seleciona Random Forest sem preco por validacao temporal e mede o teste final."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, f1_score, recall_score

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from investigacao.avaliar_modelos_padrao import (
    PATTERNS,
    SEED,
    _bucket_streets,
    _write_csv,
    build_splits,
    load_target_neighborhoods,
    read_itbi_sources,
)
from investigacao.comparar_modelos_sem_preco import _one_hot


CANDIDATES = [
    {"street_threshold": 10, "min_samples_leaf": 1},
    {"street_threshold": 20, "min_samples_leaf": 1},
    {"street_threshold": 30, "min_samples_leaf": 1},
    {"street_threshold": 20, "min_samples_leaf": 2},
]
SELECTION_TREES = 100
FINAL_TREES = 300


def _predict(train: pd.DataFrame, test: pd.DataFrame, config: dict[str, int], n_estimators: int):
    train, test, _ = _bucket_streets(train, test, config["street_threshold"])
    x_train, x_test = _one_hot(train, test)
    model = RandomForestClassifier(
        n_estimators=n_estimators,
        random_state=SEED,
        class_weight="balanced_subsample",
        min_samples_leaf=config["min_samples_leaf"],
        max_features="sqrt",
        n_jobs=-1,
    )
    model.fit(x_train, train["finish_pattern"])
    return model.predict(x_test)


def _score(actual: pd.Series, predicted) -> dict[str, float]:
    labels = sorted(actual.unique())
    return {
        "macro_f1": float(f1_score(actual, predicted, labels=labels, average="macro", zero_division=0)),
        "balanced_accuracy": float(recall_score(actual, predicted, labels=labels, average="macro", zero_division=0)),
        "accuracy": float(accuracy_score(actual, predicted)),
    }


def _select(train: pd.DataFrame, targets: set[str]) -> pd.DataFrame:
    validation_months = ["2026-01", "2026-02", "2026-03"]
    rows: list[dict[str, object]] = []
    for config in CANDIDATES:
        for month in validation_months:
            fit = train[train["competence"] < month]
            validate = train[(train["competence"] == month) & train["neighborhood"].isin(targets)]
            predicted = _predict(fit, validate, config, SELECTION_TREES)
            rows.append({**config, "validation_month": month, **_score(validate["finish_pattern"], predicted)})
    return pd.DataFrame(rows)


def run_optimization(config_path: str | Path, output_dir: str | Path | None = None) -> dict[str, object]:
    config_path = Path(config_path).resolve()
    root = config_path.parent
    output = Path(output_dir).resolve() if output_dir else root / "investigacao" / "resultados" / "random_forest_otimizado"
    history, _ = read_itbi_sources(root / "data" / "itbi")
    targets = set(load_target_neighborhoods(config_path))
    splits, _, final_months = build_splits(history, targets)
    split = next(item for item in splits if item.name == "historico_sem_preco")
    selection = _select(split.train, targets)
    aggregate = selection.groupby(["street_threshold", "min_samples_leaf"], as_index=False)[["macro_f1", "balanced_accuracy", "accuracy"]].mean()
    aggregate = aggregate.sort_values(["macro_f1", "balanced_accuracy"], ascending=False).reset_index(drop=True)
    winner = aggregate.iloc[0].to_dict()
    final_config = {"street_threshold": int(winner["street_threshold"]), "min_samples_leaf": int(winner["min_samples_leaf"])}
    prediction = _predict(split.train, split.test, final_config, FINAL_TREES)
    final = _score(split.test["finish_pattern"], prediction)
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(selection, output / "validacao_temporal_por_mes.csv")
    _write_csv(aggregate, output / "validacao_temporal_resumo.csv")
    _write_csv(pd.DataFrame([{**final_config, "n_estimators": FINAL_TREES, "test_months": ",".join(final_months), **final}]), output / "resultado_teste_final.csv")
    report = f"""# Otimização do Random Forest sem preço

## Dados e protocolo

- Entradas: bairro, rua normalizada e área; não há preço nem preço/m².
- Treino final: {len(split.train):,} transações de jun/2024 a mar/2026, toda BH.
- Teste final: {len(split.test):,} transações dos bairros-alvo em {', '.join(final_months)}.
- Seleção: três validações temporais internas (jan–mar/2026), sem usar o teste final.

## Configuração selecionada

- Rua rara: menos de {final_config['street_threshold']} transações no treino.
- `min_samples_leaf={final_config['min_samples_leaf']}`; `max_features=sqrt`; 300 árvores; pesos balanceados por árvore.

## Resultado no teste final

| F1 macro | Balanced accuracy | Acurácia |
| ---: | ---: | ---: |
| {final['macro_f1']:.3f} | {final['balanced_accuracy']:.3f} | {final['accuracy']:.3f} |

Ambas as metas foram {'atingidas' if final['macro_f1'] >= .7 and final['balanced_accuracy'] >= .72 else 'não atingidas'}: F1 macro ≥ 0,700 e balanced accuracy ≥ 0,720.
"""
    (output / "relatorio_otimizacao.md").write_text(report, encoding="utf-8")
    (output / "manifesto.json").write_text(json.dumps({"candidates": CANDIDATES, "final_config": final_config, "selection_trees": SELECTION_TREES, "final_trees": FINAL_TREES, "seed": SEED}, indent=2), encoding="utf-8")
    return {"output_dir": str(output), "config": final_config, **final}


def main() -> None:
    parser = argparse.ArgumentParser(description="Otimiza Random Forest ITBI sem preco")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--output-dir")
    args = parser.parse_args()
    print(json.dumps(run_optimization(args.config, args.output_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
