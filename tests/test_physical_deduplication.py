import pandas as pd

from property_scraper.analysis import deduplicate_physical_properties


def test_deduplicate_physical_properties_multi_broker():
    # 3 listings of the same apartment by 3 different brokers at different dates and prices
    df = pd.DataFrame([
        {
            "neighborhood": "SANTA LUCIA",
            "street": "RUA CHOPIN",
            "bedrooms": 3,
            "area_m2": 110.0,
            "price": 850_000.0,
            "collected_at": "2026-08-10T10:00:00Z",
            "collected_date": "2026-08-10",
            "url": "https://portal1.com/imovel-1",
            "title": "Apartamento 3 quartos Santa Lucia",
        },
        {
            "neighborhood": "SANTA LUCIA",
            "street": "RUA CHOPIN",
            "bedrooms": 3,
            "area_m2": 112.0,  # ~1.8% difference
            "price": 820_000.0,  # Lowest price
            "collected_at": "2026-08-12T10:00:00Z",
            "collected_date": "2026-08-12",
            "url": "https://portal2.com/imovel-2",
            "title": "Excelente apê 3 qtos Chopin",
        },
        {
            "neighborhood": "SANTA LUCIA",
            "street": "RUA CHOPIN",
            "bedrooms": 3,
            "area_m2": 110.0,
            "price": 840_000.0,
            "collected_at": "2026-08-14T10:00:00Z",  # Most recent
            "collected_date": "2026-08-14",
            "url": "https://portal3.com/imovel-3",
            "title": "Oportunidade Santa Lucia Chopin 3 quartos",
        },
        # Distinct apartment in same street but 4 bedrooms and 220m2
        {
            "neighborhood": "SANTA LUCIA",
            "street": "RUA CHOPIN",
            "bedrooms": 4,
            "area_m2": 220.0,
            "price": 1_600_000.0,
            "collected_at": "2026-08-14T11:00:00Z",
            "collected_date": "2026-08-14",
            "url": "https://portal1.com/cobertura-chopin",
            "title": "Cobertura 4 quartos Chopin",
        },
    ])

    deduped = deduplicate_physical_properties(df)

    # Should consolidate the 3 into 1, plus the distinct 4-bedroom unit = 2 physical properties
    assert len(deduped) == 2

    # Find the 3-bedroom unit cluster representative
    unit_3bed = deduped[deduped["bedrooms"] == 3].iloc[0]
    # The representative should be the most recent one (2026-08-14)
    assert unit_3bed["url"] == "https://portal3.com/imovel-3"
    assert unit_3bed["duplicate_count"] == 3
    # Minimum price among all 3 was 820_000
    assert unit_3bed["min_advertised_price"] == 820_000.0
    # Alternate urls should contain portal1 and portal2 links
    assert "https://portal1.com/imovel-1" in unit_3bed["alternate_urls"]
    assert "https://portal2.com/imovel-2" in unit_3bed["alternate_urls"]
    assert "https://portal3.com/imovel-3" not in unit_3bed["alternate_urls"]

    # Distinct unit check
    unit_4bed = deduped[deduped["bedrooms"] == 4].iloc[0]
    assert unit_4bed["duplicate_count"] == 1
    assert unit_4bed["min_advertised_price"] == 1_600_000.0
    assert unit_4bed["alternate_urls"] == ""


def test_deduplicate_bedroom_tolerance_and_street_enrichment():
    # Broker A lists as 2 bedrooms without street name, Broker B lists as 3 bedrooms with street name
    df = pd.DataFrame([
        {
            "neighborhood": "CRUZEIRO",
            "street": "",
            "number": "",
            "bedrooms": 2,
            "area_m2": 149.0,
            "price": 590_000.0,
            "collected_at": "2026-08-14T10:00:00Z",  # Most recent
            "collected_date": "2026-08-14",
            "url": "https://portal1.com/privativa-2qtos",
            "title": "Área privativa 2 quartos no Cruzeiro",
        },
        {
            "neighborhood": "CRUZEIRO",
            "street": "RUA MUZAMBINHO",
            "number": "100",
            "bedrooms": 3,
            "area_m2": 149.0,
            "price": 590_000.0,
            "collected_at": "2026-08-12T10:00:00Z",
            "collected_date": "2026-08-12",
            "url": "https://portal2.com/privativa-muzambinho-3qtos",
            "title": "Área privativa 3 quartos Rua Muzambinho",
        },
    ])

    deduped = deduplicate_physical_properties(df)
    assert len(deduped) == 1
    rep = deduped.iloc[0]
    assert rep["duplicate_count"] == 2
    assert rep["street"] == "RUA MUZAMBINHO"
    assert rep["number"] == "100"


def test_deduplicate_does_not_inflate_duplicate_count_from_history_snapshots():
    # Same listing collected on 3 consecutive days
    df = pd.DataFrame([
        {
            "neighborhood": "SION",
            "street": "RUA VENEZUELA",
            "bedrooms": 3,
            "area_m2": 90.0,
            "price": 600_000.0,
            "collected_at": f"2026-08-1{day}T10:00:00Z",
            "collected_date": f"2026-08-1{day}",
            "url": "https://portal1.com/imovel-history",
            "title": "Apartamento Sion 3 quartos",
        }
        for day in range(1, 4)
    ])
    deduped = deduplicate_physical_properties(df)
    assert len(deduped) == 1
    rep = deduped.iloc[0]
    # Even though 3 daily snapshots exist, there is only 1 distinct concurrent ad
    assert rep["duplicate_count"] == 1
    assert rep["alternate_urls"] == ""


def test_deduplicate_enriches_parking_and_inherits_qualification():
    # Broker A: more recent, price 700k, missing parking info, not qualified
    # Broker B: older, price 660k (~5.7% lower, within 8% tolerance), approved parking, qualified opportunity
    df = pd.DataFrame([
        {
            "neighborhood": "LOURDES",
            "street": "RUA CURITIBA",
            "bedrooms": 2,
            "area_m2": 70.0,
            "price": 700_000.0,
            "collected_at": "2026-08-14T10:00:00Z",
            "collected_date": "2026-08-14",
            "url": "https://portal1.com/broker-a",
            "title": "Apartamento Lourdes 2 quartos",
            "parking_review_status": "pending_review",
            "opportunity_status": "not_qualified",
            "ranking_status": "not_qualified",
            "ranking_priority": 0,
            "conservative_ref_m2": 11_000.0,
            "discount_vs_conservative_ref": 0.09,
        },
        {
            "neighborhood": "LOURDES",
            "street": "RUA CURITIBA",
            "bedrooms": 2,
            "area_m2": 70.0,
            "price": 660_000.0,
            "collected_at": "2026-08-12T10:00:00Z",
            "collected_date": "2026-08-12",
            "url": "https://portal2.com/broker-b",
            "title": "Lourdes com 1 vaga livre oportunidade",
            "parking_review_status": "approved",
            "parking_type": "independent",
            "parking_evidence": "1 vaga livre",
            "parking_confidence": "high",
            "parking_spaces": 1,
            "opportunity_status": "confirmed",
            "opportunity_criteria": "conservative_discount",
            "ranking_status": "parking_eligible",
            "ranking_priority": 3,
            "conservative_ref_m2": 11_000.0,
            "discount_vs_conservative_ref": 0.143,
        },
    ])
    deduped = deduplicate_physical_properties(df)
    assert len(deduped) == 1
    rep = deduped.iloc[0]
    assert rep["duplicate_count"] == 2
    assert rep["min_advertised_price"] == 660_000.0
    # Parking inherited from Broker B
    assert rep["parking_review_status"] == "approved"
    assert rep["parking_type"] == "independent"
    # Qualification inherited & updated with the lower advertised price
    assert rep["opportunity_status"] == "confirmed"
    assert rep["ranking_status"] == "parking_eligible"
    assert rep["ranking_priority"] == 3


