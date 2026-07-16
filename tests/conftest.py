import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def project(tmp_path):
    config = {
        "neighborhoods": ["ANCHIETA"],
        "sites": ["vivareal"],
        "min_transactions": 5,
        "classifier": "heuristics",
        "itbi_history_start": "2026-01",
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return tmp_path, config_path
