from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .errors import PipelineError


DEFAULT_NEIGHBORHOODS = [
    "BELVEDERE", "SAVASSI", "SANTO AGOSTINHO", "CRUZEIRO", "SAO BENTO",
    "LOURDES", "FUNCIONARIOS", "ANCHIETA", "BOA VIAGEM", "SANTA LUCIA", "CARMO", "SION", "SERRA",
]

# Promoted cards from these regions leak into target searches (GEMINI.md §3).
DEFAULT_EXCLUDED_REGIONS = [
    "RIBEIRAO DAS NEVES", "SANTA MONICA", "BARREIRO", "BETIM", "CONTAGEM",
]


@dataclass(frozen=True)
class Paths:
    root: Path
    data: Path
    iptu: Path
    itbi: Path
    listings: Path
    processed: Path
    reports: Path
    diagnostics: Path

    @classmethod
    def from_root(cls, root: Path) -> "Paths":
        data = root / "data"
        return cls(
            root=root,
            data=data,
            iptu=data / "iptu",
            itbi=data / "itbi",
            listings=data / "listings",
            processed=data / "processed",
            reports=data / "reports",
            diagnostics=data / "diagnostics",
        )

    def ensure(self) -> None:
        for path in (self.iptu, self.itbi, self.listings, self.processed, self.reports, self.diagnostics):
            path.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class Settings:
    paths: Paths
    neighborhoods: list[str] = field(default_factory=lambda: list(DEFAULT_NEIGHBORHOODS))
    sites: list[str] = field(default_factory=lambda: ["vivareal", "zap", "olx", "quintoandar", "imovelweb"])
    pages_per_neighborhood: int = 2
    min_pages_per_neighborhood: int = 2
    min_new_listings_per_page: int = 3
    discount_threshold: float = 0.20
    classifier: str = "ml"
    min_transactions: int = 5
    listing_freshness_days: int = 30
    max_price: float | None = None
    min_area_m2: float | None = None
    iptu_staleness_days: int = 75
    itbi_history_start: str = "2024-06"
    api_base_url: str = "https://dados.pbh.gov.br/api/3/action"
    iptu_dataset: str = "cadastro-imobiliario-regional-centro-sul"
    itbi_dataset: str = "itbi-relatorios"
    excluded_regions: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDED_REGIONS))


def load_settings(config_path: str | Path = "config.json") -> Settings:
    config_file = Path(config_path).resolve()
    root = config_file.parent
    raw: dict = {}
    if config_file.exists():
        raw = json.loads(config_file.read_text(encoding="utf-8"))
    pbh = raw.get("pbh", {})
    max_price = float(raw["max_price"]) if raw.get("max_price") is not None else None
    if max_price is not None and max_price <= 0:
        raise PipelineError("max_price deve ser maior que zero")
    min_area_m2 = float(raw["min_area_m2"]) if raw.get("min_area_m2") is not None else None
    if min_area_m2 is not None and min_area_m2 <= 0:
        raise PipelineError("min_area_m2 deve ser maior que zero")
    pages = int(raw.get("pages_per_neighborhood", 2))
    min_pages = int(raw.get("min_pages_per_neighborhood", min(2, pages)))
    min_new = int(raw.get("min_new_listings_per_page", 3))
    if pages < 1 or min_pages < 1 or min_pages > pages:
        raise PipelineError("A paginacao exige 1 <= min_pages_per_neighborhood <= pages_per_neighborhood")
    if min_new < 1:
        raise PipelineError("min_new_listings_per_page deve ser maior que zero")
    settings = Settings(
        paths=Paths.from_root(root),
        neighborhoods=raw.get("neighborhoods", DEFAULT_NEIGHBORHOODS),
        sites=raw.get("sites", ["vivareal", "zap", "olx", "quintoandar", "imovelweb"]),
        pages_per_neighborhood=pages,
        min_pages_per_neighborhood=min_pages,
        min_new_listings_per_page=min_new,
        discount_threshold=float(raw.get("discount_threshold", 0.20)),
        classifier=str(raw.get("classifier", "ml")),
        min_transactions=int(raw.get("min_transactions", 5)),
        listing_freshness_days=int(raw.get("listing_freshness_days", 30)),
        max_price=max_price,
        min_area_m2=min_area_m2,
        iptu_staleness_days=int(raw.get("iptu_staleness_days", 75)),
        itbi_history_start=str(raw.get("itbi_history_start", "2024-06")),
        api_base_url=str(pbh.get("api_base_url", "https://dados.pbh.gov.br/api/3/action")),
        iptu_dataset=str(pbh.get("iptu_dataset", "cadastro-imobiliario-regional-centro-sul")),
        itbi_dataset=str(pbh.get("itbi_dataset", "itbi-relatorios")),
        excluded_regions=list(raw.get("excluded_regions", DEFAULT_EXCLUDED_REGIONS)),
    )
    settings.paths.ensure()
    return settings
