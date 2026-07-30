# Scraper maintenance guide

The scraper collects search-result cards rather than detail pages and produces a common contract for every portal. Priorities are correct location and filters, complete required fields, efficient deduplication and pagination, and reproducible diagnostics when a portal changes.

## Listing-card contract

Every portal must preserve the column order defined by `RAW_LISTING_COLUMNS`. `link`, `price`, and `area` are mandatory; unavailable optional fields must be empty strings.

The primary implementation is `crawlee_scraper.py`. Collection and validation are implemented in `src/property_scraper/sources/listings.py` and `src/property_scraper/card_validation.py`.

## Adding a portal

1. Map the portal's neighborhood search URL, supported price/area filters, and actual pagination behavior.
2. Add its identifier to `SUPPORTED_SITES`, generate start URLs, and enable it in `config.json` only after validation.
3. Create a dedicated Crawlee handler that waits for a stable card selector and fails explicitly if cards do not render.
4. Prefer semantic card selectors (`data-testid`, `data-qa`, and `aria-label`) over generated CSS classes; use text parsing only as a fallback.
5. Validate the listing location from visible card content. Search URLs can include sponsored properties from other neighborhoods or cities.
6. Register each page through the shared pagination logic; do not duplicate deduplication behavior in a handler.
7. Run `validate-cards` and confirm a complete card before enabling production collection.

## Filters and adaptive pagination

The collector sends price and minimum-area filters when a portal URL supports them, then reapplies the same rules locally. A listing identity is its normalized URL without query parameters or a trailing slash.

The scraper always visits at least `min_pages_per_neighborhood` pages. It keeps going only while the current page contributes at least `min_new_listings_per_page` new listings inside the configured filters, stopping at `pages_per_neighborhood`. Page metrics and stop decisions are written to `data/listings/<run_id>/pagination.json`.

## Quick validation

Run validation without changing the production listing pointer:

```powershell
python main.py validate-cards --sites newportal --neighborhood BELVEDERE --max-price 800000 --pages 4 --attempts 3
```

Review the generated `report.md`, `report.json`, `samples.csv`, and `pagination.json`. Approve a portal only when a complete card, correct location, and non-repeated URLs are present.

## Maintenance checklist

- Run `validate-cards` when a portal's volume drops or fields become empty.
- Inspect `pagination.json` for repeated pages or premature stops.
- Keep the portal name consistent across its URL generator, `site` field, CSV file, and configuration.
- Treat a missing card as a recoverable failure with diagnostic evidence.
- Update the contract, normalization, report, and tests together whenever a field changes.
