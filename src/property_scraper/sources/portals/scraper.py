from __future__ import annotations

import asyncio
import logging
import os
import shutil
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

import pandas as pd
from crawlee import ConcurrencySettings, Request
from crawlee.configuration import Configuration
from crawlee.crawlers import PlaywrightCrawler, PlaywrightCrawlingContext
from crawlee.storages import Dataset

from ...normalization import extract_area_m2, extract_currency
from .base import RAW_LISTING_COLUMNS, BasePortalAdapter, add_query_params, slugify
from .grupo_olx import GrupoOLXAdapter
from .imovelweb import ImovelwebAdapter
from .olx import OLXAdapter
from .quintoandar import QuintoAndarAdapter

logger = logging.getLogger("property_scraper.scraper")


class CrawleeScraper:
    """Scraper orchestrator for VivaReal, ZAP, OLX, QuintoAndar and Imovelweb."""

    def __init__(
        self,
        raw_data_path: str | Path | None = None,
        diagnostics_dir: str | Path | None = None,
    ) -> None:
        self.raw_data_path = str(raw_data_path) if raw_data_path else None
        self.diagnostics_dir = Path(diagnostics_dir) if diagnostics_dir else Path("data/diagnostics")
        self.pagination_stats: list[dict[str, Any]] = []
        self._seen_by_query: dict[tuple[str, str], set[str]] = {}

        self.adapters: dict[str, BasePortalAdapter] = {
            "vivareal": GrupoOLXAdapter("vivareal", "VIVAREAL", "VivaReal"),
            "zap": GrupoOLXAdapter("zap", "ZAP", "Zap Imóveis"),
            "olx": OLXAdapter(),
            "quintoandar": QuintoAndarAdapter(),
            "imovelweb": ImovelwebAdapter(),
        }

    @staticmethod
    def listing_with_contract(item: dict[str, Any]) -> dict[str, Any]:
        """Returns a raw listing with every CSV column present and ordered."""
        return {column: item.get(column, "") or "" for column in RAW_LISTING_COLUMNS}

    @staticmethod
    def slugify(text: Any) -> str:
        return slugify(text)

    @staticmethod
    def add_query_params(url: str, params: dict[str, Any]) -> str:
        return add_query_params(url, params)

    @staticmethod
    def extract_price(value: Any) -> float:
        """Extracts the first advertised BRL value using shared normalization."""
        return extract_currency(value)

    @staticmethod
    def extract_area(value: Any) -> float:
        """Extracts square metres using shared normalization."""
        return extract_area_m2(value)

    def is_olx_location_match(self, location: Any, neighborhood: str) -> bool:
        normalized_location = self.slugify(str(location or ""))
        return "belo-horizonte" in normalized_location and self.slugify(neighborhood) in normalized_location

    def is_imovelweb_location_match(self, location: Any, neighborhood: str) -> bool:
        return self.is_olx_location_match(location, neighborhood)

    def is_quintoandar_location_match(self, location: Any, neighborhood: str) -> bool:
        return self.is_olx_location_match(location, neighborhood)

    def register_page(
        self,
        listings: list[dict[str, Any]],
        site: str,
        neighborhood: str,
        page_number: int,
        price_filters: dict[str, Any] | None,
        max_pages: int,
        min_pages: int,
        min_new_listings: int,
        rejected_by_location: int = 0,
    ) -> tuple[list[dict[str, Any]], bool]:
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

    def generate_start_urls(
        self,
        neighborhoods: list[str],
        sites: list[str] | None = None,
        price_filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Generates search URLs for a list of neighborhoods across multiple sites."""
        sites = sites or ["vivareal", "zap", "olx", "quintoandar", "imovelweb"]
        start_urls = []
        normalized_filters = {
            self.slugify(name): limits for name, limits in (price_filters or {}).items()
        }
        for neighborhood in neighborhoods:
            slug = self.slugify(neighborhood)
            if not slug:
                continue
            limits = normalized_filters.get(slug, {})
            for site_key in sites:
                adapter = self.adapters.get(site_key.lower())
                if adapter:
                    url = adapter.build_url(neighborhood, limits)
                    start_urls.append({
                        "url": url,
                        "label": adapter.label,
                        "userData": {"neighborhood": neighborhood, "site": adapter.display_name, "site_id": adapter.site_id},
                    })
        return start_urls

    async def _capture_screenshot(self, page: Any, prefix: str, neighborhood: str) -> None:
        screenshots_dir = self.diagnostics_dir / "screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = screenshots_dir / f"{prefix}_{self.slugify(neighborhood)}.png"
        try:
            await page.screenshot(path=str(screenshot_path))
        except Exception as exc:
            logger.warning(f"Failed to capture screenshot {screenshot_path}: {exc}")

    async def scrape(
        self,
        neighborhoods: list[str],
        max_pages_per_bairro: int = 2,
        sites: list[str] | None = None,
        price_filters: dict[str, Any] | None = None,
        min_pages_per_bairro: int = 2,
        min_new_listings_per_page: int = 3,
    ) -> list[dict[str, Any]]:
        """Asynchronously scrapes listings for the specified neighborhoods and sites."""
        self.pagination_stats = []
        self._seen_by_query = {}
        start_urls = self.generate_start_urls(neighborhoods, sites, price_filters)
        if not start_urls:
            logger.warning("No neighborhood links were generated.")
            return []

        logger.info(
            f"Starting Crawlee PlaywrightCrawler for {len(neighborhoods)} neighborhoods across {len(start_urls)} URLs..."
        )

        storage_dir = os.path.abspath(os.path.join("storage", "runs", uuid.uuid4().hex))
        configuration = Configuration(storage_dir=storage_dir, purge_on_start=True)
        dataset = await Dataset.open(configuration=configuration)

        crawler = PlaywrightCrawler(
            configuration=configuration,
            max_requests_per_crawl=len(start_urls) * max_pages_per_bairro * 2,
            max_request_retries=3,
            concurrency_settings=ConcurrencySettings(
                min_concurrency=1,
                max_concurrency=1,
                desired_concurrency=1,
            ),
            browser_launch_options={
                "args": [
                    "--disable-gpu",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                ]
            },
            browser_new_context_options={
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "locale": "pt-BR",
                "timezone_id": "America/Sao_Paulo",
                "viewport": {"width": 1366, "height": 768},
            },
            headless=True,
            browser_type="chromium",
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

        # Register standard handlers for page-based adapters
        def make_standard_handler(adapter: BasePortalAdapter):
            async def handler(context: PlaywrightCrawlingContext) -> None:
                url = context.request.url
                user_data = context.request.user_data
                neighborhood = user_data.get("neighborhood", "Unknown")
                site = user_data.get("site", adapter.display_name)
                context.log.info(f"Processing {site}: {neighborhood} -> {url}")

                page = context.page
                try:
                    await page.wait_for_selector(adapter.wait_selector, timeout=adapter.timeout_ms)
                except Exception as e:
                    context.log.warning(f"Timeout on {site} for {neighborhood}: {e}")
                    await self._capture_screenshot(page, f"{adapter.site_id}_error", neighborhood)
                    raise RuntimeError(f"{site} did not render listing cards for {neighborhood}") from e

                raw_listings = await page.evaluate(adapter.card_script)
                if not raw_listings:
                    await self._capture_screenshot(page, f"{adapter.site_id}_empty", neighborhood)
                    raise RuntimeError(f"{site} returned no extractable cards for {neighborhood}")

                filtered, rejected_by_location = adapter.filter_listings(raw_listings, neighborhood)
                context.log.info(
                    f"[{site}] Found {len(filtered)} listings in {neighborhood}; {rejected_by_location} rejected by location."
                )

                current_page = adapter.extract_current_page(url)
                should_continue = await store_page(
                    filtered, site, neighborhood, current_page, context, rejected_by_location
                )
                if should_continue:
                    next_url = adapter.build_next_page_url(url, current_page + 1)
                    if next_url:
                        await context.add_requests([
                            Request.from_url(url=next_url, label=adapter.label, user_data=user_data)
                        ])

            return handler

        crawler.router.handler("VIVAREAL")(make_standard_handler(self.adapters["vivareal"]))
        crawler.router.handler("ZAP")(make_standard_handler(self.adapters["zap"]))
        crawler.router.handler("OLX")(make_standard_handler(self.adapters["olx"]))
        crawler.router.handler("IMOVELWEB")(make_standard_handler(self.adapters["imovelweb"]))

        # Special handler for QuintoAndar (dynamic "Ver mais" button)
        @crawler.router.handler("QUINTOANDAR")
        async def handle_quintoandar(context: PlaywrightCrawlingContext) -> None:
            adapter = self.adapters["quintoandar"]
            url = context.request.url
            user_data = context.request.user_data
            neighborhood = user_data.get("neighborhood", "Unknown")
            site = user_data.get("site", adapter.display_name)
            context.log.info(f"Processing {site}: {neighborhood} -> {url}")

            page = context.page
            try:
                await page.wait_for_selector(adapter.wait_selector, timeout=adapter.timeout_ms)
            except Exception as e:
                context.log.warning(f"Timeout on {site} for {neighborhood}: {e}")
                await self._capture_screenshot(page, f"{adapter.site_id}_error", neighborhood)
                raise RuntimeError(f"{site} did not render listing cards for {neighborhood}") from e

            current_page = 1
            while True:
                raw_listings = await page.evaluate(adapter.card_script)
                filtered, rejected_by_location = adapter.filter_listings(raw_listings, neighborhood)
                context.log.info(
                    f"[{site}] Found {len(filtered)} listings in {neighborhood}; {rejected_by_location} rejected by location."
                )
                should_continue = await store_page(
                    filtered, site, neighborhood, current_page, context, rejected_by_location
                )
                if not should_continue:
                    break

                more_button = page.get_by_role("button", name="Ver mais", exact=True)
                if await more_button.count() != 1:
                    context.log.info(f"[{site}] No more results for {neighborhood}.")
                    break

                card_count = len(raw_listings)
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
                    context.log.warning(f"[{site}] 'Load more' did not load new cards in {neighborhood}: {e}")
                    break
                current_page += 1

        requests = [
            Request.from_url(url=item["url"], label=item["label"], user_data=item["userData"])
            for item in start_urls
        ]
        try:
            await crawler.run(requests)
            results = await dataset.get_data()
            items = results.items
            logger.info(f"Scrape complete. Total listings collected by Crawlee: {len(items)}")

            if items and self.raw_data_path:
                df = pd.DataFrame(items)
                os.makedirs(os.path.dirname(self.raw_data_path), exist_ok=True)
                df.to_csv(
                    self.raw_data_path,
                    index=False,
                    sep=";",
                    decimal=",",
                    encoding="utf-8-sig",
                )
                logger.info(f"Listings saved to: {self.raw_data_path}")

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
        neighborhoods: list[str],
        max_pages_per_bairro: int = 2,
        sites: list[str] | None = None,
        price_filters: dict[str, Any] | None = None,
        min_pages_per_bairro: int = 2,
        min_new_listings_per_page: int = 3,
    ) -> list[dict[str, Any]]:
        """Wrapper method to run the async crawler synchronously."""
        return asyncio.run(
            self.scrape(
                neighborhoods,
                max_pages_per_bairro,
                sites,
                price_filters,
                min_pages_per_bairro,
                min_new_listings_per_page,
            )
        )
