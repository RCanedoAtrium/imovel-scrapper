from __future__ import annotations

import ast
import re
import unicodedata
from typing import Any

import pandas as pd


def strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value)
    return "".join(char for char in normalized if unicodedata.category(char) != "Mn")


def normalize_column_name(value: Any) -> str:
    return strip_accents(str(value).strip().replace("\ufeff", "")).upper()


def normalize_neighborhood(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return strip_accents(str(value).strip().upper())


def normalize_street(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    street = strip_accents(str(value).strip().upper())
    street = re.sub(r"[^\w\s]", " ", street)
    street = re.sub(
        r"^(RUA\b|R\b|AVENIDA\b|AVE\b|AV\b|ALAMEDA\b|AL\b|TRAVESSA\b|TV\b|PRACA\b|PC\b|RODOVIA\b|ROD\b)\s+",
        "",
        street,
    )
    return re.sub(r"\s+", " ", street).strip()


def normalize_cep(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    digits = re.sub(r"\D", "", str(value))
    return digits if len(digits) == 8 else ""


def normalize_number(value: Any) -> str:
    number = clean_numeric(value)
    return str(int(number)) if number > 0 else ""


def clean_numeric(value: Any) -> float:
    if value is None or pd.isna(value):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    text = re.split(r"\s+(?:a|-)\s+", text, maxsplit=1)[0]
    text = re.sub(r"[^\d,.-]", "", text)
    if not text:
        return 0.0
    if "," in text:
        integer, decimal, *_ = text.split(",") + [""]
        text = f"{integer.replace('.', '')}.{decimal}"
    elif "." in text and (text.count(".") > 1 or len(text.rsplit(".", 1)[-1]) == 3):
        text = text.replace(".", "")
    try:
        return float(text)
    except ValueError:
        return 0.0


def extract_currency(value: Any) -> float:
    if value is None or pd.isna(value):
        return 0.0
    match = re.search(r"R\$\s*([\d.]+(?:,\d{1,2})?)", str(value), flags=re.IGNORECASE)
    return clean_numeric(match.group(1)) if match else clean_numeric(value)


def extract_area_m2(value: Any) -> float:
    if value is None or pd.isna(value):
        return 0.0
    text = str(value)
    numeric = r"\d+(?:[.,]\s*\d+)?"
    # Priority 1: explicit util or privativa
    util_match = re.search(rf"({numeric})\s*m[²2]\s*(?:[úu]til|privativ)", text, re.I)
    if util_match:
        return clean_numeric(util_match.group(1))
    # Priority 2: range
    range_match = re.search(rf"({numeric})\s*(?:-|a)\s*{numeric}\s*m[²2]", text, re.I)
    if range_match:
        return clean_numeric(range_match.group(1))
    # Priority 3: clean m2 without total/terreno immediately after
    clean_match = re.search(rf"({numeric})\s*m[²2](?!\s*(?:tot|total|terreno))", text, re.I)
    if clean_match:
        return clean_numeric(clean_match.group(1))
    # Priority 4: any m2
    match = re.search(rf"({numeric})\s*m[²2]", text, re.I)
    if match:
        return clean_numeric(match.group(1))
    return clean_numeric(value) if re.fullmatch(r"\s*[\d.,]+\s*", text) else 0.0


TYPOLOGY_AREA_LIMIT_M2 = 300
# Only these terms justify a large unit (GEMINI.md §1). Words such as CASA,
# TERRENO or LOTE describe a different property type and must not exempt it.
LARGE_UNIT_KEYWORDS = ("COBERTURA", "PRIVATIVA", "AREA PRIVATIVA", "DUPLEX", "TRIPLEX", "GARDEN", "GARDEM")
_BEDROOMS_IN_TEXT = re.compile(r"(\d+)\s*(?:quartos?|dormitorios?)\b", re.I)


def validate_typology_coherence(
    title: str,
    area_m2: float,
    bedrooms: float = 0.0,
    area_limit: float = TYPOLOGY_AREA_LIMIT_M2,
) -> tuple[bool, str]:
    """Validates whether an apartment area is coherent with its title description.

    Conventional apartments (2 to 4 bedrooms) > area_limit m² without keywords
    indicating coverage, private area, or duplex/garden are flagged to avoid
    land/total-area distortions. When the bedroom count is unknown, the unit is
    treated as conventional so the check stays conservative.
    """
    if area_m2 <= area_limit:
        return True, ""
    normalized_title = strip_accents(str(title or "")).upper()
    rooms = int(bedrooms or 0)
    if rooms <= 0:
        match = _BEDROOMS_IN_TEXT.search(normalized_title)
        rooms = int(match.group(1)) if match else 0
    if rooms and not 2 <= rooms <= 4:
        return True, ""
    for kw in LARGE_UNIT_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", normalized_title):
            return True, ""
    return False, "area_mismatch_warning"


def detect_area_warning(
    area_text: Any,
    title: str,
    area_m2: float,
    bedrooms: float = 0.0,
    area_limit: float = TYPOLOGY_AREA_LIMIT_M2,
) -> str:
    """Returns an area warning tag if area is suspicious, unverified, or mismatched."""
    text = str(area_text or "")
    if re.search(r"m[²2]\s*(?:tot|total|terreno)", text, re.I) and not re.search(r"m[²2]\s*(?:[úu]til|privativ)", text, re.I):
        return "area_total_only"
    is_coherent, warning = validate_typology_coherence(title, area_m2, bedrooms, area_limit=area_limit)
    if not is_coherent:
        return warning
    return ""


def _region_tokens(value: Any) -> str:
    """Accent-free upper-case text where URL/slug separators become spaces."""
    text = strip_accents(str(value or "")).upper()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9]+", " ", text)).strip()


def find_excluded_region(text: Any, regions: list[str]) -> str:
    """Return the first out-of-area region mentioned in ``text`` (GEMINI.md §3)."""
    haystack = f" {_region_tokens(text)} "
    for region in regions:
        token = _region_tokens(region)
        if token and f" {token} " in haystack:
            return token
    return ""


def safe_literal(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not ((text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]"))):
        return value
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return value


def extract_nested(record: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in record and not _is_missing(record[key]):
            return safe_literal(record[key])
        current: Any = record
        found = True
        for part in key.split("."):
            current = safe_literal(current)
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                found = False
                break
        if found and not _is_missing(current):
            return safe_literal(current)
    return None


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        result = pd.isna(value)
        return bool(result) if not hasattr(result, "__len__") else False
    except (TypeError, ValueError):
        return False


def parse_listing_address(title: str) -> tuple[str, str, str]:
    if not isinstance(title, str) or " em " not in title.lower():
        return "", "", ""
    parts = re.split(r"\s+em\s+", title, flags=re.IGNORECASE)
    address = parts[-1].strip()
    cep_match = re.search(r"\b\d{5}-?\d{3}\b", address)
    cep = normalize_cep(cep_match.group(0)) if cep_match else ""
    if cep_match:
        address = address.replace(cep_match.group(0), "")
    address = re.sub(r"\b(Belo Horizonte|MG)\b", "", address, flags=re.IGNORECASE)
    number_matches = re.findall(r"\b\d{1,5}\b", address)
    # The first number follows the street in the address segment. Later values
    # commonly describe bedrooms, bathrooms, or parking spaces.
    number = number_matches[0] if number_matches else ""
    street_match = re.search(
        r"\b(rua|r|avenida|av|alameda|al|praca|pc|travessa|tv)\.?\s+([^,0-9]+)",
        address,
        flags=re.IGNORECASE,
    )
    street = normalize_street(" ".join(street_match.groups())) if street_match else ""
    return street, normalize_number(number), cep
