from __future__ import annotations

import re
from typing import Any

from .base import CARD_METADATA_HELPERS, BasePortalAdapter, slugify

IMOVELWEB_CARD_SCRIPT = CARD_METADATA_HELPERS + r"""
() => {
    const cards = document.querySelectorAll('div[class*="postingCardLayout-module__posting-card-container"]');
    const results = [];
    cards.forEach(card => {
        const linkEl = card.querySelector('h2[class*="posting-description"] a');
        const href = linkEl ? linkEl.href : '';
        const title = linkEl ? linkEl.textContent.trim() : '';
        const priceEl = card.querySelector('[data-qa="POSTING_CARD_PRICE"]');
        const price = priceEl ? priceEl.textContent.trim() : '';
        const featuresEl = card.querySelector('[data-qa="POSTING_CARD_FEATURES"]');
        let area = '';
        if (featuresEl) {
            const spans = Array.from(featuresEl.querySelectorAll('span')).map(readText);
            const utilSpan = spans.find(s => /m[²2]\s*(?:[úu]til|privativ)/i.test(s));
            const cleanSpan = spans.find(s => /m[²2]/i.test(s) && !/tot|terreno/i.test(s));
            const anySpan = spans.find(s => /m[²2]/i.test(s));
            area = utilSpan || cleanSpan || anySpan || '';
        }
        const locationEl = card.querySelector('[data-qa="POSTING_CARD_LOCATION"]');
        const location = locationEl ? locationEl.textContent.trim() : '';
        const addressCandidates = Array.from(
            card.querySelectorAll('[data-qa*="ADDRESS"], [class*="address" i], strong, b')
        ).map(readText);
        const address = addressCandidates.find(
            value => /^(?:rua|r|avenida|av|alameda|al|travessa|tv|praça|praca)\.?\s+/i.test(value)
        ) || '';
        const addressParts = address.match(/^(.*?\D)(?:\s*,?\s+)(\d{1,5}[A-Za-z]?)$/);
        const metadata = cardMetadata(card, {
            streetSelectors: ['[data-qa="POSTING_CARD_ADDRESS"]', '[class*="address" i]'],
            areaSelectors: ['[data-qa="POSTING_CARD_FEATURES"] span'],
            bedroomSelectors: ['[aria-label*="quarto" i]', '[data-qa*="BEDROOM"]'],
            bathroomSelectors: ['[aria-label*="banheiro" i]', '[data-qa*="BATHROOM"]'],
            parkingSelectors: ['[aria-label*="vaga" i]', '[data-qa*="PARKING"]'],
            condominiumSelectors: ['[data-qa*="EXPENSE"]', '[data-qa*="CONDOMINIUM"]'],
            iptuSelectors: ['[data-qa*="IPTU"]'],
        });
        if (href && (price || area)) {
            results.push({
                title: title,
                price: price,
                area: metadata.area || area,
                location: location,
                ...metadata,
                street: addressParts ? addressParts[1].trim() : (address || metadata.street || location),
                number: addressParts ? addressParts[2] : '',
                link: href
            });
        }
    });
    return results;
}
"""


class ImovelwebAdapter(BasePortalAdapter):
    """Adapter for Imovelweb portal."""

    def __init__(self) -> None:
        self.site_id = "imovelweb"
        self.label = "IMOVELWEB"
        self.display_name = "Imovelweb"
        self.wait_selector = "div[class*='postingCardLayout-module__posting-card-container'], [data-qa='POSTING_CARD_PRICE']"
        self.timeout_ms = 15000
        self.card_script = IMOVELWEB_CARD_SCRIPT

    def build_url(self, neighborhood: str, price_filter: dict[str, Any] | None = None) -> str:
        slug = slugify(neighborhood)
        limits = price_filter or {}
        maximum = limits.get("max")
        minimum_area = limits.get("min_area_m2")
        area_segment = f"-mais-{int(float(minimum_area))}-m2" if minimum_area is not None else ""
        if maximum is not None:
            price = int(float(maximum))
            return f"https://www.imovelweb.com.br/apartamentos-venda-{slug}-belo-horizonte{area_segment}-menos-{price}-reales.html"
        return f"https://www.imovelweb.com.br/apartamentos-venda-{slug}-belo-horizonte{area_segment}.html"

    def extract_current_page(self, url: str) -> int:
        match = re.search(r"-pagina-(\d+)\.html", url)
        return int(match.group(1)) if match else 1

    def build_next_page_url(self, current_url: str, next_page: int) -> str:
        if "-pagina-" in current_url:
            return re.sub(r"-pagina-\d+\.html", f"-pagina-{next_page}.html", current_url)
        return current_url.replace(".html", f"-pagina-{next_page}.html")

    def filter_listings(self, listings: list[dict[str, Any]], neighborhood: str) -> tuple[list[dict[str, Any]], int]:
        target_slug = slugify(neighborhood)
        matching = []
        for item in listings:
            loc_slug = slugify(str(item.get("location") or ""))
            if "belo-horizonte" in loc_slug and target_slug in loc_slug:
                if item.get("link") and not str(item["link"]).startswith("http"):
                    item["link"] = f"https://www.imovelweb.com.br{item['link']}"
                matching.append(item)
        rejected = len(listings) - len(matching)
        return matching, rejected
