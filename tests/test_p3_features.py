import json
from pathlib import Path

import pandas as pd
import pytest

from property_scraper.alerts import generate_alerts, save_alerts
from property_scraper.analysis import analyze
from property_scraper.config import load_settings
from property_scraper.enrichment import DetailEnricher
from property_scraper.history import ListingHistoryStore
from property_scraper.normalization import extract_listing_id, is_legacy_listing
from property_scraper.pattern_model import RandomForestPatternModel
from property_scraper.processing import prepare_all


def test_extract_listing_id_and_legacy_filter():
    # Imovelweb
    iw_legacy = "https://www.imovelweb.com.br/propriedades/apartamento-savassi-2950123456.html"
    iw_modern = "https://www.imovelweb.com.br/propriedades/apartamento-savassi-3015987654.html"
    assert extract_listing_id(iw_legacy, site="imovelweb") == "2950123456"
    assert extract_listing_id(iw_modern, site="imovelweb") == "3015987654"
    assert is_legacy_listing("2950123456", site="imovelweb") is True
    assert is_legacy_listing("3015987654", site="imovelweb") is False

    # OLX
    olx_legacy = "https://mg.olx.com.br/belo-horizonte-e-regiao/imoveis/apartamento-1340123456"
    olx_modern = "https://mg.olx.com.br/belo-horizonte-e-regiao/imoveis/apartamento-1450987654"
    assert extract_listing_id(olx_legacy, site="olx") == "1340123456"
    assert extract_listing_id(olx_modern, site="olx") == "1450987654"
    assert is_legacy_listing("1340123456", site="olx") is True
    assert is_legacy_listing("1450987654", site="olx") is False

    # VivaReal / Zap
    vr_url = "https://www.vivareal.com.br/imovel/apartamento-2-quartos-lourdes-id-2748192849/"
    zap_url = "https://www.zapimoveis.com.br/imovel/venda-apartamento-codigo-2748192849/"
    assert extract_listing_id(vr_url, site="vivareal") == "2748192849"
    assert extract_listing_id(zap_url, site="zap") == "2748192849"

    # QuintoAndar
    qa_url = "https://www.quintoandar.com.br/imovel/894123456/"
    assert extract_listing_id(qa_url, site="quintoandar") == "894123456"


def test_persistent_listing_history_and_price_drops(tmp_path: Path):
    db_path = tmp_path / "history.db"
    store = ListingHistoryStore(db_path)

    # Day 1: First observation at R$ 500,000
    df_day1 = pd.DataFrame([{
        "source": "vivareal",
        "url": "https://www.vivareal.com.br/imovel/id-12345678/",
        "neighborhood": "LOURDES",
        "street": "RUA DA BAHIA",
        "number": "100",
        "price": 500000.0,
        "area_m2": 70.0,
        "collected_date": "2026-08-01",
    }])
    enriched_day1 = store.record_observations(df_day1)
    rec1 = enriched_day1.iloc[0]
    assert rec1["listing_id"] == "12345678"
    assert rec1["first_seen"] == "2026-08-01"
    assert rec1["initial_price"] == 500000.0
    assert rec1["previous_price"] == 500000.0
    assert rec1["price_drop"] == 0.0
    assert rec1["days_on_market"] == 0
    assert rec1["observations_count"] == 1

    # Day 15: Second observation with price drop to R$ 450,000 (10% drop)
    df_day2 = pd.DataFrame([{
        "source": "vivareal",
        "url": "https://www.vivareal.com.br/imovel/id-12345678/",
        "neighborhood": "LOURDES",
        "street": "RUA DA BAHIA",
        "number": "100",
        "price": 450000.0,
        "area_m2": 70.0,
        "collected_date": "2026-08-16",
    }])
    enriched_day2 = store.record_observations(df_day2)
    rec2 = enriched_day2.iloc[0]
    assert rec2["first_seen"] == "2026-08-01"
    assert rec2["initial_price"] == 500000.0
    assert rec2["previous_price"] == 500000.0
    assert rec2["price_drop"] == 50000.0
    assert rec2["price_drop_pct"] == pytest.approx(0.10)
    assert rec2["days_on_market"] == 15
    assert rec2["observations_count"] == 2

    # Query price drops from store
    drops = store.get_price_drops(min_drop_pct=0.05)
    assert len(drops) == 1
    assert drops.iloc[0]["listing_id"] == "12345678"
    assert drops.iloc[0]["price_drop_pct"] == pytest.approx(0.10)


def test_pattern_model_cross_validation_and_regularization():
    # Build synthetic transaction data with 2 classes
    rows = []
    for i in range(30):
        rows.append({
            "neighborhood": "SAVASSI" if i % 2 == 0 else "LOURDES",
            "street": "RUA SERGIPE" if i % 3 == 0 else "RUA ALAGOAS",
            "area_m2": 60.0 + i * 2,
            "finish_pattern": "P3" if i < 15 else "P4",
            "competence": "2026-01",
        })
    df = pd.DataFrame(rows)
    model = RandomForestPatternModel(df)

    # Verify cross-validation metadata
    assert "cv_accuracy" in model.metadata
    assert model.metadata["cv_accuracy"] is not None
    assert model.metadata["cv_f1_macro"] is not None
    assert "P3" in model.metadata["per_class_f1"]
    assert "P4" in model.metadata["per_class_f1"]
    assert model.metadata["min_samples_leaf"] >= 2


def test_detail_page_enricher_parser_and_shortlist():
    sample_html = """
    <html>
      <head>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "Apartment",
          "datePosted": "2026-07-15",
          "floorSize": {"@type": "QuantitativeValue", "value": 85.0}
        }
        </script>
      </head>
      <body>
        <h1>Apartamento à venda no Lourdes</h1>
        <p>Área útil: 85 m² | Área total: 110 m²</p>
        <p>Possui 2 vagas de garagem livres e cobertas.</p>
        <p>Condomínio: R$ 650,00 | IPTU: R$ 2.400,00</p>
      </body>
    </html>
    """
    detail = DetailEnricher.parse_detail_html(sample_html, url="https://example.test/1")
    assert detail["area_m2"] == 85.0
    assert detail["total_area_m2"] == 110.0
    assert detail["parking_review_status"] == "approved"
    assert detail["parking_type"] in {"independent", "covered", "covered_independent"}
    assert detail["condominium"] == 650.0
    assert detail["advertised_iptu"] == 2400.0
    assert detail["publication_date"] == "2026-07-15"

    # Test shortlist enrichment
    listings = pd.DataFrame([{
        "url": "https://example.test/shortlist_1",
        "ranking_status": "parking_pending_review",
        "opportunity_status": "estimated",
        "source": "vivareal",
    }])

    def mock_fetcher(url: str) -> str:
        return sample_html

    enriched = DetailEnricher.enrich_shortlist(listings, fetcher=mock_fetcher)
    assert enriched.iloc[0]["parking_review_status"] == "approved"
    assert enriched.iloc[0]["condominium"] == 650.0


def test_richer_ranking_carrying_costs_and_opportunity_score(tmp_path: Path):
    # Setup test project files
    root = tmp_path
    config = {
        "neighborhoods": ["ANCHIETA"],
        "sites": ["vivareal"],
        "discount_threshold": 0.20,
        "rules": {
            "pattern_min_confidence": 0.50,
            "min_price_drop_pct": 0.03,
        },
    }
    config_file = root / "config.json"
    config_file.write_text(json.dumps(config), encoding="utf-8")
    settings = load_settings(config_file)

    itbi_dir = root / "data" / "itbi"
    iptu_dir = root / "data" / "iptu"
    listing_dir = root / "data" / "listings" / "run1"
    for d in (itbi_dir, iptu_dir, listing_dir):
        d.mkdir(parents=True, exist_ok=True)

    # ITBI reference: ANCHIETA mean ~10,000/m²
    pd.DataFrame([{
        "Endereco Completo": "Rua Ptolomeu 130 - 30360250",
        "Bairro": "ANCHIETA",
        "Ano de Construcao (Unidade)": "2015",
        "Area Construida Adquirida": "80,00",
        "Padrao Acabamento (Unidade)": "P3" if i < 6 else "P4",
        "Descrição Tipo Ocupacao (Unidade)": "RESIDENCIAL",
        "Valor Declarado": "800.000,00",
        "Valor Base Calculo": "800.000,00",
    } for i in range(12)]).to_csv(itbi_dir / "itbi_2026.csv", sep=";", index=False)

    pd.DataFrame([{
        "ID_IPTU_CTM": "1", "AREA_CONSTRUCAO": "80.0", "TIPO_CONSTRUTIVO": "APARTAMENTO",
        "PADRAO_ACABAMENTO": "P4", "NOME_LOGRADOURO": "PTOLOMEU", "NUMERO_IMOVEL": "130",
        "CEP": "30360250",
    }]).to_csv(iptu_dir / "iptu_2026.csv", index=False)

    # Listing: 80 m² at R$ 560,000 (7,000/m² -> 30% discount), 2 vagas cobertas, condo R$ 600, IPTU R$ 2400
    pd.DataFrame([{
        "title": "Apartamento a venda em Rua Ptolomeu 130 Anchieta Belo Horizonte 30360-250 com 2 vagas cobertas",
        "price": "560.000",
        "area": "80",
        "bedrooms": "3",
        "condominium": "600",
        "advertised_iptu": "2400",
        "link": "https://www.vivareal.com.br/imovel/id-99887766/",
        "site": "vivareal",
        "neighborhood": "ANCHIETA",
    }]).to_csv(listing_dir / "run.csv", index=False)
    (root / "data" / "listings" / "latest.txt").write_text("run1", encoding="utf-8")

    prepare_all(settings)
    analyze(settings)

    analysis = pd.read_csv(root / "data" / "processed" / "analysis.csv", sep=";", decimal=",", encoding="utf-8-sig")
    assert len(analysis) == 1
    rec = analysis.iloc[0]

    # Verify carrying cost calculation
    # Monthly carrying cost = 600 + (2400 / 12) = 800
    assert rec["carrying_cost_monthly"] == pytest.approx(800.0)
    assert rec["carrying_cost_m2"] == pytest.approx(10.0)

    # Verify rental yield
    assert rec["estimated_monthly_rent"] > 0
    assert rec["estimated_gross_yield"] > 0

    # Verify opportunity score
    assert rec["opportunity_score"] > 50.0
    assert "discount:" in str(rec["score_breakdown"])
    assert "parking:" in str(rec["score_breakdown"])
    assert "evidence:" in str(rec["score_breakdown"])

    # Test alerts generation (§4.5)
    alerts = generate_alerts(analysis, settings)
    assert alerts["new_opportunities_count"] == 1
    assert "✨ New Opportunities" in alerts["markdown_digest"]

    json_path, md_path = save_alerts(alerts, settings)
    assert json_path.exists()
    assert md_path.exists()
    assert "ANCHIETA" in md_path.read_text(encoding="utf-8")


def test_history_retention_pruning(tmp_path: Path):
    from datetime import datetime, timezone

    db_path = tmp_path / "history.db"
    store = ListingHistoryStore(db_path)

    # 1. Observation from 200 days ago
    df_old = pd.DataFrame([{
        "source": "vivareal",
        "url": "https://www.vivareal.com.br/imovel/id-old-123/",
        "neighborhood": "LOURDES",
        "price": 600000.0,
        "area_m2": 80.0,
        "collected_date": "2026-01-01",
    }])
    store.record_observations(df_old, now=datetime(2026, 1, 1, tzinfo=timezone.utc), retention_days=180)
    assert len(store.get_history()) == 1

    # 2. Observation today (2026-08-01, 212 days later)
    df_new = pd.DataFrame([{
        "source": "vivareal",
        "url": "https://www.vivareal.com.br/imovel/id-new-456/",
        "neighborhood": "SAVASSI",
        "price": 500000.0,
        "area_m2": 70.0,
        "collected_date": "2026-08-01",
    }])
    store.record_observations(df_new, now=datetime(2026, 8, 1, tzinfo=timezone.utc), retention_days=180)

    # Old record (>180 days without new observation) should have been pruned
    hist = store.get_history()
    assert len(hist) == 1
    assert hist.iloc[0]["listing_id"] == "id-new-456"
