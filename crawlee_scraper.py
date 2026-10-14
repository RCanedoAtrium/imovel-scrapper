"""Facade for backwards compatibility with legacy imports and CLI executions.

Implementation has been modularized into `property_scraper.sources.portals`.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add src to sys.path if invoked directly
_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from property_scraper.sources.portals import (
    CARD_METADATA_HELPERS,
    GRUPO_OLX_CARD_SCRIPT,
    IMOVELWEB_CARD_SCRIPT,
    OLX_CARD_SCRIPT,
    QUINTOANDAR_CARD_SCRIPT,
    RAW_LISTING_COLUMNS,
    VIVAREAL_CARD_SCRIPT,
    ZAP_CARD_SCRIPT,
    BasePortalAdapter,
    CrawleeScraper,
    GrupoOLXAdapter,
    ImovelwebAdapter,
    OLXAdapter,
    QuintoAndarAdapter,
    add_query_params,
    slugify,
)

__all__ = [
    "RAW_LISTING_COLUMNS",
    "CARD_METADATA_HELPERS",
    "BasePortalAdapter",
    "slugify",
    "add_query_params",
    "GRUPO_OLX_CARD_SCRIPT",
    "VIVAREAL_CARD_SCRIPT",
    "ZAP_CARD_SCRIPT",
    "GrupoOLXAdapter",
    "OLX_CARD_SCRIPT",
    "OLXAdapter",
    "QUINTOANDAR_CARD_SCRIPT",
    "QuintoAndarAdapter",
    "IMOVELWEB_CARD_SCRIPT",
    "ImovelwebAdapter",
    "CrawleeScraper",
]

if __name__ == "__main__":
    scraper = CrawleeScraper()
    print("CrawleeScraper facade initialized successfully.")
