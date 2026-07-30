from __future__ import annotations

import re
from typing import TypedDict


class ParkingClassification(TypedDict):
    parking_type: str
    parking_evidence: str
    parking_confidence: str
    parking_review_status: str


_SPACE_WORD = r"(?:vaga(?:s)?|garagem|estacionamento)"
_FREE_WORD = r"(?:livre(?:s)?|independente(?:s)?|paralela(?:s)?|em\s+paralelo)"
_STACKED_WORD = r"(?:presa(?:s)?|em\s+linha|em\s+fila)"
_NO_SPACE_WORD = r"(?:sem\s+(?:vaga(?:s)?|garagem)|n[aã]o\s+possui\s+(?:vaga(?:s)?|garagem))"


def _excerpt(text: str, match: re.Match[str]) -> str:
    """Keep a short, human-readable piece of the original listing text."""
    start = max(0, match.start() - 45)
    end = min(len(text), match.end() + 70)
    return re.sub(r"\s+", " ", text[start:end]).strip(" ,;:-")


def _find_evidence(text: str, qualifier: str) -> tuple[str, bool]:
    patterns = (
        rf"{_SPACE_WORD}.{{0,70}}?{qualifier}",
        rf"{qualifier}.{{0,70}}?{_SPACE_WORD}",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _excerpt(text, match), True
    return "", False


def classify_parking(text: object) -> ParkingClassification:
    """Classify parking usability from the listing text without guessing.

    ``em linha`` takes precedence over ``livre``. Listings frequently use both
    terms together, but tandem spaces still do not satisfy the independent-space
    requirement used by the investment criteria.
    """
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return {
            "parking_type": "unknown",
            "parking_evidence": "",
            "parking_confidence": "low",
            "parking_review_status": "pending_review",
        }

    no_space = re.search(_NO_SPACE_WORD, normalized, flags=re.IGNORECASE)
    if no_space:
        return {
            "parking_type": "no_space",
            "parking_evidence": _excerpt(normalized, no_space),
            "parking_confidence": "high",
            "parking_review_status": "rejected",
        }

    stacked_evidence, has_stacked = _find_evidence(normalized, _STACKED_WORD)
    free_evidence, has_free = _find_evidence(normalized, _FREE_WORD)
    if has_stacked and has_free:
        parking_type = "tandem" if re.search(r"em\s+linha", stacked_evidence, re.I) else "mixed"
        return {
            "parking_type": parking_type,
            "parking_evidence": stacked_evidence,
            "parking_confidence": "high",
            "parking_review_status": "rejected",
        }
    if has_stacked:
        return {
            "parking_type": "tandem" if re.search(r"em\s+linha", stacked_evidence, re.I) else "blocked",
            "parking_evidence": stacked_evidence,
            "parking_confidence": "high",
            "parking_review_status": "rejected",
        }
    if has_free:
        return {
            "parking_type": "independent",
            "parking_evidence": free_evidence,
            "parking_confidence": "high",
            "parking_review_status": "approved",
        }
    return {
        "parking_type": "unknown",
        "parking_evidence": "",
        "parking_confidence": "low",
        "parking_review_status": "pending_review",
    }
