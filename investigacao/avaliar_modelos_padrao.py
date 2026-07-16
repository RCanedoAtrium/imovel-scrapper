"""Experimento offline para prever o padrao de acabamento a partir do ITBI.

Este modulo e propositalmente independente do pacote ``imovel_scrapper``. Ele
le os CSVs brutos do ITBI e escreve todos os seus artefatos em investigacao.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_recall_fscore_support,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder


PATTERNS = ["P1", "P2", "P3", "P4", "P5"]
STREET_THRESHOLDS = [5, 10, 20, 30, 50]
SEED = 42
FINAL_N_ESTIMATORS = 150
SELECTION_N_ESTIMATORS = 50
CSV_KWARGS = {"sep": ";", "encoding": "utf-8-sig", "index": False}


@dataclass(frozen=True)
class ExperimentSplit:
    name: str
    train: pd.DataFrame
    test: pd.DataFrame
    use_price: bool
    temporal: bool


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value)
    return "".join(char for char in normalized if unicodedata.category(char) != "Mn")


def normalize_column(value: object) -> str:
    return _strip_accents(str(value).replace("\ufeff", "").strip().upper())


def normalize_neighborhood(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return _strip_accents(str(value).strip().upper())


def normalize_street(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = _strip_accents(str(value).strip().upper())
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(
        r"^(RUA\b|R\b|AVENIDA\b|AVE\b|AV\b|ALAMEDA\b|AL\b|TRAVESSA\b|TV\b|PRACA\b|PC\b|RODOVIA\b|ROD\b)\s+",
        "",
        text,
    )
    return re.sub(r"\s+", " ", text).strip()


def clean_numeric(value: object) -> float:
    if value is None or pd.isna(value):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[^\d,.-]", "", str(value).strip())
    if not text:
        return 0.0
    if "," in text:
        integer, decimal, *_ = text.split(",") + [""]
        text = f"{integer.replace('.', '')}.{decimal}"
    elif text.count(".") > 1 or ("." in text and len(text.rsplit(".", 1)[-1]) == 3):
        text = text.replace(".", "")
    try:
        return float(text)
    except ValueError:
        return 0.0


def competence_from_name(path: Path) -> str | None:
    match = re.search(r"(20\d{2})(0[1-9]|1[0-2])", path.name)
    return f"{match.group(1)}-{match.group(2)}" if match else None


def _read_csv(path: Path) -> pd.DataFrame:
    first_line = path.open("r", encoding="utf-8-sig", errors="replace").readline()
    separator = ";" if first_line.count(";") > first_line.count(",") else ","
    errors: list[Exception] = []
    for encoding in ("utf-8-sig", "latin1"):
        try:
            return pd.read_csv(path, sep=separator, encoding=encoding, dtype=str, low_memory=False)
        except (UnicodeDecodeError, pd.errors.ParserError) as exc:
            errors.append(exc)
    raise ValueError(f"Nao foi possivel ler {path}: {errors}")


def _address_street(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    first = str(value).split("-")[0].strip()
    match = re.match(r"(.+?)\s+\d{1,6}\s*$", first)
    return normalize_street(match.group(1) if match else first)


def _canonical_columns(frame: pd.DataFrame) -> pd.DataFrame:
    aliases = {
        "ENDERECO COMPLETO": "ENDERECO",
        "ANO DE CONSTRUCAO UNIDADE": "ANO DE CONSTRUCAO (UNIDADE)",
        "PADRAO ACABAMENTO UNIDADE": "PADRAO ACABAMENTO (UNIDADE)",
        "DESCRICAO TIPO OCUPACAO UNIDADE": "DESCRICAO TIPO OCUPACAO (UNIDADE)",
    }
    frame = frame.copy()
    frame.columns = [aliases.get(normalize_column(column), normalize_column(column)) for column in frame.columns]
    return frame


def read_itbi_sources(itbi_dir: Path, start_competence: str = "2024-01") -> tuple[pd.DataFrame, list[str]]:
    rows: list[pd.DataFrame] = []
    sources: list[str] = []
    for path in sorted(itbi_dir.glob("*.csv")):
        competence = competence_from_name(path)
        if not competence or competence < start_competence:
            continue
        raw = _canonical_columns(_read_csv(path))
        required = {
            "AREA CONSTRUIDA ADQUIRIDA", "VALOR DECLARADO", "VALOR BASE CALCULO",
            "BAIRRO", "DESCRICAO TIPO OCUPACAO (UNIDADE)",
        }
        missing = sorted(required - set(raw.columns))
        if missing:
            raise ValueError(f"{path.name}: colunas obrigatorias ausentes: {', '.join(missing)}")
        occupation = raw["DESCRICAO TIPO OCUPACAO (UNIDADE)"].fillna("").str.upper()
        raw = raw[occupation == "RESIDENCIAL"].copy()
        raw["area_m2"] = raw["AREA CONSTRUIDA ADQUIRIDA"].map(clean_numeric)
        declared = raw["VALOR DECLARADO"].map(clean_numeric)
        base = raw["VALOR BASE CALCULO"].map(clean_numeric)
        raw["transaction_price"] = pd.concat([declared, base], axis=1).max(axis=1)
        raw["neighborhood"] = raw["BAIRRO"].map(normalize_neighborhood)
        raw["street"] = raw.get("ENDERECO", pd.Series("", index=raw.index)).map(_address_street)
        raw["finish_pattern"] = raw.get(
            "PADRAO ACABAMENTO (UNIDADE)", pd.Series("", index=raw.index)
        ).fillna("").str.upper().str.strip()
        raw = raw[
            (raw["area_m2"] > 10)
            & (raw["transaction_price"] > 20_000)
            & (raw["neighborhood"] != "")
            & (raw["finish_pattern"].isin(PATTERNS))
        ].copy()
        raw["competence"] = competence
        rows.append(raw[["competence", "neighborhood", "street", "area_m2", "transaction_price", "finish_pattern"]])
        sources.append(path.name)
    if not rows:
        raise ValueError(f"Nenhum CSV ITBI valido encontrado em {itbi_dir}")
    return pd.concat(rows, ignore_index=True), sources


def load_target_neighborhoods(config_path: Path) -> list[str]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    neighborhoods = [normalize_neighborhood(value) for value in payload.get("neighborhoods", [])]
    if not neighborhoods:
        raise ValueError("config nao possui neighborhoods")
    return sorted(set(value for value in neighborhoods if value))


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, **CSV_KWARGS)


def _bucket_streets(train: pd.DataFrame, test: pd.DataFrame, threshold: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    train = train.copy()
    test = test.copy()
    train_raw = train["street"].fillna("").replace("", "SEM_RUA")
    test_raw = test["street"].fillna("").replace("", "SEM_RUA")
    counts = train_raw.value_counts()
    frequent = set(counts[counts >= threshold].index)
    known = set(counts.index)

    def bucket(values: pd.Series) -> pd.Series:
        return values.map(lambda value: value if value in frequent or value == "SEM_RUA" else "RUA_RARA")

    train["street_model"] = bucket(train_raw)
    test["street_model"] = bucket(test_raw)
    status = test_raw.map(
        lambda value: "ausente" if value == "SEM_RUA" else (
            "frequente" if value in frequent else ("rara" if value in known else "inedita")
        )
    )
    coverage = status.value_counts().reindex(["frequente", "rara", "ausente", "inedita"], fill_value=0).rename_axis("street_status").reset_index(name="records")
    coverage["share"] = coverage["records"] / len(test) if len(test) else 0.0
    return train, test, coverage


def _features(frame: pd.DataFrame, use_street: bool, use_price: bool) -> tuple[pd.DataFrame, list[str], list[str]]:
    output = frame.copy()
    categorical = ["neighborhood"]
    if use_street:
        categorical.append("street_model")
    numeric = ["area_m2"]
    if use_price:
        output["log_transaction_price"] = output["transaction_price"].map(math.log1p)
        numeric.append("log_transaction_price")
    return output[categorical + numeric], categorical, numeric


def _fit_predict(
    train: pd.DataFrame,
    test: pd.DataFrame,
    threshold: int,
    use_street: bool,
    use_price: bool,
    n_estimators: int = FINAL_N_ESTIMATORS,
) -> tuple[pd.Series, pd.DataFrame]:
    train, test, coverage = _bucket_streets(train, test, threshold)
    x_train, categorical, numeric = _features(train, use_street, use_price)
    x_test, _, _ = _features(test, use_street, use_price)
    preprocessor = ColumnTransformer(
        [("categorical", OneHotEncoder(handle_unknown="ignore"), categorical), ("numeric", "passthrough", numeric)]
    )
    model = RandomForestClassifier(
        n_estimators=n_estimators, random_state=SEED, class_weight="balanced_subsample", min_samples_leaf=2, n_jobs=-1
    )
    pipeline = Pipeline([("features", preprocessor), ("model", model)])
    pipeline.fit(x_train, train["finish_pattern"])
    return pd.Series(pipeline.predict(x_test), index=test.index), coverage


def _macro_f1_present(y_true: pd.Series, y_pred: pd.Series) -> float:
    labels = sorted(set(y_true))
    return float(f1_score(y_true, y_pred, labels=labels, average="macro", zero_division=0))


def select_street_threshold(train: pd.DataFrame, use_price: bool) -> tuple[int, pd.DataFrame]:
    months = sorted(train["competence"].unique())
    results: list[dict[str, object]] = []
    if len(months) < 4:
        return 20, pd.DataFrame([{"threshold": 20, "mean_macro_f1": None, "folds": 0, "reason": "competencias_insuficientes"}])
    validation_months = months[-min(3, len(months) - 1):]
    for threshold in STREET_THRESHOLDS:
        scores: list[float] = []
        for validation_month in validation_months:
            fit = train[train["competence"] < validation_month]
            validate = train[train["competence"] == validation_month]
            if fit["finish_pattern"].nunique() < 2 or validate.empty:
                continue
            predicted, _ = _fit_predict(
                fit, validate, threshold, use_street=True, use_price=use_price,
                n_estimators=SELECTION_N_ESTIMATORS,
            )
            scores.append(_macro_f1_present(validate["finish_pattern"], predicted))
        results.append({
            "threshold": threshold,
            "mean_macro_f1": sum(scores) / len(scores) if scores else None,
            "folds": len(scores),
            "reason": "temporal_internal",
        })
    table = pd.DataFrame(results)
    valid = table.dropna(subset=["mean_macro_f1"])
    if valid.empty:
        return 20, table
    best = valid.sort_values(["mean_macro_f1", "threshold"], ascending=[False, True]).iloc[0]
    return int(best["threshold"]), table


def _metrics(name: str, y_true: pd.Series, y_pred: pd.Series, threshold: int, split: ExperimentSplit, use_street: bool) -> tuple[dict[str, object], pd.DataFrame, pd.DataFrame]:
    precision, recall, f1, support = precision_recall_fscore_support(y_true, y_pred, labels=PATTERNS, zero_division=0)
    per_pattern = pd.DataFrame({
        "model": name, "pattern": PATTERNS, "precision": precision, "recall": recall, "f1": f1, "support": support,
        "insufficient_support": support < 5,
    })
    matrix = pd.DataFrame(
        confusion_matrix(y_true, y_pred, labels=PATTERNS), index=PATTERNS, columns=PATTERNS
    ).rename_axis("actual").reset_index()
    matrix.insert(0, "model", name)
    supported_recalls = recall[support > 0]
    return {
        "model": name,
        "split": split.name,
        "temporal": split.temporal,
        "uses_price": split.use_price,
        "uses_street": use_street,
        "street_threshold": threshold if use_street else None,
        "train_records": len(split.train),
        "test_records": len(split.test),
        "macro_f1_present_classes": _macro_f1_present(y_true, y_pred),
        "balanced_accuracy": float(supported_recalls.mean()) if len(supported_recalls) else 0.0,
        "accuracy": float(accuracy_score(y_true, y_pred)),
    }, per_pattern, matrix


def _random_split(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    from sklearn.model_selection import train_test_split

    counts = frame["finish_pattern"].value_counts()
    stratify = frame["finish_pattern"] if (counts >= 2).all() else None
    return train_test_split(frame, test_size=0.25, random_state=SEED, stratify=stratify)


def build_splits(history: pd.DataFrame, target_neighborhoods: Iterable[str]) -> tuple[list[ExperimentSplit], list[str], list[str]]:
    target = history[history["neighborhood"].isin(set(target_neighborhoods))].copy()
    months = sorted(target["competence"].unique())
    if len(months) < 12:
        raise ValueError(f"Sao necessarias 12 competencias nos bairros-alvo; encontradas {len(months)}")
    latest = months[-12:]
    final_test_months = latest[-3:]
    temporal_train_months = latest[:-3]
    final_test = target[target["competence"].isin(final_test_months)].copy()
    historic_train = history[history["competence"] < final_test_months[0]].copy()
    temporal_train = target[target["competence"].isin(temporal_train_months)].copy()
    random_train, random_test = _random_split(target[target["competence"].isin(latest)].copy())
    if final_test.empty or temporal_train.empty or historic_train.empty:
        raise ValueError("Divisoes de treino/teste vazias")
    return [
        ExperimentSplit("historico_sem_preco", historic_train, final_test, False, True),
        ExperimentSplit("temporal_sem_preco", temporal_train, final_test, False, True),
        ExperimentSplit("temporal_com_preco", temporal_train, final_test, True, True),
        ExperimentSplit("aleatorio_com_preco", random_train, random_test, True, False),
    ], latest, final_test_months


def run_investigation(config_path: str | Path, output_dir: str | Path | None = None) -> dict[str, object]:
    config = Path(config_path).resolve()
    root = config.parent
    output = Path(output_dir).resolve() if output_dir else root / "investigacao" / "resultados"
    neighborhoods = load_target_neighborhoods(config)
    history, source_files = read_itbi_sources(root / "data" / "itbi")
    splits, latest_months, final_test_months = build_splits(history, neighborhoods)
    target = history[history["neighborhood"].isin(neighborhoods)].copy()
    exported_columns = ["competence", "neighborhood", "street", "area_m2", "transaction_price", "finish_pattern"]
    _write_csv(history[exported_columns], output / "itbi_historico_desde_2024.csv")
    _write_csv(target[target["competence"].isin(latest_months)][exported_columns], output / "itbi_bairros_alvo_ultimos_12_meses.csv")

    comparison: list[dict[str, object]] = []
    patterns: list[pd.DataFrame] = []
    matrices: list[pd.DataFrame] = []
    threshold_tables: list[pd.DataFrame] = []
    coverage_tables: list[pd.DataFrame] = []
    for split in splits:
        threshold, search = select_street_threshold(split.train, split.use_price)
        search.insert(0, "model", split.name)
        threshold_tables.append(search)
        for use_street, suffix in ((True, "com_rua"), (False, "baseline_sem_rua")):
            name = f"{split.name}_{suffix}"
            prediction, coverage = _fit_predict(split.train, split.test, threshold, use_street, split.use_price)
            summary, by_pattern, matrix = _metrics(name, split.test["finish_pattern"], prediction, threshold, split, use_street)
            comparison.append(summary)
            patterns.append(by_pattern)
            matrices.append(matrix)
            if use_street:
                coverage.insert(0, "model", name)
                coverage_tables.append(coverage)

    _write_csv(pd.DataFrame(comparison), output / "comparacao_modelos.csv")
    _write_csv(pd.concat(patterns, ignore_index=True), output / "metricas_por_padrao.csv")
    _write_csv(pd.concat(matrices, ignore_index=True), output / "matrizes_confusao.csv")
    _write_csv(pd.concat(threshold_tables, ignore_index=True), output / "busca_limiar_rua.csv")
    _write_csv(pd.concat(coverage_tables, ignore_index=True), output / "cobertura_ruas.csv")
    manifest = {
        "source_files": source_files,
        "target_neighborhoods": neighborhoods,
        "latest_12_months": latest_months,
        "final_test_months": final_test_months,
        "filters": {"start_competence": "2024-01", "occupation": "RESIDENCIAL", "min_area_m2_exclusive": 10, "min_transaction_price_exclusive": 20000},
        "features": {"without_price": ["neighborhood", "street", "area_m2"], "with_price": ["neighborhood", "street", "area_m2", "log_transaction_price"]},
        "street_thresholds": STREET_THRESHOLDS,
        "seed": SEED,
        "random_forest": {
            "n_estimators": FINAL_N_ESTIMATORS,
            "selection_n_estimators": SELECTION_N_ESTIMATORS,
            "class_weight": "balanced_subsample",
            "min_samples_leaf": 2,
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "manifesto.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"output_dir": str(output), "models": len(comparison), "records": len(history), "target_records": len(target)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Investiga Random Forest para padrao de acabamento ITBI")
    parser.add_argument("--config", default="config.json", help="JSON com bairros-alvo")
    parser.add_argument("--output-dir", help="Diretorio de resultados; padrao: investigacao/resultados")
    args = parser.parse_args()
    result = run_investigation(args.config, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
