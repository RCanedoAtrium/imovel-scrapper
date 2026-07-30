from property_scraper.parking import classify_parking


def test_classifies_explicit_free_parking_with_evidence():
    result = classify_parking("Apartamento com 2 vagas de garagem livres, cobertas e demarcadas.")

    assert result["parking_type"] == "independent"
    assert result["parking_review_status"] == "approved"
    assert "vagas de garagem livres" in result["parking_evidence"]


def test_classifies_tandem_parking_as_ineligible_even_when_called_free():
    result = classify_parking("Possui 2 vagas de garagem livres, em linha e cobertas.")

    assert result["parking_type"] == "tandem"
    assert result["parking_review_status"] == "rejected"


def test_classifies_stacked_and_missing_parking_without_guessing():
    stacked = classify_parking("Garagem: 1 vaga presa.")
    missing = classify_parking("Apartamento de 70 m² com uma vaga coberta.")

    assert stacked["parking_type"] == "blocked"
    assert stacked["parking_review_status"] == "rejected"
    assert missing["parking_type"] == "unknown"
    assert missing["parking_review_status"] == "pending_review"
