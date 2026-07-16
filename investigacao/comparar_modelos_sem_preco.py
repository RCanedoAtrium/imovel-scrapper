"""Compara classificadores de padrao ITBI sem usar valor ou preco por m²."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_recall_fscore_support
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from xgboost import XGBClassifier

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from investigacao.avaliar_modelos_padrao import (
    FINAL_N_ESTIMATORS,
    PATTERNS,
    SEED,
    _bucket_streets,
    _write_csv,
    build_splits,
    load_target_neighborhoods,
    read_itbi_sources,
)


STREET_THRESHOLD = 30
ORDINAL_LABELS = {pattern: index + 1 for index, pattern in enumerate(PATTERNS)}


def _model_frame(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[["neighborhood", "street_model", "area_m2"]].copy()


def _one_hot(train: pd.DataFrame, test: pd.DataFrame):
    transformer = ColumnTransformer([
        ("categorical", OneHotEncoder(handle_unknown="ignore"), ["neighborhood", "street_model"]),
        ("numeric", "passthrough", ["area_m2"]),
    ])
    return transformer.fit_transform(_model_frame(train)), transformer.transform(_model_frame(test))


def random_forest_predict(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    x_train, x_test = _one_hot(train, test)
    model = RandomForestClassifier(
        n_estimators=FINAL_N_ESTIMATORS,
        random_state=SEED,
        class_weight="balanced_subsample",
        min_samples_leaf=2,
        n_jobs=-1,
    )
    model.fit(x_train, train["finish_pattern"])
    return model.predict(x_test)


def catboost_predict(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    model = CatBoostClassifier(
        loss_function="MultiClass",
        iterations=350,
        depth=8,
        learning_rate=0.06,
        auto_class_weights="Balanced",
        random_seed=SEED,
        verbose=False,
        thread_count=-1,
    )
    model.fit(_model_frame(train), train["finish_pattern"], cat_features=["neighborhood", "street_model"])
    return model.predict(_model_frame(test)).reshape(-1)


def xgboost_predict(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    x_train, x_test = _one_hot(train, test)
    labels = train["finish_pattern"].map(ORDINAL_LABELS).sub(1)
    counts = labels.value_counts()
    weights = labels.map(lambda value: len(labels) / (len(counts) * counts[value]))
    model = XGBClassifier(
        objective="multi:softprob",
        num_class=len(PATTERNS),
        n_estimators=350,
        max_depth=8,
        learning_rate=0.06,
        min_child_weight=5,
        subsample=0.85,
        colsample_bytree=0.85,
        reg_lambda=5.0,
        tree_method="hist",
        random_state=SEED,
        n_jobs=-1,
        eval_metric="mlogloss",
    )
    model.fit(x_train, labels, sample_weight=weights)
    return np.asarray([PATTERNS[index] for index in model.predict(x_test).astype(int)])


def ordinal_probabilities(cumulative_probabilities: np.ndarray) -> np.ndarray:
    """Converte P(y >= k), para k=2..5, em probabilidades P1..P5."""
    cumulative = np.minimum.accumulate(np.clip(cumulative_probabilities, 0, 1), axis=1)
    padded = np.column_stack([np.ones(len(cumulative)), cumulative, np.zeros(len(cumulative))])
    probabilities = padded[:, :-1] - padded[:, 1:]
    return probabilities / probabilities.sum(axis=1, keepdims=True)


def ordinal_catboost_predict(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    train_labels = train["finish_pattern"].map(ORDINAL_LABELS)
    cumulative: list[np.ndarray] = []
    for boundary in range(2, 6):
        target = (train_labels >= boundary).astype(int)
        model = CatBoostClassifier(
            loss_function="Logloss",
            iterations=220,
            depth=8,
            learning_rate=0.06,
            auto_class_weights="Balanced",
            random_seed=SEED + boundary,
            verbose=False,
            thread_count=-1,
        )
        model.fit(_model_frame(train), target, cat_features=["neighborhood", "street_model"])
        cumulative.append(model.predict_proba(_model_frame(test))[:, 1])
    probabilities = ordinal_probabilities(np.column_stack(cumulative))
    return np.asarray([PATTERNS[index] for index in probabilities.argmax(axis=1)])


def _evaluate(name: str, actual: pd.Series, predicted: np.ndarray) -> tuple[dict[str, object], pd.DataFrame, pd.DataFrame]:
    precision, recall, f1, support = precision_recall_fscore_support(actual, predicted, labels=PATTERNS, zero_division=0)
    present = support > 0
    per_pattern = pd.DataFrame({
        "model": name,
        "pattern": PATTERNS,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "support": support,
        "insufficient_support": support < 5,
    })
    confusion = pd.DataFrame(
        confusion_matrix(actual, predicted, labels=PATTERNS), index=PATTERNS, columns=PATTERNS
    ).rename_axis("actual").reset_index()
    confusion.insert(0, "model", name)
    summary = {
        "model": name,
        "macro_f1_present_classes": float(f1[present].mean()) if present.any() else 0.0,
        "balanced_accuracy": float(recall[present].mean()) if present.any() else 0.0,
        "accuracy": float(accuracy_score(actual, predicted)),
    }
    return summary, per_pattern, confusion


def _report(comparison: pd.DataFrame, test_months: list[str], train_records: int, test_records: int, train_scope: str) -> str:
    ordered = comparison.sort_values("macro_f1_present_classes", ascending=False).reset_index(drop=True)
    winner = ordered.iloc[0]
    rows = "\n".join(
        f"| {row.model} | {row.macro_f1_present_classes:.3f} | {row.balanced_accuracy:.3f} | {row.accuracy:.3f} |"
        for row in ordered.itertuples()
    )
    return f"""# Comparação de modelos sem preço

## Protocolo

- Entradas: bairro, rua normalizada e área construída; preço e preço/m² foram excluídos.
- Treino: {train_records:,} transações ITBI de {"toda Belo Horizonte" if train_scope == "all" else "somente dos bairros-alvo"}, anteriores a {test_months[0]}.
- Teste temporal: {test_records:,} transações dos bairros-alvo, competências {', '.join(test_months)}.
- Ruas com menos de {STREET_THRESHOLD} ocorrências no treino foram agrupadas como `RUA_RARA`.
- Métrica principal: F1 macro nas classes com suporte no teste. P1 não teve suporte no teste e é mostrado separadamente nos arquivos de métricas.

## Resultados

| Modelo | F1 macro | Balanced accuracy | Acurácia |
| --- | ---: | ---: | ---: |
{rows}

## Métodos comparados

- `random_forest`: 150 árvores com pesos balanceados e categorias codificadas em colunas.
- `catboost`: classificação multiclasse com bairro e rua como categorias nativas.
- `xgboost`: gradient boosting multiclasse com categorias codificadas em colunas e pesos por classe.
- `ordinal_catboost`: quatro classificadores binários cumulativos (`P≥2` até `P≥5`), convertidos em probabilidades P1–P5; este método considera a ordem entre padrões.

## Recomendação

Use **{winner.model}** como candidato principal: ele obteve o maior F1 macro ({winner.macro_f1_present_classes:.3f}) no teste temporal, equilibrando melhor os padrões presentes do que os demais métodos. Antes de implantação, repetir este teste a cada nova competência ITBI e acompanhar as métricas por bairro e por padrão.

Os resultados medem generalização para meses futuros dentro dos bairros-alvo; não medem ainda desempenho contra preços anunciados. Eles também não permitem avaliar P1 neste teste, pois não houve exemplos P1 nas competências de teste.
"""


def run_comparison(
    config_path: str | Path,
    output_dir: str | Path | None = None,
    train_scope: str = "all",
) -> dict[str, object]:
    if train_scope not in {"all", "target"}:
        raise ValueError("train_scope deve ser 'all' ou 'target'")
    config = Path(config_path).resolve()
    root = config.parent
    output = Path(output_dir).resolve() if output_dir else (
        root / "investigacao" / "resultados" / "bairros_alvo" if train_scope == "target"
        else root / "investigacao" / "resultados"
    )
    history, _ = read_itbi_sources(root / "data" / "itbi")
    targets = load_target_neighborhoods(config)
    splits, _, final_test_months = build_splits(history, targets)
    split = next(item for item in splits if item.name == "historico_sem_preco")
    if train_scope == "target":
        target_history = history[history["neighborhood"].isin(targets)].copy()
        split = type(split)(
            "historico_bairros_alvo_sem_preco",
            target_history[target_history["competence"] < final_test_months[0]].copy(),
            split.test,
            False,
            True,
        )
    train, test, coverage = _bucket_streets(split.train, split.test, STREET_THRESHOLD)
    methods = {
        "random_forest": random_forest_predict,
        "catboost": catboost_predict,
        "xgboost": xgboost_predict,
        "ordinal_catboost": ordinal_catboost_predict,
    }
    summaries: list[dict[str, object]] = []
    pattern_metrics: list[pd.DataFrame] = []
    matrices: list[pd.DataFrame] = []
    for name, predict in methods.items():
        predicted = predict(train, test)
        summary, by_pattern, matrix = _evaluate(name, test["finish_pattern"], predicted)
        summaries.append(summary)
        pattern_metrics.append(by_pattern)
        matrices.append(matrix)
    comparison = pd.DataFrame(summaries).sort_values("macro_f1_present_classes", ascending=False)
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(comparison, output / "comparacao_metodos_sem_preco.csv")
    _write_csv(pd.concat(pattern_metrics, ignore_index=True), output / "metricas_metodos_sem_preco.csv")
    _write_csv(pd.concat(matrices, ignore_index=True), output / "matrizes_confusao_metodos_sem_preco.csv")
    _write_csv(coverage, output / "cobertura_ruas_metodos_sem_preco.csv")
    report = _report(comparison, final_test_months, len(train), len(test), train_scope)
    (output / "relatorio_metodos_sem_preco.md").write_text(report, encoding="utf-8")
    manifest = {
        "features": ["neighborhood", "street_model", "area_m2"],
        "street_threshold": STREET_THRESHOLD,
        "train_records": len(train),
        "test_records": len(test),
        "test_months": final_test_months,
        "train_scope": train_scope,
        "seed": SEED,
        "methods": list(methods),
        "parameters": {
            "random_forest": {"n_estimators": FINAL_N_ESTIMATORS, "min_samples_leaf": 2},
            "catboost": {"iterations": 350, "depth": 8, "learning_rate": 0.06},
            "xgboost": {"n_estimators": 350, "max_depth": 8, "learning_rate": 0.06},
            "ordinal_catboost": {"boundaries": [2, 3, 4, 5], "iterations_per_boundary": 220},
        },
    }
    (output / "manifesto_metodos_sem_preco.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"output_dir": str(output), "winner": str(comparison.iloc[0]["model"]), "models": len(methods)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Compara modelos ITBI sem preco")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--output-dir")
    parser.add_argument("--train-scope", choices=["all", "target"], default="all")
    args = parser.parse_args()
    print(json.dumps(run_comparison(args.config, args.output_dir, args.train_scope), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
