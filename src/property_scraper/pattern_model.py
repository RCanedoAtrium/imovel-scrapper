from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, f1_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from .errors import DataQualityError
from .normalization import normalize_neighborhood, normalize_street

VALID_PATTERNS = {"P1", "P2", "P3", "P4", "P5"}
MODEL_VERSION = "random-forest-no-price-v2"
RARE_STREET = "RARE_STREET"
STREET_THRESHOLD = 20
N_ESTIMATORS = 300
RANDOM_SEED = 42


@dataclass(frozen=True)
class PatternPrediction:
    pattern: str
    confidence: float
    method: str = "itbi_random_forest_sem_preco"


class RandomForestPatternModel:
    """Optimized finishing-pattern model that deliberately excludes price."""

    feature_columns = ["neighborhood", "street_model", "area_m2"]

    def __init__(self, transactions: pd.DataFrame):
        self.version = MODEL_VERSION
        self.frequent_streets: set[str] = set()
        self.pipeline: Pipeline | None = None
        self.metadata: dict[str, Any] = {}
        self._fit(transactions)

    @staticmethod
    def _training_frame(transactions: pd.DataFrame) -> pd.DataFrame:
        required = {"neighborhood", "street", "area_m2", "finish_pattern"}
        missing = sorted(required.difference(transactions.columns))
        if missing:
            raise DataQualityError("Missing columns for Random Forest training: " + ", ".join(missing))
        train = transactions[list(required | ({"competence"} & set(transactions.columns)))].copy()
        train["neighborhood"] = train["neighborhood"].map(normalize_neighborhood)
        train["street"] = train["street"].map(normalize_street)
        train["area_m2"] = pd.to_numeric(train["area_m2"], errors="coerce")
        train["finish_pattern"] = train["finish_pattern"].astype(str).str.upper().str.strip()
        return train[
            train["finish_pattern"].isin(VALID_PATTERNS)
            & train["area_m2"].gt(10)
            & train["neighborhood"].ne("")
        ].dropna(subset=["area_m2"])

    def _fit(self, transactions: pd.DataFrame) -> None:
        train = self._training_frame(transactions)
        if len(train) <= 10 or train["finish_pattern"].nunique() < 2:
            raise DataQualityError("Insufficient data to train the finish-pattern Random Forest")

        street_counts = train.loc[train["street"].ne(""), "street"].value_counts()
        self.frequent_streets = set(street_counts[street_counts >= STREET_THRESHOLD].index)
        train["street_model"] = train["street"].where(train["street"].isin(self.frequent_streets), RARE_STREET)

        leaf_samples = max(1, min(5, len(train) // 4))
        preprocessor = ColumnTransformer(
            [
                (
                    "categorical",
                    OneHotEncoder(handle_unknown="ignore"),
                    ["neighborhood", "street_model"],
                ),
                ("numeric", "passthrough", ["area_m2"]),
            ]
        )
        forest = RandomForestClassifier(
            n_estimators=N_ESTIMATORS,
            random_state=RANDOM_SEED,
            class_weight="balanced_subsample",
            min_samples_leaf=leaf_samples,
            max_features="sqrt",
            n_jobs=-1,
        )
        self.pipeline = Pipeline([("preprocessor", preprocessor), ("classifier", forest)])

        # Cross-validation performance assessment
        min_class_count = int(train["finish_pattern"].value_counts().min())
        n_splits = min(5, min_class_count)
        cv_accuracy = None
        cv_f1_macro = None
        per_class_f1 = {}
        per_class_precision = {}
        per_class_recall = {}
        if n_splits >= 2:
            skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=RANDOM_SEED)
            cv_predictions = cross_val_predict(
                self.pipeline,
                train[self.feature_columns],
                train["finish_pattern"],
                cv=skf,
                n_jobs=1,
            )
            cv_accuracy = float(accuracy_score(train["finish_pattern"], cv_predictions))
            cv_f1_macro = float(f1_score(train["finish_pattern"], cv_predictions, average="macro", zero_division=0))
            report = classification_report(train["finish_pattern"], cv_predictions, output_dict=True, zero_division=0)
            for cls in sorted(train["finish_pattern"].unique()):
                cls_str = str(cls)
                if cls_str in report:
                    per_class_f1[cls_str] = float(report[cls_str]["f1-score"])
                    per_class_precision[cls_str] = float(report[cls_str]["precision"])
                    per_class_recall[cls_str] = float(report[cls_str]["recall"])

        self.pipeline.fit(train[self.feature_columns], train["finish_pattern"])
        # Analysis predicts one listing at a time. Keeping a worker pool for a
        # single row adds overhead and emits one joblib warning per listing.
        forest.n_jobs = 1
        competences = train.get("competence", pd.Series(dtype=str)).dropna().astype(str)
        self.metadata = {
            "version": self.version,
            "features": ["neighborhood", "street", "area_m2"],
            "price_features_used": False,
            "training_rows": len(train),
            "training_start": competences.min() if not competences.empty else "",
            "training_end": competences.max() if not competences.empty else "",
            "classes": sorted(train["finish_pattern"].unique()),
            "frequent_streets": len(self.frequent_streets),
            "street_threshold": STREET_THRESHOLD,
            "n_estimators": N_ESTIMATORS,
            "min_samples_leaf": leaf_samples,
            "max_features": "sqrt",
            "class_weight": "balanced_subsample",
            "random_seed": RANDOM_SEED,
            "cv_accuracy": cv_accuracy,
            "cv_f1_macro": cv_f1_macro,
            "per_class_f1": per_class_f1,
            "per_class_precision": per_class_precision,
            "per_class_recall": per_class_recall,
        }

    def predict(self, neighborhood: object, street: object, area_m2: object) -> PatternPrediction:
        return self.predict_batch(pd.DataFrame([{
            "neighborhood": neighborhood,
            "street": street,
            "area_m2": area_m2,
        }]))[0]

    def predict_batch(self, listings: pd.DataFrame) -> list[PatternPrediction]:
        if self.pipeline is None:
            raise DataQualityError("Random Forest has not been trained")
        frame = listings[["neighborhood", "street", "area_m2"]].copy()
        frame["neighborhood"] = frame["neighborhood"].map(normalize_neighborhood)
        normalized_streets = frame["street"].map(normalize_street)
        frame["street_model"] = normalized_streets.where(
            normalized_streets.isin(self.frequent_streets), RARE_STREET
        )
        frame["area_m2"] = pd.to_numeric(frame["area_m2"], errors="coerce").fillna(0.0)
        model_frame = frame[self.feature_columns]
        patterns = self.pipeline.predict(model_frame).astype(str)
        probabilities = self.pipeline.predict_proba(model_frame)
        classifier = self.pipeline.named_steps["classifier"]
        class_indexes = {str(pattern): index for index, pattern in enumerate(classifier.classes_)}
        confidences = np.asarray([
            probabilities[row, class_indexes[pattern]] for row, pattern in enumerate(patterns)
        ])
        return [
            PatternPrediction(pattern=pattern, confidence=float(confidence))
            for pattern, confidence in zip(patterns, confidences)
        ]
