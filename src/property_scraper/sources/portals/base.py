from __future__ import annotations

import re
import unicodedata
import urllib.parse
from abc import ABC, abstractmethod
from typing import Any

RAW_LISTING_COLUMNS = [
    "title", "street", "number", "location", "neighborhood", "price", "area", "bedrooms", "bathrooms",
    "parking_spaces", "condominium", "advertised_iptu", "link", "site",
]

CARD_METADATA_HELPERS = r"""
const readText = (element) => element ? (element.textContent || '').replace(/\s+/g, ' ').trim() : '';
const firstText = (card, selectors) => {
    for (const selector of selectors || []) {
        const value = readText(card.querySelector(selector));
        if (value) return value;
    }
    return '';
};
const countFrom = (value, labels) => {
    const text = String(value || '');
    for (const label of labels) {
        const match = text.match(new RegExp('(\\d+)\\s*' + label, 'i'));
        if (match) return match[1];
    }
    return '';
};
const feature = (card, selectors, labels, fallbackText) => {
    const explicit = firstText(card, selectors);
    const explicitCount = (explicit.match(/\d+/) || [''])[0];
    return countFrom(explicit, labels) || countFrom(fallbackText, labels) || explicitCount;
};
const moneyAfter = (text, labels) => {
    for (const label of labels) {
        const amount = 'R\\$\\s*[\\d.]+(?:,\\d{1,2})?';
        const beforeLabel = String(text || '').match(
            new RegExp(label + '\\s*:?\\s*(' + amount + ')', 'i')
        );
        if (beforeLabel) return beforeLabel[1];
        const afterLabel = String(text || '').match(
            new RegExp('(' + amount + ')\\s*' + label, 'i')
        );
        if (afterLabel) return afterLabel[1];
    }
    return '';
};
const cardMetadata = (card, options = {}) => {
    const allText = readText(card) + ' ' + (card.getAttribute('title') || '');
    let area = '';
    const explicitAreas = [];
    for (const selector of options.areaSelectors || []) {
        card.querySelectorAll(selector).forEach(el => {
            const txt = readText(el);
            if (txt) explicitAreas.push(txt);
        });
    }
    const utilCandidate = explicitAreas.find(txt => /m[²2]\s*(?:[úu]til|privativ)/i.test(txt));
    const cleanCandidate = explicitAreas.find(txt => /m[²2]/i.test(txt) && !/tot|terreno/i.test(txt));
    if (utilCandidate) {
        area = utilCandidate;
    } else if (cleanCandidate) {
        area = cleanCandidate;
    } else if (explicitAreas.length > 0) {
        area = explicitAreas[0];
    } else {
        const utilMatch = allText.match(/\d+(?:[.,]\d+)?\s*m[²2]\s*(?:[úu]til|privativ)/i);
        const cleanMatch = allText.match(/\d+(?:[.,]\d+)?\s*m[²2](?!\s*tot)/i);
        const anyMatch = allText.match(/\d+(?:[.,]\d+)?\s*m[²2]/i);
        area = utilMatch ? utilMatch[0] : (cleanMatch ? cleanMatch[0] : (anyMatch ? anyMatch[0] : ''));
    }
    const condominiumText = firstText(card, options.condominiumSelectors);
    const iptuText = firstText(card, options.iptuSelectors);
    return {
        street: firstText(card, options.streetSelectors)
            || ((allText.match(/\b(?:rua|r|avenida|av|alameda|al|travessa|tv|praça|praca)\.?\s+[^,]+/i) || [''])[0]),
        area,
        bedrooms: feature(card, options.bedroomSelectors, ['quartos?', 'dormit[oó]rios?'], allText),
        bathrooms: feature(card, options.bathroomSelectors, ['banheiros?', 'banhos?'], allText),
        parking_spaces: feature(card, options.parkingSelectors, ['vagas?', 'garagens?'], allText),
        condominium: moneyAfter(condominiumText || allText, ['cond(?:o(?:m[ií]nio)?)?\\.?']),
        advertised_iptu: moneyAfter(iptuText || allText, ['iptu']),
    };
};
"""


def slugify(text: Any) -> str:
    """Converts neighborhood name to URL slug format."""
    if not isinstance(text, str):
        return ""
    text = text.lower().strip()
    text = unicodedata.normalize("NFD", text)
    text = "".join([c for c in text if unicodedata.category(c) != "Mn"])
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def add_query_params(url: str, params: dict[str, Any]) -> str:
    """Adds or replaces query string parameters without breaking pagination."""
    parsed = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))

    def serialize(value: Any) -> str:
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)

    query.update({key: serialize(value) for key, value in params.items() if value is not None})
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), parsed.fragment)
    )


class BasePortalAdapter(ABC):
    """Abstract base adapter defining portal contract."""

    site_id: str
    label: str
    display_name: str
    wait_selector: str
    timeout_ms: int = 15000
    card_script: str

    @abstractmethod
    def build_url(self, neighborhood: str, price_filter: dict[str, Any] | None = None) -> str:
        """Build the initial search URL for a neighborhood and price filter."""

    @abstractmethod
    def extract_current_page(self, url: str) -> int:
        """Extract current page number from URL."""

    @abstractmethod
    def build_next_page_url(self, current_url: str, next_page: int) -> str | None:
        """Build next page URL if applicable."""

    def filter_listings(self, listings: list[dict[str, Any]], neighborhood: str) -> tuple[list[dict[str, Any]], int]:
        """Filters listings by location if needed. Returns (matching_listings, rejected_count)."""
        return listings, 0
