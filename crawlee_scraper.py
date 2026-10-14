import asyncio
import os
import re
import shutil
import unicodedata
import urllib.parse
import uuid

import pandas as pd
from crawlee import ConcurrencySettings, Request
from crawlee.configuration import Configuration
from crawlee.crawlers import PlaywrightCrawler, PlaywrightCrawlingContext
from crawlee.storages import Dataset

RAW_LISTING_COLUMNS = [
    "title", "street", "number", "location", "neighborhood", "price", "area", "bedrooms", "bathrooms",
    "parking_spaces", "condominium", "advertised_iptu", "link", "site",
]

# This runs inside the browser context.  Portals use different markup, but their
# cards expose the same concepts through data attributes, accessible labels, or
# visible text.  The explicit selectors are preferred; text parsing is only a
# fallback for cards whose markup is less semantic.
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


class CrawleeScraper:
    """Scraper do pipeline para VivaReal, ZAP, OLX, QuintoAndar e Imovelweb."""

    def __init__(self, raw_data_path=None):
        self.raw_data_path = raw_data_path
        self.pagination_stats = []
        self._seen_by_query = {}

    @staticmethod
    def listing_with_contract(item):
        """Returns a raw listing with every CSV column present and ordered."""
        return {column: item.get(column, "") or "" for column in RAW_LISTING_COLUMNS}

    @staticmethod
    def slugify(text):
        """Converts neighborhood name to URL slug format."""
        if not isinstance(text, str):
            return ""
        text = text.lower().strip()
        # Remove accents
        text = unicodedata.normalize('NFD', text)
        text = "".join([c for c in text if unicodedata.category(c) != 'Mn'])
        # Replace non-alphanumeric with hyphens
        text = re.sub(r'[^a-z0-9]+', '-', text)
        # Strip duplicate or outer hyphens
        text = re.sub(r'-+', '-', text)
        return text.strip('-')

    @staticmethod
    def add_query_params(url, params):
        """Adds or replaces query string parameters without breaking pagination."""
        parsed = urllib.parse.urlsplit(url)
        query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        def serialize(value):
            if isinstance(value, float) and value.is_integer():
                return str(int(value))
            return str(value)
        query.update({key: serialize(value) for key, value in params.items() if value is not None})
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), parsed.fragment))

    @staticmethod
    def extract_price(value):
        """Extracts the first advertised BRL value for pagination decisions."""
        match = re.search(r"R\$\s*([\d.]+(?:,\d{1,2})?)", str(value or ""), re.IGNORECASE)
        text = match.group(1) if match else str(value or "")
        text = re.sub(r"[^\d,.-]", "", text)
        if not text:
            return 0.0
        if "," in text:
            integer, decimal = (text.split(",", 1) + [""])[:2]
            text = f"{integer.replace('.', '')}.{decimal}"
        elif "." in text and (text.count(".") > 1 or len(text.rsplit(".", 1)[-1]) == 3):
            text = text.replace(".", "")
        try:
            return float(text)
        except ValueError:
            return 0.0

    @staticmethod
    def extract_area(value):
        """Extracts square metres from the area text exposed by a card."""
        match = re.search(r"(\d+(?:[.,]\d+)?)\s*m[²2]", str(value or ""), re.IGNORECASE)
        if not match:
            return 0.0
        try:
            return float(match.group(1).replace(",", "."))
        except ValueError:
            return 0.0

    def is_olx_location_match(self, location, neighborhood):
        """Accepts OLX cards only when their visible location is the requested BH neighborhood."""
        normalized_location = self.slugify(str(location or ""))
        return (
            "belo-horizonte" in normalized_location
            and self.slugify(neighborhood) in normalized_location
        )

    def is_imovelweb_location_match(self, location, neighborhood):
        """Keeps Imovelweb cards tied to the neighborhood selected in the search.

        Imovelweb sometimes appends promoted cards from nearby neighborhoods to a
        correctly filtered result page.  The search context alone is therefore
        not enough to label a card with a neighborhood.
        """
        return self.is_olx_location_match(location, neighborhood)

    def is_quintoandar_location_match(self, location, neighborhood):
        """Accepts QuintoAndar cards only when their visible location matches the search."""
        return self.is_olx_location_match(location, neighborhood)

    def register_page(
        self,
        listings,
        site,
        neighborhood,
        page_number,
        price_filters,
        max_pages,
        min_pages,
        min_new_listings,
        rejected_by_location=0,
    ):
        """Deduplicates a query page and decides whether adaptive pagination continues."""
        query_key = (self.slugify(site), self.slugify(neighborhood))
        seen = self._seen_by_query.setdefault(query_key, set())
        limits = {
            self.slugify(name): values for name, values in (price_filters or {}).items()
        }.get(self.slugify(neighborhood), {})
        minimum = float(limits.get("min", 0) or 0)
        maximum = float(limits.get("max", 0) or 0)
        new_items = []
        new_within_price = 0
        rejected_by_price = 0
        rejected_by_area = 0
        for item in listings:
            link = str(item.get("link") or item.get("url") or "")
            parsed = urllib.parse.urlsplit(link)
            identity = urllib.parse.urlunsplit(
                (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", "")
            ) or link
            if not identity or identity in seen:
                continue
            seen.add(identity)
            new_items.append(item)
            price = self.extract_price(item.get("price"))
            minimum_area = float(limits.get("min_area_m2", 0) or 0)
            area = self.extract_area(item.get("area"))
            matches_price = price > 0 and (not minimum or price >= minimum) and (not maximum or price <= maximum)
            matches_area = not minimum_area or area >= minimum_area
            matches = matches_price and matches_area
            if matches or not limits:
                new_within_price += 1
            elif not matches_price:
                rejected_by_price += 1
            else:
                rejected_by_area += 1

        min_pages = max(1, min(min_pages, max_pages))
        if page_number >= max_pages:
            continued = False
            stop_reason = "max_pages"
        elif page_number < min_pages:
            continued = True
            stop_reason = "minimum_pages_not_reached"
        elif new_within_price >= min_new_listings:
            continued = True
            stop_reason = "enough_new_listings"
        else:
            continued = False
            stop_reason = "low_yield"

        self.pagination_stats.append({
            "site": site,
            "neighborhood": neighborhood,
            "page": page_number,
            "raw_listings": len(listings),
            "new_unique_listings": len(new_items),
            "new_within_price": new_within_price,
            "rejected_by_price": rejected_by_price,
            "rejected_by_area": rejected_by_area,
            "rejected_by_location": rejected_by_location,
            "continued": continued,
            "decision": stop_reason,
        })
        return new_items, continued

    def generate_start_urls(self, neighborhoods, sites=None, price_filters=None):
        """Generates search URLs for a list of neighborhoods across multiple sites."""
        if sites is None:
            # By default scrape all 5 sites
            sites = ["vivareal", "zap", "olx", "quintoandar", "imovelweb"]

        start_urls = []
        normalized_filters = {
            self.slugify(name): limits for name, limits in (price_filters or {}).items()
        }
        for neighborhood in neighborhoods:
            slug = self.slugify(neighborhood)
            if not slug:
                continue

            neighborhood_escaped = urllib.parse.quote(neighborhood)
            limits = normalized_filters.get(slug, {})
            common_price_params = {
                "precoMinimo": limits.get("min"),
                "precoMaximo": limits.get("max"),
            }

            if "vivareal" in sites:
                url = f"https://www.vivareal.com.br/venda/minas-gerais/belo-horizonte/bairros/{slug}/apartamento_residencial/"
                url = self.add_query_params(url, common_price_params)
                start_urls.append({
                    "url": url,
                    "label": "VIVAREAL",
                    "userData": {"neighborhood": neighborhood, "site": "VivaReal"}
                })

            if "zap" in sites:
                url = f"https://www.zapimoveis.com.br/venda/apartamentos/mg%2Bbelo-horizonte%2B%2B{slug}/"
                url = self.add_query_params(url, common_price_params)
                start_urls.append({
                    "url": url,
                    "label": "ZAP",
                    "userData": {"neighborhood": neighborhood, "site": "Zap Imóveis"}
                })

            if "olx" in sites:
                url = f"https://www.olx.com.br/imoveis/venda/apartamentos/estado-mg/belo-horizonte-e-regiao?q={neighborhood_escaped}"
                url = self.add_query_params(url, {"pe": limits.get("max")})
                start_urls.append({
                    "url": url,
                    "label": "OLX",
                    "userData": {"neighborhood": neighborhood, "site": "OLX"}
                })

            if "quintoandar" in sites:
                maximum = limits.get("max")
                if maximum is not None:
                    minimum = int(float(limits.get("min", 150_000)))
                    url = (
                        f"https://www.quintoandar.com.br/comprar/imovel/{slug}-belo-horizonte-mg-brasil/"
                        f"de-{minimum}-a-{int(float(maximum))}-venda"
                    )
                else:
                    url = f"https://www.quintoandar.com.br/comprar/imovel/{slug}-belo-horizonte-mg-brasil/de-apartamento"
                start_urls.append({
                    "url": url,
                    "label": "QUINTOANDAR",
                    "userData": {"neighborhood": neighborhood, "site": "QuintoAndar"}
                })

            if "imovelweb" in sites:
                maximum = limits.get("max")
                minimum_area = limits.get("min_area_m2")
                area_segment = f"-mais-{int(float(minimum_area))}-m2" if minimum_area is not None else ""
                if maximum is not None:
                    price = int(float(maximum))
                    url = f"https://www.imovelweb.com.br/apartamentos-venda-{slug}-belo-horizonte{area_segment}-menos-{price}-reales.html"
                else:
                    url = f"https://www.imovelweb.com.br/apartamentos-venda-{slug}-belo-horizonte{area_segment}.html"
                start_urls.append({
                    "url": url,
                    "label": "IMOVELWEB",
                    "userData": {"neighborhood": neighborhood, "site": "Imovelweb"}
                })

        return start_urls

    async def scrape(
        self,
        neighborhoods,
        max_pages_per_bairro=2,
        sites=None,
        price_filters=None,
        min_pages_per_bairro=2,
        min_new_listings_per_page=3,
    ):
        """Asynchronously scrapes listings for the specified neighborhoods and sites."""
        self.pagination_stats = []
        self._seen_by_query = {}
        start_urls = self.generate_start_urls(neighborhoods, sites, price_filters)
        if not start_urls:
            print("No neighborhood links were generated.")
            return []

        print(f"Starting Crawlee PlaywrightCrawler for {len(neighborhoods)} neighborhoods across {len(start_urls)} target URLs...")

        # Isolate Crawlee persistence per run. This avoids Windows file locks from
        # an interrupted or overlapping prior process and prevents data mixing.
        storage_dir = os.path.abspath(os.path.join("storage", "runs", uuid.uuid4().hex))
        configuration = Configuration(storage_dir=storage_dir, purge_on_start=True)
        dataset = await Dataset.open(configuration=configuration)

        # Configure the crawler
        crawler = PlaywrightCrawler(
            configuration=configuration,
            max_requests_per_crawl=len(start_urls) * max_pages_per_bairro * 2,
            # A security-verification page is not a valid empty result.  Raising
            # from the handler lets Crawlee retry the individual neighborhood
            # instead of silently leaving it out of the run.
            max_request_retries=3,
            concurrency_settings=ConcurrencySettings(
                min_concurrency=1,
                max_concurrency=1,
                desired_concurrency=1,
            ),
            browser_launch_options={
                "args": [
                    "--disable-gpu",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled"
                ]
            },
            browser_new_context_options={
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "locale": "pt-BR",
                "timezone_id": "America/Sao_Paulo",
                "viewport": {"width": 1366, "height": 768},
                "ignore_https_errors": True,
            },
            headless=True,
            browser_type="chromium",
            # Keep Crawlee's browser fingerprint aligned with the launched
            # browser.  A fixed, hand-written fingerprint caused Imovelweb's
            # Cloudflare verification to reject many neighborhood requests.
            fingerprint_generator="default",
            ignore_http_error_status_codes=[403, 404],
        )

        async def store_page(listings, site, neighborhood, page_number, context, rejected_by_location=0):
            new_items, should_continue = self.register_page(
                listings=listings,
                site=site,
                neighborhood=neighborhood,
                page_number=page_number,
                price_filters=price_filters,
                max_pages=max_pages_per_bairro,
                min_pages=min_pages_per_bairro,
                min_new_listings=min_new_listings_per_page,
                rejected_by_location=rejected_by_location,
            )
            for item in new_items:
                item["neighborhood"] = neighborhood.upper()
                item["site"] = site
                await context.push_data(self.listing_with_contract(item))
            return should_continue

        # --- VivaReal Request Handler ---
        @crawler.router.handler("VIVAREAL")
        async def handle_vivareal(context: PlaywrightCrawlingContext) -> None:
            url = context.request.url
            user_data = context.request.user_data
            neighborhood = user_data.get("neighborhood", "Desconhecido")
            site = user_data.get("site", "VivaReal")
            context.log.info(f"Processando VivaReal: {neighborhood} -> {url}")

            page = context.page
            try:
                await page.wait_for_selector(
                    "[data-cy='rp-cardProperty-price-txt']",
                    timeout=20000
                )
            except Exception as e:
                context.log.warning(f"Timeout VivaReal em {neighborhood}: {e}")
                os.makedirs("data/diagnostics/screenshots", exist_ok=True)
                await page.screenshot(path=f"data/diagnostics/screenshots/vivareal_{self.slugify(neighborhood)}_error.png")
                raise RuntimeError(
                    f"VivaReal did not render listing cards for {neighborhood}"
                ) from e

            listings = await page.evaluate(VIVAREAL_CARD_SCRIPT)

            if not listings:
                os.makedirs("data/diagnostics/screenshots", exist_ok=True)
                await page.screenshot(path=f"data/diagnostics/screenshots/vivareal_{self.slugify(neighborhood)}_empty.png")
                raise RuntimeError(f"VivaReal returned no extractable cards for {neighborhood}")

            context.log.info(f"[VivaReal] Found {len(listings)} listings in {neighborhood}.")
            current_page_match = re.search(r'[?&]pagina=(\d+)', url)
            current_page = int(current_page_match.group(1)) if current_page_match else 1
            should_continue = await store_page(listings, site, neighborhood, current_page, context)
            if should_continue:
                next_page = current_page + 1
                next_url = self.add_query_params(url, {"pagina": next_page})
                await context.add_requests([Request.from_url(url=next_url, label="VIVAREAL", user_data=user_data)])

        # --- ZAP Imóveis Request Handler ---
        @crawler.router.handler("ZAP")
        async def handle_zap(context: PlaywrightCrawlingContext) -> None:
            url = context.request.url
            user_data = context.request.user_data
            neighborhood = user_data.get("neighborhood", "Desconhecido")
            site = user_data.get("site", "Zap Imóveis")
            context.log.info(f"Processing ZAP Imóveis: {neighborhood} -> {url}")

            page = context.page
            try:
                await page.wait_for_selector(
                    "[data-cy='rp-cardProperty-price-txt']",
                    timeout=20000
                )
            except Exception as e:
                context.log.warning(f"Timeout ZAP em {neighborhood}: {e}")
                os.makedirs("data/diagnostics/screenshots", exist_ok=True)
                await page.screenshot(path=f"data/diagnostics/screenshots/zap_{self.slugify(neighborhood)}_error.png")
                raise RuntimeError(
                    f"ZAP did not render listing cards for {neighborhood}"
                ) from e

            listings = await page.evaluate(ZAP_CARD_SCRIPT)

            context.log.info(f"[ZAP Imóveis] Found {len(listings)} listings in {neighborhood}.")
            if not listings:
                page_title = await page.title()
                user_agent = await page.evaluate("() => navigator.userAgent")
                context.log.warning(f"ZAP sem resultados. Titulo='{page_title}', UA='{user_agent}'")
                os.makedirs("data/diagnostics/screenshots", exist_ok=True)
                await page.screenshot(path=f"data/diagnostics/screenshots/zap_{self.slugify(neighborhood)}_empty.png")
                raise RuntimeError(f"ZAP returned no extractable cards for {neighborhood}")
            current_page_match = re.search(r'[?&]page=(\d+)', url)
            current_page = int(current_page_match.group(1)) if current_page_match else 1
            should_continue = await store_page(listings, site, neighborhood, current_page, context)
            if should_continue:
                next_page = current_page + 1
                next_url = self.add_query_params(url, {"page": next_page})
                await context.add_requests([Request.from_url(url=next_url, label="ZAP", user_data=user_data)])

        # --- OLX Request Handler ---
        @crawler.router.handler("OLX")
        async def handle_olx(context: PlaywrightCrawlingContext) -> None:
            url = context.request.url
            user_data = context.request.user_data
            neighborhood = user_data.get("neighborhood", "Desconhecido")
            site = user_data.get("site", "OLX")
            context.log.info(f"Processando OLX: {neighborhood} -> {url}")

            page = context.page
            try:
                await page.wait_for_selector("section[class*='olx-adcard']", timeout=15000)
            except Exception as e:
                context.log.warning(f"Timeout OLX em {neighborhood}: {e}")
                os.makedirs("data/diagnostics/screenshots", exist_ok=True)
                await page.screenshot(path=f"data/diagnostics/screenshots/olx_{self.slugify(neighborhood)}_error.png")
                raise RuntimeError(
                    f"OLX did not render listing cards for {neighborhood}"
                ) from e

            listings = await page.evaluate(OLX_CARD_SCRIPT)

            total_listings = len(listings)
            listings = [
                item for item in listings
                if self.is_olx_location_match(item.get("location"), neighborhood)
            ]
            rejected_by_location = total_listings - len(listings)
            context.log.info(
                f"[OLX] {len(listings)} listings from Belo Horizonte/{neighborhood}; "
                f"{rejected_by_location} rejected by location."
            )
            current_page_match = re.search(r'[?&]o=(\d+)', url)
            current_page = int(current_page_match.group(1)) if current_page_match else 1
            should_continue = await store_page(
                listings, site, neighborhood, current_page, context, rejected_by_location,
            )
            if should_continue:
                next_page = current_page + 1
                next_url = self.add_query_params(url, {"o": next_page})
                await context.add_requests([Request.from_url(url=next_url, label="OLX", user_data=user_data)])

        # --- QuintoAndar Request Handler ---
        @crawler.router.handler("QUINTOANDAR")
        async def handle_quintoandar(context: PlaywrightCrawlingContext) -> None:
            url = context.request.url
            user_data = context.request.user_data
            neighborhood = user_data.get("neighborhood", "Desconhecido")
            site = user_data.get("site", "QuintoAndar")
            context.log.info(f"Processando QuintoAndar: {neighborhood} -> {url}")

            page = context.page
            try:
                await page.wait_for_selector(
                    "div[data-testid='house-card-container'] a[href*='/imovel/']",
                    timeout=15000
                )
            except Exception as e:
                context.log.warning(f"Timeout QuintoAndar em {neighborhood}: {e}")
                os.makedirs("data/diagnostics/screenshots", exist_ok=True)
                await page.screenshot(path=f"data/diagnostics/screenshots/quintoandar_{self.slugify(neighborhood)}_error.png")
                raise RuntimeError(
                    f"QuintoAndar did not render listing cards for {neighborhood}"
                ) from e

            # QuintoAndar appends cards in the current document through the
            # "Ver mais" button.  The old ?page=N URL does not change its
            # server-side result set, so it only produced duplicates.
            current_page = 1
            while True:
                listings = await page.evaluate(QUINTOANDAR_CARD_SCRIPT)
                matching_listings = [
                    item for item in listings
                    if self.is_quintoandar_location_match(item.get("location"), neighborhood)
                ]
                rejected_by_location = len(listings) - len(matching_listings)
                context.log.info(
                    f"[QuintoAndar] {len(matching_listings)} listings in {neighborhood}; "
                    f"{rejected_by_location} rejected by location."
                )
                should_continue = await store_page(
                    matching_listings, site, neighborhood, current_page, context, rejected_by_location,
                )
                if not should_continue:
                    break

                more_button = page.get_by_role("button", name="Ver mais", exact=True)
                if await more_button.count() != 1:
                    context.log.info(f"[QuintoAndar] No more results for {neighborhood}.")
                    break

                card_count = len(listings)
                await more_button.click()
                try:
                    await page.wait_for_function(
                        """previousCount => document.querySelectorAll(
                            'div[data-testid="house-card-container"] a[href*="/imovel/"]'
                        ).length > previousCount""",
                        arg=card_count,
                        timeout=15_000,
                    )
                except Exception as e:
                    context.log.warning(
                        f"[QuintoAndar] 'Load more' did not load new cards in {neighborhood}: {e}"
                    )
                    break
                current_page += 1

        # --- Imovelweb Request Handler ---
        @crawler.router.handler("IMOVELWEB")
        async def handle_imovelweb(context: PlaywrightCrawlingContext) -> None:
            url = context.request.url
            user_data = context.request.user_data
            neighborhood = user_data.get("neighborhood", "Desconhecido")
            site = user_data.get("site", "Imovelweb")
            context.log.info(f"Processando Imovelweb: {neighborhood} -> {url}")

            page = context.page
            try:
                await page.wait_for_selector(
                    "div[class*='postingCardLayout-module__posting-card-container'], [data-qa='POSTING_CARD_PRICE']",
                    timeout=15000
                )
            except Exception as e:
                context.log.warning(f"Timeout Imovelweb em {neighborhood}: {e}")
                os.makedirs("data/diagnostics/screenshots", exist_ok=True)
                await page.screenshot(path=f"data/diagnostics/screenshots/imovelweb_{self.slugify(neighborhood)}_error.png")
                # Do not accept a Cloudflare challenge (or a transient portal
                # page) as an empty search.  Crawlee retries this request, which
                # is essential for preserving one result set per neighborhood.
                raise RuntimeError(
                    f"Imovelweb did not render listing cards for {neighborhood}"
                ) from e

            listings = await page.evaluate(IMOVELWEB_CARD_SCRIPT)

            matching_listings = [
                item for item in listings
                if self.is_imovelweb_location_match(item.get("location"), neighborhood)
            ]
            rejected_by_location = len(listings) - len(matching_listings)
            context.log.info(
                f"[Imovelweb] Found {len(matching_listings)} listings in {neighborhood}; "
                f"{rejected_by_location} from another neighborhood were discarded."
            )
            for item in matching_listings:
                if item['link'] and not item['link'].startswith('http'):
                    item['link'] = f"https://www.imovelweb.com.br{item['link']}"
            current_page_match = re.search(r'-pagina-(\d+)\.html', url)
            current_page = int(current_page_match.group(1)) if current_page_match else 1
            should_continue = await store_page(
                matching_listings, site, neighborhood, current_page, context, rejected_by_location,
            )
            if should_continue:
                next_page = current_page + 1
                if "-pagina-" in url:
                    next_url = re.sub(r'-pagina-\d+\.html', f'-pagina-{next_page}.html', url)
                else:
                    next_url = url.replace('.html', f'-pagina-{next_page}.html')
                await context.add_requests([Request.from_url(url=next_url, label="IMOVELWEB", user_data=user_data)])

        # Run the crawler with the start URLs
        requests = [
            Request.from_url(url=item["url"], label=item["label"], user_data=item["userData"])
            for item in start_urls
        ]
        try:
            await crawler.run(requests)

            # Retrieve results
            results = await dataset.get_data()
            items = results.items
            print(f"Scrape complete. Total listings collected by Crawlee: {len(items)}")

            if items and self.raw_data_path:
                df = pd.DataFrame(items)
                os.makedirs(os.path.dirname(self.raw_data_path), exist_ok=True)
                df.to_csv(
                    self.raw_data_path,
                    index=False,
                    sep=';',
                    decimal=',',
                    encoding='utf-8-sig',
                )
                print(f"Listings saved to: {self.raw_data_path}")

            return items
        finally:
            shutil.rmtree(storage_dir, ignore_errors=True)
            for empty_dir in (os.path.dirname(storage_dir), os.path.dirname(os.path.dirname(storage_dir))):
                try:
                    os.rmdir(empty_dir)
                except OSError:
                    pass

    def run(
        self,
        neighborhoods,
        max_pages_per_bairro=2,
        sites=None,
        price_filters=None,
        min_pages_per_bairro=2,
        min_new_listings_per_page=3,
    ):
        """Wrapper method to run the async crawler synchronously."""
        return asyncio.run(self.scrape(
            neighborhoods,
            max_pages_per_bairro,
            sites,
            price_filters,
            min_pages_per_bairro,
            min_new_listings_per_page,
        ))
