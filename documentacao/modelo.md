# Finish-pattern model

## Objective

Predict a listing's `finish_pattern` (`P1` through `P5`) when no deterministic IPTU match is available. The model uses only neighborhood, street, and built area. Listing price and price per square metre are explicitly excluded to prevent circular reasoning.

## Training data and normalization

- Source: monthly ITBI CSV files in `data/itbi`.
- Only residential transactions with area above 10 m², transaction value above R$20,000, and finish patterns from P1 to P5 are used.
- Transaction value is the greater of declared value and assessed value.
- Streets are extracted from the original PBH columns, normalized to uppercase ASCII, and stripped of street-type prefixes.
- The canonical normalization rules are in `src/property_scraper/normalization.py`.

## Configuration

`RandomForestPatternModel` in `src/property_scraper/pattern_model.py` uses a `RandomForestClassifier` with 300 trees and a fixed random seed. Streets with fewer than 20 training records are assigned to `RARE_STREET`; this prevents highly sparse street values from overfitting the model.

## Precedence

1. A deterministic IPTU match takes precedence.
2. The Random Forest is used only when there is no confirmed building match.
3. The analysis dataset records the pattern, classification method, confidence, and model version.

Price is never used in either the deterministic match or the model inference.
