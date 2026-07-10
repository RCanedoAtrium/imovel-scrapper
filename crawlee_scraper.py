import os
import re
import asyncio
import unicodedata
import urllib.parse
import pandas as pd
from crawlee import Request
from crawlee.crawlers import PlaywrightCrawler, PlaywrightCrawlingContext
from crawlee.storages import Dataset

class CrawleeScraper:
    """Standalone modular web scraper for VivaReal, ZAP Imóveis, OLX, QuintoAndar, and Imovelweb using Crawlee and Playwright."""
    
    def __init__(self, raw_data_path="data/raw/anuncios_vivareal.csv"):
        self.raw_data_path = raw_data_path
        
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

    def generate_start_urls(self, neighborhoods, sites=None):
        """Generates search URLs for a list of neighborhoods across multiple sites."""
        if sites is None:
            # By default scrape all 5 sites
            sites = ["vivareal", "zap", "olx", "quintoandar", "imovelweb"]
            
        start_urls = []
        for neighborhood in neighborhoods:
            slug = self.slugify(neighborhood)
            if not slug:
                continue
                
            neighborhood_escaped = urllib.parse.quote(neighborhood)
            
            if "vivareal" in sites:
                url = f"https://www.vivareal.com.br/venda/minas-gerais/belo-horizonte/bairros/{slug}/apartamento_residencial/"
                start_urls.append({
                    "url": url,
                    "label": "VIVAREAL",
                    "userData": {"neighborhood": neighborhood, "site": "VivaReal"}
                })
                
            if "zap" in sites:
                url = f"https://www.zapimoveis.com.br/venda/apartamentos/mg+belo-horizonte/?onde=,Minas%20Gerais,Belo%20Horizonte,,{neighborhood_escaped},,bairro,"
                start_urls.append({
                    "url": url,
                    "label": "ZAP",
                    "userData": {"neighborhood": neighborhood, "site": "Zap Imóveis"}
                })
                
            if "olx" in sites:
                url = f"https://www.olx.com.br/imoveis/venda/apartamentos/estado-mg/belo-horizonte-e-regiao/belo-horizonte?q={neighborhood_escaped}"
                start_urls.append({
                    "url": url,
                    "label": "OLX",
                    "userData": {"neighborhood": neighborhood, "site": "OLX"}
                })
                
            if "quintoandar" in sites:
                url = f"https://www.quintoandar.com.br/comprar/imovel/{slug}-belo-horizonte-mg-brasil/de-apartamento"
                start_urls.append({
                    "url": url,
                    "label": "QUINTOANDAR",
                    "userData": {"neighborhood": neighborhood, "site": "QuintoAndar"}
                })
                
            if "imovelweb" in sites:
                url = f"https://www.imovelweb.com.br/apartamentos-venda-belo-horizonte-mg-{slug}.html"
                start_urls.append({
                    "url": url,
                    "label": "IMOVELWEB",
                    "userData": {"neighborhood": neighborhood, "site": "Imovelweb"}
                })
                
        return start_urls

    async def scrape(self, neighborhoods, max_pages_per_bairro=2, sites=None):
        """Asynchronously scrapes listings for the specified neighborhoods and sites."""
        start_urls = self.generate_start_urls(neighborhoods, sites)
        if not start_urls:
            print("Nenhum link de bairro gerado.")
            return []
            
        print(f"Iniciando Crawlee PlaywrightCrawler para {len(neighborhoods)} bairros em {len(start_urls)} URLs alvo...")
        
        # Open/Reset the default dataset
        dataset = await Dataset.open()
        
        # Configure the crawler
        crawler = PlaywrightCrawler(
            max_requests_per_crawl=len(start_urls) * max_pages_per_bairro * 2,
            browser_launch_options={
                "args": [
                    "--disable-gpu",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled"
                ]
            },
            headless=True,
            browser_type="chromium",
            ignore_http_error_status_codes=[403, 404],
        )
        
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
                    "a.olx-core-surface, .group\\/card, a[class*='olx-core-surface']",
                    timeout=15000
                )
            except Exception as e:
                context.log.warning(f"Timeout VivaReal em {neighborhood}: {e}")
                os.makedirs("data/screenshots", exist_ok=True)
                await page.screenshot(path=f"data/screenshots/vivareal_{self.slugify(neighborhood)}_error.png")
                return

            listings = await page.evaluate("""
                () => {
                    const cards = document.querySelectorAll('a.olx-core-surface, .group\\\\/card, a[class*="olx-core-surface"]');
                    const results = [];
                    cards.forEach(card => {
                        const href = card.href || '';
                        const addrEl = card.querySelector('[data-cy="rp-cardProperty-location-txt"]');
                        const address = addrEl ? addrEl.textContent.trim() : '';
                        const cardTitle = card.getAttribute('title') || '';
                        const priceEl = card.querySelector('[data-cy="rp-cardProperty-price-txt"]');
                        const price = priceEl ? priceEl.textContent.trim() : '';
                        const areaEl = card.querySelector('[data-cy="rp-cardProperty-propertyArea-txt"]');
                        const area = areaEl ? areaEl.textContent.trim() : '';
                        if (href && (price || area)) {
                            results.push({
                                title: cardTitle || address,
                                price: price,
                                area: area,
                                link: href
                            });
                        }
                    });
                    return results;
                }
            """)
            
            context.log.info(f"[VivaReal] Encontrados {len(listings)} anúncios em {neighborhood}.")
            for item in listings:
                item['neighborhood'] = neighborhood.upper()
                item['site'] = site
                await context.push_data(item)
                
            # Handle Pagination
            current_page_match = re.search(r'[?&]pagina=(\d+)', url)
            current_page = int(current_page_match.group(1)) if current_page_match else 1
            if current_page < max_pages_per_bairro:
                next_page = current_page + 1
                next_url = re.sub(r'pagina=\d+', f'pagina={next_page}', url) if "pagina=" in url else f"{url}&pagina={next_page}" if "?" in url else f"{url}?pagina={next_page}"
                await context.add_requests([Request.from_url(url=next_url, label="VIVAREAL", user_data=user_data)])

        # --- ZAP Imóveis Request Handler ---
        @crawler.router.handler("ZAP")
        async def handle_zap(context: PlaywrightCrawlingContext) -> None:
            url = context.request.url
            user_data = context.request.user_data
            neighborhood = user_data.get("neighborhood", "Desconhecido")
            site = user_data.get("site", "Zap Imóveis")
            context.log.info(f"Processando ZAP Imóveis: {neighborhood} -> {url}")
            
            page = context.page
            try:
                await page.wait_for_selector(
                    "a.olx-core-surface, .group\\/card, a[class*='olx-core-surface']",
                    timeout=15000
                )
            except Exception as e:
                context.log.warning(f"Timeout ZAP em {neighborhood}: {e}")
                os.makedirs("data/screenshots", exist_ok=True)
                await page.screenshot(path=f"data/screenshots/zap_{self.slugify(neighborhood)}_error.png")
                return

            listings = await page.evaluate("""
                () => {
                    const cards = document.querySelectorAll('a.olx-core-surface, .group\\\\/card, a[class*="olx-core-surface"]');
                    const results = [];
                    cards.forEach(card => {
                        const href = card.href || '';
                        const addrEl = card.querySelector('[data-cy="rp-cardProperty-location-txt"]');
                        const address = addrEl ? addrEl.textContent.trim() : '';
                        const cardTitle = card.getAttribute('title') || '';
                        const priceEl = card.querySelector('[data-cy="rp-cardProperty-price-txt"]');
                        const price = priceEl ? priceEl.textContent.trim() : '';
                        const areaEl = card.querySelector('[data-cy="rp-cardProperty-propertyArea-txt"]');
                        const area = areaEl ? areaEl.textContent.trim() : '';
                        if (href && (price || area)) {
                            results.push({
                                title: cardTitle || address,
                                price: price,
                                area: area,
                                link: href
                            });
                        }
                    });
                    return results;
                }
            """)
            
            context.log.info(f"[ZAP Imóveis] Encontrados {len(listings)} anúncios em {neighborhood}.")
            for item in listings:
                item['neighborhood'] = neighborhood.upper()
                item['site'] = site
                await context.push_data(item)
                
            # Handle Pagination
            current_page_match = re.search(r'[?&]pagina=(\d+)', url)
            current_page = int(current_page_match.group(1)) if current_page_match else 1
            if current_page < max_pages_per_bairro:
                next_page = current_page + 1
                next_url = re.sub(r'pagina=\d+', f'pagina={next_page}', url) if "pagina=" in url else f"{url}&pagina={next_page}"
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
                os.makedirs("data/screenshots", exist_ok=True)
                await page.screenshot(path=f"data/screenshots/olx_{self.slugify(neighborhood)}_error.png")
                return

            listings = await page.evaluate("""
                () => {
                    const cards = document.querySelectorAll('section[class*="olx-adcard"]');
                    const results = [];
                    cards.forEach(card => {
                        const linkEl = card.querySelector('a.olx-adcard__link');
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
                        
                        if (href && (price || area)) {
                            results.push({
                                title: title || location,
                                price: price,
                                area: area,
                                link: href
                            });
                        }
                    });
                    return results;
                }
            """)
            
            context.log.info(f"[OLX] Encontrados {len(listings)} anúncios em {neighborhood}.")
            for item in listings:
                item['neighborhood'] = neighborhood.upper()
                item['site'] = site
                await context.push_data(item)
                
            # Handle Pagination
            current_page_match = re.search(r'[?&]o=(\d+)', url)
            current_page = int(current_page_match.group(1)) if current_page_match else 1
            if current_page < max_pages_per_bairro:
                next_page = current_page + 1
                next_url = re.sub(r'o=\d+', f'o={next_page}', url) if "o=" in url else f"{url}&o={next_page}"
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
                os.makedirs("data/screenshots", exist_ok=True)
                await page.screenshot(path=f"data/screenshots/quintoandar_{self.slugify(neighborhood)}_error.png")
                return

            listings = await page.evaluate("""
                () => {
                    const cards = document.querySelectorAll('div[data-testid="house-card-container"] a[href*="/imovel/"]');
                    const results = [];
                    cards.forEach(card => {
                        const href = card.href || '';
                        const titleEl = card.querySelector('h2[class*="descriptionText"]');
                        const title = titleEl ? titleEl.textContent.trim() : '';
                        const priceEl = card.querySelector('div[class*="CardTitle-Title"] p');
                        const price = priceEl ? priceEl.textContent.trim() : '';
                        const areaEl = card.querySelector('h3[class*="amenitiesText"]');
                        const areaText = areaEl ? areaEl.textContent.trim() : '';
                        const area = areaText.split('·')[0].trim();
                        
                        if (href && (price || area)) {
                            results.push({
                                title: title,
                                price: price,
                                area: area,
                                link: href
                            });
                        }
                    });
                    return results;
                }
            """)
            
            context.log.info(f"[QuintoAndar] Encontrados {len(listings)} anúncios em {neighborhood}.")
            for item in listings:
                item['neighborhood'] = neighborhood.upper()
                item['site'] = site
                await context.push_data(item)
                
            # Handle Pagination
            current_page_match = re.search(r'[?&]page=(\d+)', url)
            current_page = int(current_page_match.group(1)) if current_page_match else 1
            if current_page < max_pages_per_bairro:
                next_page = current_page + 1
                next_url = re.sub(r'page=\d+', f'page={next_page}', url) if "page=" in url else f"{url}&page={next_page}" if "?" in url else f"{url}?page={next_page}"
                await context.add_requests([Request.from_url(url=next_url, label="QUINTOANDAR", user_data=user_data)])

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
                    "div[class*='postingCardLayout-module__posting-card-container']",
                    timeout=15000
                )
            except Exception as e:
                context.log.warning(f"Timeout Imovelweb em {neighborhood}: {e}")
                os.makedirs("data/screenshots", exist_ok=True)
                await page.screenshot(path=f"data/screenshots/imovelweb_{self.slugify(neighborhood)}_error.png")
                return

            listings = await page.evaluate("""
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
                        const areaEl = featuresEl ? featuresEl.querySelector('span') : null;
                        const area = areaEl ? areaEl.textContent.trim() : '';
                        
                        if (href && (price || area)) {
                            results.push({
                                title: title,
                                price: price,
                                area: area,
                                link: href
                            });
                        }
                    });
                    return results;
                }
            """)
            
            context.log.info(f"[Imovelweb] Encontrados {len(listings)} anúncios em {neighborhood}.")
            for item in listings:
                item['neighborhood'] = neighborhood.upper()
                item['site'] = site
                # Normalize link
                if item['link'] and not item['link'].startswith('http'):
                    item['link'] = f"https://www.imovelweb.com.br{item['link']}"
                await context.push_data(item)
                
            # Handle Pagination
            current_page_match = re.search(r'-pagina-(\d+)\.html', url)
            current_page = int(current_page_match.group(1)) if current_page_match else 1
            if current_page < max_pages_per_bairro:
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
        await crawler.run(requests)
        
        # Retrieve results
        results = await dataset.get_data()
        items = results.items
        print(f"Scrape completo! Total de anúncios coletados pelo Crawlee: {len(items)}")
        
        if items:
            df = pd.DataFrame(items)
            # Save CSV locally
            os.makedirs(os.path.dirname(self.raw_data_path), exist_ok=True)
            df.to_csv(self.raw_data_path, index=False, encoding='utf-8-sig')
            print(f"Anúncios salvos em: {self.raw_data_path}")
            
        return items

    def run(self, neighborhoods, max_pages_per_bairro=2, sites=None):
        """Wrapper method to run the async crawler synchronously."""
        return asyncio.run(self.scrape(neighborhoods, max_pages_per_bairro, sites))

if __name__ == "__main__":
    # Standard local test block
    test_bairros = ["Savassi"]
    scraper = CrawleeScraper()
    print("Iniciando teste de raspagem modular com Crawlee...")
    scraper.run(test_bairros, max_pages_per_bairro=1, sites=["vivareal", "zap", "olx", "quintoandar", "imovelweb"])
