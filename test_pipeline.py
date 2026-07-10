import os
import sys
import pandas as pd
import re
import unicodedata
from processor import DataProcessor

def test_normalization():
    print("Testing street name normalization...")
    assert DataProcessor.normalize_street_name("Rua Ptolomeu") == "PTOLOMEU"
    assert DataProcessor.normalize_street_name("Avenida Raja Gabaglia") == "RAJA GABAGLIA"
    assert DataProcessor.normalize_street_name("R. Engenheiro Walter Kurrle") == "ENGENHEIRO WALTER KURRLE"
    assert DataProcessor.normalize_street_name("Av. Luiz Paulo Franco") == "LUIZ PAULO FRANCO"
    print("Normalization tests passed!")

def test_ad_parsing():
    print("\nTesting ad detail parsing...")
    # Mock row
    row = {
        'title': "Apartamento com 4 dormitórios, 2 suítes e 5 banheiros disponível para compra em Rua Engenheiro Walter Kurrle, Belvedere, Belo Horizonte",
        'price': "R$ 3.500.000Cond. R$ 2.300 • IPTU R$ 11.500",
        'area': "Tamanho do imóvel 215 m²",
        'neighborhood': "BELVEDERE",
        'link': "https://example.com/ad",
        'url': "https://example.com/ad"
    }
    
    # We will simulate the loader block
    title_clean = row['title']
    price_text = row['price']
    search_text = title_clean + " " + price_text
    
    quartos_match = re.search(r'(\d+)\s*(?:dormitório|dormitorio|quarto|quartos|quarto\(s\)|dormitório\(s\))', search_text, re.IGNORECASE)
    quartos = int(quartos_match.group(1)) if quartos_match else 0
    
    banheiros_match = re.search(r'(\d+)\s*(?:banheiro|banheiros|banho|banhos|banheiro\(s\)|banho\(s\))', search_text, re.IGNORECASE)
    banheiros = int(banheiros_match.group(1)) if banheiros_match else 0
    
    vagas_match = re.search(r'(\d+)\s*(?:vaga|vagas|garagem|garagens|vaga\(s\)|garagem\(s\))', search_text, re.IGNORECASE)
    vagas = int(vagas_match.group(1)) if vagas_match else 0
    
    condo_match = re.search(r'Cond\.\s*R\$\s*([\d.]+)', price_text, re.IGNORECASE)
    condo = DataProcessor.clean_numeric(condo_match.group(1)) if condo_match else 0.0
    
    iptu_match = re.search(r'IPTU\s*R\$\s*([\d.]+)', price_text, re.IGNORECASE)
    iptu_ad = DataProcessor.clean_numeric(iptu_match.group(1)) if iptu_match else 0.0
    
    def parse_ad_address(t):
        if not isinstance(t, str) or ' em ' not in t:
            return "", "", ""
        addr_part = t.split(' em ')[-1].strip()
        cep_match = re.search(r'\b\d{5}-?\d{3}\b', addr_part)
        cep_val = re.sub(r'\D', '', cep_match.group(0)) if cep_match else ""
        if cep_match:
            addr_part = addr_part.replace(cep_match.group(0), "")
        addr_part = re.sub(r'\bBelo Horizonte\b', '', addr_part, flags=re.IGNORECASE)
        addr_part = re.sub(r'\bMG\b', '', addr_part, flags=re.IGNORECASE)
        numbers = re.findall(r'\b\d{1,5}\b', addr_part)
        num_val = ""
        if numbers:
            num_val = numbers[-1]
        street_match = re.search(r'\b(rua|r|avenida|av|alameda|al|praca|pc|travessa|tv)\s+([^,0-9]+)', addr_part, re.IGNORECASE)
        street_val = ""
        if street_match:
            street_val = street_match.group(1) + " " + street_match.group(2)
            street_val = street_val.strip()
        return street_val, num_val, cep_val

    street, number, cep = parse_ad_address(title_clean)
    
    print(f"Parsed -> Quartos: {quartos}, Banheiros: {banheiros}, Vagas: {vagas}, Condo: {condo}, IPTU: {iptu_ad}")
    print(f"Address -> Street: {street}, Number: {number}, CEP: {cep}")
    
    assert quartos == 4
    assert banheiros == 5
    assert condo == 2300.0
    assert iptu_ad == 11500.0
    assert "Engenheiro Walter Kurrle" in street
    print("Ad parsing tests passed!")

def test_full_pipeline():
    print("\nTesting full processor integration...")
    processor = DataProcessor(itbi_dir="data/raw/itbi", iptu_dir="data/raw/iptu")
    
    # Process ITBI
    print("Processing ITBI...")
    stats, top_20 = processor.process_itbi()
    assert not stats.empty
    assert len(top_20) > 0
    print("ITBI stats generated successfully.")
    
    # Process IPTU
    print("Processing IPTU...")
    iptu_df = processor.process_iptu()
    assert not iptu_df.empty
    print("IPTU reference table generated successfully.")
    
    # Run full export pipeline
    print("Running analyze_and_export...")
    success = processor.analyze_and_export(discount_threshold=0.20, classifier_mode="ml")
    assert success
    print("Excel report generated successfully!")

if __name__ == "__main__":
    test_normalization()
    test_ad_parsing()
    test_full_pipeline()
    print("\nALL PIPELINE TESTS PASSED!")
