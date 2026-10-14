from __future__ import annotations

import re
from typing import Any

from .base import CARD_METADATA_HELPERS, BasePortalAdapter, add_query_params, slugify

GRUPO_OLX_CARD_SCRIPT = CARD_METADATA_HELPERS + r"""
() => {
    const cards = document.querySelectorAll('a.olx-core-surface, .group\\/card, a[class*="olx-core-surface"]');
    const results = [];
    cards.forEach(card => {
        const href = card.href || '';
        const addrEl = card.querySelector('[data-cy="rp-cardProperty-location-txt"]');
        const address = addrEl ? addrEl.textContent.trim() : '';
        const cardTitle = card.getAttribute('title') || '';
        const priceEl = card.querySelector('[data-cy="rp-cardProperty-price-txt"]');
        const price = priceEl ? priceEl.textContent.trim() : '';
        const areaEl = card.querySelector('[data-cy="rp-cardProperty-propertyArea-txt"]');
        const metadata = cardMetadata(card, {
            streetSelectors: ['[data-cy="rp-cardProperty-address-txt"]', '[data-cy*="address"]'],
            areaSelectors: ['[data-cy="rp-cardProperty-propertyArea-txt"]'],
            bedroomSelectors: ['[data-cy*="bedroom"]', '[data-cy*="dormitory"]'],
            bathroomSelectors: ['[data-cy*="bathroom"]'],
            parkingSelectors: ['[data-cy*="garage"]', '[data-cy*="parking"]'],
            condominiumSelectors: ['[data-cy*="condominium"]'],
            iptuSelectors: ['[data-cy*="iptu"]'],
        });
        const area = metadata.area || (areaEl ? areaEl.textContent.trim() : '');
        if (href && (price || area)) {
            results.push({
                title: cardTitle || address,
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
VIVAREAL_CARD_SCRIPT = GRUPO_OLX_CARD_SCRIPT
ZAP_CARD_SCRIPT = GRUPO_OLX_CARD_SCRIPT


class GrupoOLXAdapter(BasePortalAdapter):
    """Shared adapter for Grupo OLX portals (VivaReal and Zap Imóveis)."""

    def __init__(self, site_id: str, label: str, display_name: str):
        self.site_id = site_id.lower()
        self.label = label
        self.display_name = display_name
        self.wait_selector = "[data-cy='rp-cardProperty-price-txt']"
        self.timeout_ms = 20000
        self.card_script = GRUPO_OLX_CARD_SCRIPT
        self.page_param = "pagina" if self.site_id == "vivareal" else "page"

    def build_url(self, neighborhood: str, price_filter: dict[str, Any] | None = None) -> str:
        slug = slugify(neighborhood)
        limits = price_filter or {}
        common_price_params = {
            "precoMinimo": limits.get("min"),
            "precoMaximo": limits.get("max"),
        }
        if self.site_id == "vivareal":
            base = f"https://www.vivareal.com.br/venda/minas-gerais/belo-horizonte/bairros/{slug}/apartamento_residencial/"
        else:
            base = f"https://www.zapimoveis.com.br/venda/apartamentos/mg%2Bbelo-horizonte%2B%2B{slug}/"
        return add_query_params(base, common_price_params)

    def extract_current_page(self, url: str) -> int:
        match = re.search(rf"[?&]{self.page_param}=(\d+)", url)
        return int(match.group(1)) if match else 1

    def build_next_page_url(self, current_url: str, next_page: int) -> str:
        return add_query_params(current_url, {self.page_param: next_page})
