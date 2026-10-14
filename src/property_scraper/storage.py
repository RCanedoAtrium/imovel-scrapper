from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

# CSVs are intended to be opened directly in Brazilian Excel installations.
# The BOM makes UTF-8 recognizable by Excel; semicolon and decimal comma avoid
# locale-dependent column and number parsing.
EXCEL_CSV_OPTIONS = {
    "sep": ";",
    "decimal": ",",
    "encoding": "utf-8-sig",
}


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(temporary, path)


def write_csv(df: pd.DataFrame, path: Path, *, index: bool = False) -> Path:
    """Write a CSV that opens correctly in Excel configured for pt-BR."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(temporary, index=index, **EXCEL_CSV_OPTIONS)
    os.replace(temporary, path)
    return path


def read_csv_auto(path: Path, **kwargs: Any) -> pd.DataFrame:
    """Read both current Excel-compatible CSVs and legacy comma-separated runs."""
    with path.open("r", encoding="utf-8-sig", errors="replace") as f:
        first_line = f.readline()
    separator = ";" if first_line.count(";") > first_line.count(",") else ","
    kwargs.setdefault("decimal", "," if separator == ";" else ".")
    return pd.read_csv(path, sep=separator, encoding="utf-8-sig", **kwargs)


def write_dataset(df: pd.DataFrame, path: Path, metadata: dict[str, Any]) -> tuple[Path, Path]:
    """Write a canonical dataset as CSV plus its audit manifest."""
    if path.suffix.lower() != ".csv":
        raise ValueError(f"Datasets canonicos devem usar a extensao .csv: {path}")
    write_csv(df, path)
    manifest = {
        "dataset": path.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": len(df),
        "columns": list(df.columns),
        "sha256": sha256_file(path),
        **metadata,
    }
    manifest_path = path.with_suffix(".manifest.json")
    atomic_json(manifest_path, manifest)
    return path, manifest_path
