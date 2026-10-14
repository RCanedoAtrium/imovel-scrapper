from __future__ import annotations

from typing import Any

from .base import CARD_METADATA_HELPERS, BasePortalAdapter, slugify

QUINTOANDAR_CARD_SCRIPT = CARD_METADATA_HELPERS + r"""
() => {
    const cards = document.querySelectorAll('div[data-testid="house-card-container"] a[href*="/imovel/"]');
    const results = [];
    cards.forEach(card => {
        const href = card.href || '';
        const lines = (card.innerText || '').split('\n').map(value => value.trim()).filter(Boolean);
        const priceIndex = lines.findIndex(value => /^R\$\s*/.test(value));
        const title = priceIndex > 0
            ? lines.slice(0, priceIndex).filter(value => !/^(Exclusivo|Anúncio novo)$/i.test(value)).join(' ')
            : (card.getAttribute('title') || '');
        const price = priceIndex >= 0 ? lines[priceIndex] : '';
        const costLine = lines.slice(priceIndex + 1).find(
            value => /condo\.?|condom[ií]nio/i.test(value)
        ) || '';
        const condominiumMatch = costLine.match(
            /(R\$\s*[\d.]+(?:,\d{1,2})?)\s*condo\.?|condom[ií]nio/i
        );
        const amenities = lines.find(value => /\d+\s*m[²2]/i.test(value)) || '';
        const location = lines.find(value => /·\s*Belo Horizonte/i.test(value)) || '';
        const address = location.split(/\s*,\s*[^,]+?\s*·/)[0].trim();
        const addressParts = address.match(/^(.*?\D)(?:\s*,?\s+)(\d{1,5}[A-Za-z]?)$/);
        const area = (amenities.match(/\d+(?:[.,]\d+)?\s*m[²2]/i) || [''])[0];
        const metadata = cardMetadata(card, {
            streetSelectors: [],
            areaSelectors: [],
            bedroomSelectors: ['[aria-label*="quarto" i]', '[data-testid*="bedroom"]'],
            bathroomSelectors: ['[aria-label*="banheiro" i]', '[data-testid*="bathroom"]'],
            parkingSelectors: ['[aria-label*="vaga" i]', '[data-testid*="parking"]'],
            condominiumSelectors: ['[class*="condominium"]'],
            iptuSelectors: ['[class*="iptu"]'],
        });
        if (href && (price || area)) {
            results.push({
                title: title,
                price: price,
                area: metadata.area || area,
                location: location,
                ...metadata,
                street: addressParts ? addressParts[1].trim() : address,
                number: addressParts ? addressParts[2] : '',
                condominium: condominiumMatch ? condominiumMatch[1] : metadata.condominium,
                link: href
            });
        }
    });
    return results;
}
"""


class QuintoAndarAdapter(BasePortalAdapter):
    """Adapter for QuintoAndar portal."""

    def __init__(self) -> None:
        self.site_id = "quintoandar"
        self.label = "QUINTOANDAR"
        self.display_name = "QuintoAndar"
        self.wait_selector = "div[data-testid='house-card-container'] a[href*='/imovel/']"
        self.timeout_ms = 15000
        self.card_script = QUINTOANDAR_CARD_SCRIPT

    def build_url(self, neighborhood: str, price_filter: dict[str, Any] | None = None) -> str:
        slug = slugify(neighborhood)
        limits = price_filter or {}
        maximum = limits.get("max")
        if maximum is not None:
            minimum = int(float(limits.get("min", 150_000)))
            return (
                f"https://www.quintoandar.com.br/comprar/imovel/{slug}-belo-horizonte-mg-brasil/"
                f"de-{minimum}-a-{int(float(maximum))}-venda"
            )
        return f"https://www.quintoandar.com.br/comprar/imovel/{slug}-belo-horizonte-mg-brasil/de-apartamento"

    def extract_current_page(self, url: str) -> int:
        return 1

    def build_next_page_url(self, current_url: str, next_page: int) -> str | None:
        # QuintoAndar uses in-page button pagination ("Ver mais")
        return None

    def filter_listings(self, listings: list[dict[str, Any]], neighborhood: str) -> tuple[list[dict[str, Any]], int]:
        target_slug = slugify(neighborhood)
        matching = []
        for item in listings:
            loc_slug = slugify(str(item.get("location") or ""))
            if "belo-horizonte" in loc_slug and target_slug in loc_slug:
                matching.append(item)
        rejected = len(listings) - len(matching)
        return matching, rejected
