# Belo Horizonte Property Scraper

A Python pipeline for collecting apartment listings in Belo Horizonte, Brazil, combining them with public PBH property and transaction data, and ranking potential investment opportunities.

It downloads official IPTU and ITBI datasets, collects listings from major Brazilian real-estate portals, normalizes the data, estimates each property's finish pattern, and exports an Excel report.

## Features

- Synchronizes official IPTU and ITBI CSV files from the [PBH open-data portal](https://dados.pbh.gov.br/).
- Collects apartment listings from Viva Real, ZAP Imóveis, OLX, QuintoAndar, and Imovelweb.
- Applies local price and area filters even when a portal ignores URL filters.
- Uses adaptive pagination, URL deduplication, and card-validation diagnostics.
- Normalizes Brazilian addresses, neighborhoods, CEPs, prices, and floor areas.
- Matches listings to municipal building records when possible; otherwise predicts finish patterns with a Random Forest model that excludes price features.
- Ranks opportunities by neighborhood and finish-pattern price differences, with parking-space review rules.
- Creates a formatted Excel report and machine-readable run manifests.

## Requirements

- Python 3.10+
- Chromium for Playwright

## Installation

```powershell
python -m pip install -r requirements.txt
playwright install chromium
```

## Configuration

Project settings are stored in [config.json](config.json). The most useful options are:

```json
{
  "neighborhoods": ["SAVASSI", "LOURDES"],
  "sites": ["vivareal", "zap", "olx", "quintoandar", "imovelweb"],
  "max_price": 800000,
  "min_area_m2": 40,
  "pages_per_neighborhood": 4,
  "min_pages_per_neighborhood": 2,
  "min_new_listings_per_page": 3,
  "discount_threshold": 0.20
}
```

`max_price` is applied across every configured neighborhood. It can also be overridden for a single collection:

```powershell
python main.py scrape --max-price 800000
```

## Usage

Run the stages individually:

```powershell
python main.py sync-pbh
python main.py scrape
python main.py prepare
python main.py analyze
python main.py report
```

Or run the full workflow:

```powershell
python main.py run --report
```

To reuse recently collected reference data and listings:

```powershell
python main.py run --skip-reference-sync --skip-scrape --report
```

Use the card validator before enabling a new or changed portal:

```powershell
python main.py validate-cards --sites vivareal --neighborhood BELVEDERE --max-price 800000
```

## Outputs

- `data/iptu` and `data/itbi`: downloaded PBH source files.
- `data/listings/<run_id>`: portal-level raw listings and pagination statistics.
- `data/processed`: normalized datasets, analysis output, and manifests.
- `data/reports`: the latest `bh_property_report_*.xlsx` file and run manifest.
- `data/diagnostics/card-validation`: portal-card validation reports and samples.

The project retains only the current canonical source snapshots, latest listing run, latest report, and latest run manifest.

## Documentation

- [Finish-pattern model](documentacao/modelo.md)
- [Scraper maintenance guide](documentacao/scraper.md)
- [Investment criteria](documentacao/criterios_compra_revenda.md)

## Notes

The codebase and generated outputs are in English. Brazilian Portuguese terms are retained only where they are needed to consume PBH source columns, construct portal URLs, or parse listing text.
