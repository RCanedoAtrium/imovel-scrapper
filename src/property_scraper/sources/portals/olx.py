from __future__ import annotations

import re
import urllib.parse
from typing import Any

from .base import CARD_METADATA_HELPERS, BasePortalAdapter, add_query_params, slugify

OLX_CARD_SCRIPT = CARD_METADATA_HELPERS + r"""
() => {
    const cards = document.querySelectorAll('section[class*="olx-adcard"]');
    const results = [];
    cards.forEach(card => {
        const linkEl = card.querySelector('a[data-testid="adcard-link"].olx-adcard__link');
        const href = linkEl ? linkEl.href : '';
        const titleEl = card.querySelector('h2.olx-adcard__title');
        const title = titleEl ? titleEl.textContent.trim() : '';
        const priceEl = card.querySelector('h3.olx-adcard__price');
        const price = priceEl ? priceEl.textContent.trim() : '';
        const details = Array.from(card.querySelectorAll('div[class*="olx-adcard__detail"]'));
        const areaEl = details.find(d => d.textContent.includes('m²'));
        const area = areaEl ? areaEl.textContent.trim() : '';
        const locEl = card.querySelector('p.olx-adcard__location');
        const location = locEl ? locEl.textContent.trim() : '';
        const metadata = cardMetadata(card, {
            streetSelectors: [],
            areaSelectors: ['[aria-label*="metros quadrados" i]'],
            bedroomSelectors: ['[aria-label*="quarto" i]', '[class*="bedroom"]'],
            bathroomSelectors: ['[aria-label*="banheiro" i]', '[class*="bathroom"]'],
            parkingSelectors: ['[aria-label*="vaga" i]', '[class*="parking"]'],
            condominiumSelectors: ['[class*="condominium"]'],
            iptuSelectors: ['[class*="iptu"]'],
        });
        if (href && !href.endsWith('#') && (price || area)) {
            results.push({
                title: title || location,
                location: location,
                price: price,
                area: area,
                ...metadata,
                link: href
            });
        }
    });
    return results;
}
"""


class OLXAdapter(BasePortalAdapter):
    """Adapter for OLX portal."""

    def __init__(self) -> None:
        self.site_id = "olx"
        self.label = "OLX"
        self.display_name = "OLX"
        self.wait_selector = "section[class*='olx-adcard']"
        self.timeout_ms = 15000
        self.card_script = OLX_CARD_SCRIPT

    def build_url(self, neighborhood: str, price_filter: dict[str, Any] | None = None) -> str:
        neighborhood_escaped = urllib.parse.quote(neighborhood)
        limits = price_filter or {}
        base = f"https://www.olx.com.br/imoveis/venda/apartamentos/estado-mg/belo-horizonte-e-regiao?q={neighborhood_escaped}"
        return add_query_params(base, {"pe": limits.get("max")})

    def extract_current_page(self, url: str) -> int:
        match = re.search(r"[?&]o=(\d+)", url)
        return int(match.group(1)) if match else 1

    def build_next_page_url(self, current_url: str, next_page: int) -> str:
        return add_query_params(current_url, {"o": next_page})

    def filter_listings(self, listings: list[dict[str, Any]], neighborhood: str) -> tuple[list[dict[str, Any]], int]:
        target_slug = slugify(neighborhood)
        matching = []
        for item in listings:
            loc_slug = slugify(str(item.get("location") or ""))
            if "belo-horizonte" in loc_slug and target_slug in loc_slug:
                matching.append(item)
        rejected = len(listings) - len(matching)
        return matching, rejected
