import os
import re
import ast
import unicodedata
import pandas as pd

class DataProcessor:
    """Processes ITBI sales records, extracts and consolidates scraper listings, and generates the final Excel report."""
    
    def __init__(self, itbi_dir="data/raw/itbi", iptu_dir="data/raw/iptu", scraper_csv="data/raw/anuncios_vivareal.csv", output_excel="relatorio_flip_bh.xlsx"):
        self.itbi_dir = itbi_dir
        self.iptu_dir = iptu_dir
        self.scraper_csv = scraper_csv
        self.output_excel = output_excel

    @staticmethod
    def normalize_column_name(col):
        """Standardizes column names by stripping BOM, accents, and converting to uppercase."""
        col = str(col).strip().replace('\ufeff', '')
        col = unicodedata.normalize('NFD', col)
        col = "".join([c for c in col if unicodedata.category(c) != 'Mn'])
        return col.upper()

    @staticmethod
    def normalize_street_name(street):
        """Standardizes a street name for matching (removes accents, typical prefixes/suffixes, and punctuation)."""
        if pd.isna(street):
            return ""
        street = str(street).strip().upper()
        # Remove accents
        street = unicodedata.normalize('NFD', street)
        street = "".join([c for c in street if unicodedata.category(c) != 'Mn'])
        # Replace punctuation
        street = re.sub(r'[^\w\s]', ' ', street)
        # Standardize common street prefixes: RUA, AVENIDA, ALAMEDA, TRAVESSA, etc.
        street = re.sub(r'^(RUA\b|R\b|AVENIDA\b|AV\b|ALAMEDA\b|AL\b|TRAVESSA\b|TV\b|PRACA\b|PC\b|RODOVIA\b|ROD\b)\s+', '', street)
        # Remove multiple spaces
        street = re.sub(r'\s+', ' ', street).strip()
        return street

    @staticmethod
    def clean_numeric(val):
        """Cleans and parses a numeric value from string representations (handling Brazilian comma decimals)."""
        if pd.isna(val):
            return 0.0
        if isinstance(val, (int, float)):
            return float(val)
        val_str = str(val).strip()
        
        # If it's a range like "30 - 110" or "30 a 110", pick the first one
        if '-' in val_str:
            val_str = val_str.split('-')[0].strip()
        elif ' a ' in val_str:
            val_str = val_str.split(' a ')[0].strip()
            
        # Remove all non-numeric characters except comma, dot, and minus sign
        val_str = re.sub(r'[^\d,.-]', '', val_str)
        if not val_str:
            return 0.0
            
        # Clean Brazilian formatting: dots as thousands, comma as decimal
        if ',' in val_str:
            parts = val_str.split(',')
            integer_part = parts[0].replace('.', '')
            decimal_part = parts[1]
            val_str = f"{integer_part}.{decimal_part}"
        else:
            # If no comma, check if dot is a thousands separator (e.g. 2.350.000 or 1.200)
            if '.' in val_str:
                if val_str.count('.') > 1 or len(val_str.split('.')[-1]) == 3:
                    val_str = val_str.replace('.', '')
        try:
            return float(val_str)
        except ValueError:
            return 0.0

    @staticmethod
    def safe_parse_literal(val):
        """Safely parses string representations of dictionaries/lists from scraped CSVs."""
        if not isinstance(val, str):
            return val
        val_str = val.strip()
        if (val_str.startswith('{') and val_str.endswith('}')) or (val_str.startswith('[') and val_str.endswith(']')):
            try:
                return ast.literal_eval(val_str)
            except:
                return val
        return val

    def extract_nested_field(self, row_dict, possible_keys):
        """Extracts field values from a dictionary, handling flat column names, nested dicts, and stringified dicts."""
        for key in possible_keys:
            # Check if key exists in flattened dictionary
            if key in row_dict and pd.notna(row_dict[key]):
                val = row_dict[key]
                return self.safe_parse_literal(val)
            
            # Check if it is a nested path like 'listing.prices.price'
            if '.' in key:
                parts = key.split('.')
                curr = row_dict
                found = True
                for part in parts:
                    if isinstance(curr, str):
                        curr = self.safe_parse_literal(curr)
                    if isinstance(curr, dict) and part in curr:
                        curr = curr[part]
                    else:
                        found = False
                        break
                if found and pd.notna(curr):
                    return self.safe_parse_literal(curr)
        return None

    def process_itbi(self, min_transactions=5):
        """Consolidates all 2026 ITBI CSVs, calculates real sold m² price, and identifies Top 20 neighborhoods."""
        if not os.path.exists(self.itbi_dir):
            print(f"Diretório de ITBI não existe: {self.itbi_dir}")
            return pd.DataFrame(), []
            
        all_files = [os.path.join(self.itbi_dir, f) for f in os.listdir(self.itbi_dir) if f.endswith('.csv')]
        if not all_files:
            print("Nenhum arquivo CSV de ITBI encontrado na pasta.")
            return pd.DataFrame(), []
            
        dfs = []
        for filepath in all_files:
            try:
                df = pd.read_csv(filepath, sep=';', encoding='utf-8-sig', dtype=str)
                df.columns = [self.normalize_column_name(c) for c in df.columns]
                dfs.append(df)
                print(f"Processador: Carregado {os.path.basename(filepath)} com {len(df)} linhas.")
            except Exception as e:
                print(f"Erro ao ler arquivo {filepath}: {e}")
                
        if not dfs:
            return pd.DataFrame(), []
            
        combined = pd.concat(dfs, ignore_index=True)
        
        # Filter for Residential units
        occup_col = 'DESCRICAO TIPO OCUPACAO (UNIDADE)'
        if occup_col in combined.columns:
            combined = combined[combined[occup_col].str.upper().fillna('') == 'RESIDENCIAL']
            
        # Clean numeric fields
        area_col = 'AREA CONSTRUIDA ADQUIRIDA'
        val_decl_col = 'VALOR DECLARADO'
        val_base_col = 'VALOR BASE CALCULO'
        bairro_col = 'BAIRRO'
        
        combined['CLEAN_AREA'] = combined[area_col].apply(self.clean_numeric)
        combined['CLEAN_VAL_DECL'] = combined[val_decl_col].apply(self.clean_numeric)
        combined['CLEAN_VAL_BASE'] = combined[val_base_col].apply(self.clean_numeric)
        
        # Max between declared price and venal base price
        combined['CLEAN_PRICE'] = combined[['CLEAN_VAL_DECL', 'CLEAN_VAL_BASE']].max(axis=1)
        
        # Filter valid listings
        combined = combined[(combined['CLEAN_PRICE'] > 20000) & (combined['CLEAN_AREA'] > 10)]
        
        # Calculate price per m2
        combined['PRICE_M2'] = combined['CLEAN_PRICE'] / combined['CLEAN_AREA']
        combined['CLEAN_BAIRRO'] = combined[bairro_col].astype(str).str.strip().str.upper()
        
        # --- PBH IPTU ENRICHMENT EXTRACTOR ---
        addr_col = 'ENDERECO'
        building_years = {}
        cep_to_bairro = {}
        
        if addr_col in combined.columns:
            def parse_row_addr(addr):
                if pd.isna(addr):
                    return "", "", ""
                parts = str(addr).split('-')
                street_num = parts[0].strip()
                tokens = street_num.split()
                if not tokens:
                    return "", "", ""
                num = tokens[-1]
                street = " ".join(tokens[:-1])
                
                cep = ""
                for part in parts:
                    part_clean = re.sub(r'\D', '', part)
                    if len(part_clean) == 8:
                        cep = part_clean
                        break
                return street, num, cep

            parsed = combined[addr_col].apply(parse_row_addr)
            combined['NORM_STREET'] = [self.normalize_street_name(x[0]) for x in parsed]
            combined['CLEAN_NUM'] = [str(int(float(x[1]))) if self.clean_numeric(x[1]) > 0 else '' for x in parsed]
            combined['CLEAN_CEP'] = [x[2] for x in parsed]
            
            # Build CEP to Bairro mapping
            cep_groups = combined[combined['CLEAN_CEP'] != ''].groupby('CLEAN_CEP')
            for cep, group in cep_groups:
                most_common_bairro = group['CLEAN_BAIRRO'].mode()
                if not most_common_bairro.empty:
                    cep_to_bairro[cep] = most_common_bairro.iloc[0]
            
            # Build building years mapping
            year_col = 'ANO DE CONSTRUCAO (UNIDADE)'
            if year_col in combined.columns:
                combined['CLEAN_YEAR'] = combined[year_col].apply(self.clean_numeric)
                valid_years = combined[combined['CLEAN_YEAR'] > 1800]
                building_groups = valid_years.groupby(['NORM_STREET', 'CLEAN_NUM'])
                for g_key, group in building_groups:
                    median_year = group['CLEAN_YEAR'].median()
                    building_years[g_key] = int(median_year)
                    
        self.cep_to_bairro = cep_to_bairro
        self.itbi_building_years = building_years
        self.itbi_clean_df = combined
        # -------------------------------------
        
        # Group stats by neighborhood
        stats = combined.groupby('CLEAN_BAIRRO').agg(
            itbi_m2_mean=('PRICE_M2', 'mean'),
            itbi_m2_median=('PRICE_M2', 'median'),
            itbi_sales_count=('PRICE_M2', 'count'),
            itbi_total_volume=('CLEAN_PRICE', 'sum')
        ).reset_index()
        
        # Filter neighborhoods to avoid statistical noise
        stats = stats[stats['itbi_sales_count'] >= min_transactions]
        
        # Pick top 20 neighborhoods by average price m2
        top_20 = stats.sort_values(by='itbi_m2_mean', ascending=False).head(20)
        top_20_list = top_20['CLEAN_BAIRRO'].tolist()
        
        print(f"Top 20 bairros mais valorizados identificados no ITBI 2026: {top_20_list}")
        return stats, top_20_list

    def process_iptu(self):
        """Processes the Cadastro Imobiliario CSV, groups by building, and extracts structural stats."""
        # Find latest IPTU CSV file
        if not os.path.exists(self.iptu_dir):
            print(f"Diretório de IPTU não existe: {self.iptu_dir}")
            return pd.DataFrame()
            
        all_files = [os.path.join(self.iptu_dir, f) for f in os.listdir(self.iptu_dir) if f.endswith('.csv')]
        if not all_files:
            print("Nenhum arquivo CSV de IPTU encontrado na pasta.")
            return pd.DataFrame()
            
        all_files.sort(reverse=True)
        filepath = all_files[0]
        print(f"Processando IPTU a partir de: {filepath}")
        
        try:
            df = pd.read_csv(filepath, sep=';', encoding='latin1', dtype=str)
        except Exception as e:
            try:
                df = pd.read_csv(filepath, sep=';', encoding='utf-8', dtype=str)
            except Exception as e2:
                print(f"Erro ao ler arquivo IPTU: {e2}")
                return pd.DataFrame()
                
        df.columns = [self.normalize_column_name(c) for c in df.columns]
        
        # Filter for APARTAMENTO
        type_col = 'TIPO_CONSTRUTIVO'
        if type_col in df.columns:
            df = df[df[type_col].str.upper().fillna('') == 'APARTAMENTO']
            
        # Clean numeric fields
        area_col = 'AREA_CONSTRUCAO'
        num_col = 'NUMERO_IMOVEL'
        cep_col = 'CEP'
        padrao_col = 'PADRAO_ACABAMENTO'
        
        df['CLEAN_AREA'] = df[area_col].apply(self.clean_numeric)
        df['CLEAN_NUM'] = df[num_col].fillna('').apply(lambda x: str(int(float(x))) if self.clean_numeric(x) > 0 else '')
        df['CLEAN_CEP'] = df[cep_col].fillna('').apply(lambda x: re.sub(r'\D', '', str(x)))
        df['NORM_STREET'] = df['NOME_LOGRADOURO'].apply(self.normalize_street_name)
        
        # Group by building
        df = df[(df['NORM_STREET'] != '') & (df['CLEAN_NUM'] != '')]
        
        grouped = df.groupby(['NORM_STREET', 'CLEAN_NUM', 'CLEAN_CEP'])
        
        building_records = []
        for name, group in grouped:
            norm_street, clean_num, clean_cep = name
            
            # Majority vote of pattern
            patterns = group[padrao_col].dropna()
            pred_pattern = patterns.mode().iloc[0] if not patterns.empty else None
            
            # Area range
            min_area = group['CLEAN_AREA'].min()
            max_area = group['CLEAN_AREA'].max()
            
            # Map Bairro if cep_to_bairro exists
            bairro = ""
            if hasattr(self, 'cep_to_bairro') and clean_cep in self.cep_to_bairro:
                bairro = self.cep_to_bairro[clean_cep]
                
            # Get building year if exists
            year = None
            if hasattr(self, 'itbi_building_years') and (norm_street, clean_num) in self.itbi_building_years:
                year = self.itbi_building_years[(norm_street, clean_num)]
                
            building_records.append({
                'NORM_STREET': norm_street,
                'NUMERO': clean_num,
                'CEP': clean_cep,
                'PADRAO_PREDOMINANTE': pred_pattern,
                'AREA_MIN': min_area,
                'AREA_MAX': max_area,
                'BAIRRO': bairro,
                'ANO_CONSTRUCAO': year
            })
            
        building_df = pd.DataFrame(building_records)
        print(f"Total de edifícios consolidados do IPTU: {len(building_df)}")
        self.iptu_buildings_df = building_df
        return building_df

    def load_and_normalize_listings(self):
        """Loads raw scraped listings and extracts standard fields (price, area, neighborhood, link, title)."""
        if not os.path.exists(self.scraper_csv):
            print(f"Arquivo de anúncios não encontrado: {self.scraper_csv}")
            return pd.DataFrame()
            
        try:
            df = pd.read_csv(self.scraper_csv, low_memory=False, dtype=str)
        except Exception as e:
            print(f"Erro ao abrir arquivo de anúncios {self.scraper_csv}: {e}")
            return pd.DataFrame()
            
        print(f"Processando {len(df)} anúncios brutos coletados...")
        
        normalized_records = []
        for idx, row in df.iterrows():
            row_dict = row.to_dict()
            
            # Extract price
            price = self.extract_nested_field(row_dict, ['price', 'listing.price', 'listing.prices.price', 'priceValue'])
            if isinstance(price, dict):
                price = price.get('value', price.get('amount', 0))
            price_clean = self.clean_numeric(price)
            
            # Extract area
            area = self.extract_nested_field(row_dict, ['area', 'usableAreaM2', 'usableAreas', 'usable_area', 'listing.usableAreas', 'listing.usableArea'])
            if isinstance(area, list) and len(area) > 0:
                area = area[0]
            area_clean = self.clean_numeric(area)
            
            # Extract neighborhood
            neighborhood = self.extract_nested_field(row_dict, ['neighborhood', 'address.neighborhood', 'listing.address.neighborhood', 'listing.address.bairro', 'bairro'])
            if isinstance(neighborhood, dict):
                neighborhood = neighborhood.get('name', neighborhood.get('neighborhood', ''))
            neighborhood_clean = str(neighborhood or '').strip().upper()
            
            # Extract link
            link = self.extract_nested_field(row_dict, ['url', 'link', 'listing.url', 'listing.link'])
            if not isinstance(link, str):
                link = ""
            if link and not link.startswith('http'):
                link = f"https://www.vivareal.com.br{link}"
                
            # Extract title
            title = self.extract_nested_field(row_dict, ['title', 'listing.title', 'name'])
            title_clean = str(title or 'Apartamento à venda').strip()
            
            # --- PARSING DETAILS ---
            # Search both title and price texts
            price_text = str(price) if price else ""
            search_text = title_clean + " " + price_text
            
            # Bedrooms
            quartos_match = re.search(r'(\d+)\s*(?:dormitório|dormitorio|quarto|quartos|quarto\(s\)|dormitório\(s\))', search_text, re.IGNORECASE)
            quartos = int(quartos_match.group(1)) if quartos_match else 0
            
            # Bathrooms
            banheiros_match = re.search(r'(\d+)\s*(?:banheiro|banheiros|banho|banhos|banheiro\(s\)|banho\(s\))', search_text, re.IGNORECASE)
            banheiros = int(banheiros_match.group(1)) if banheiros_match else 0
            
            # Vagas
            vagas_match = re.search(r'(\d+)\s*(?:vaga|vagas|garagem|garagens|vaga\(s\)|garagem\(s\))', search_text, re.IGNORECASE)
            vagas = int(vagas_match.group(1)) if vagas_match else 0
            
            # Condominium
            condo_match = re.search(r'Cond\.\s*R\$\s*([\d.]+)', price_text, re.IGNORECASE)
            condo = self.clean_numeric(condo_match.group(1)) if condo_match else 0.0
            
            # IPTU
            iptu_match = re.search(r'IPTU\s*R\$\s*([\d.]+)', price_text, re.IGNORECASE)
            iptu_ad = self.clean_numeric(iptu_match.group(1)) if iptu_match else 0.0
            
            # Parse Address (Street, Number, CEP)
            def parse_ad_address(t):
                if not isinstance(t, str) or ' em ' not in t:
                    return "", "", ""
                
                addr_part = t.split(' em ')[-1].strip()
                
                # Extract CEP if present
                cep_match = re.search(r'\b\d{5}-?\d{3}\b', addr_part)
                cep_val = re.sub(r'\D', '', cep_match.group(0)) if cep_match else ""
                if cep_match:
                    addr_part = addr_part.replace(cep_match.group(0), "")
                    
                # Remove "Belo Horizonte" and "MG"
                addr_part = re.sub(r'\bBelo Horizonte\b', '', addr_part, flags=re.IGNORECASE)
                addr_part = re.sub(r'\bMG\b', '', addr_part, flags=re.IGNORECASE)
                
                # Try to find a street number (usually last digits)
                numbers = re.findall(r'\b\d{1,5}\b', addr_part)
                num_val = ""
                if numbers:
                    num_val = numbers[-1]
                    
                # Extract street name
                street_match = re.search(r'\b(rua|r|avenida|av|alameda|al|praca|pc|travessa|tv)\s+([^,0-9]+)', addr_part, re.IGNORECASE)
                street_val = ""
                if street_match:
                    street_val = street_match.group(1) + " " + street_match.group(2)
                    street_val = street_val.strip()
                    
                return street_val, num_val, cep_val

            street_raw, number_raw, cep_raw = parse_ad_address(title_clean)
            norm_street = self.normalize_street_name(street_raw)
            clean_num = str(int(float(number_raw))) if self.clean_numeric(number_raw) > 0 else ""
            clean_cep = re.sub(r'\D', '', cep_raw)
            # -----------------------
            
            if price_clean > 1000 and area_clean > 5:
                price_m2 = price_clean / area_clean
                normalized_records.append({
                    'BAIRRO': neighborhood_clean,
                    'TITULO': title_clean,
                    'PRECO_ANUNCIADO': price_clean,
                    'AREA_M2': area_clean,
                    'M2_ANUNCIADO': price_m2,
                    'LINK': link,
                    'QUARTOS': quartos,
                    'BANHEIROS': banheiros,
                    'VAGAS': vagas,
                    'CONDOMINIO': condo,
                    'IPTU': iptu_ad,
                    'NORM_STREET': norm_street,
                    'NUMERO': clean_num,
                    'CEP': clean_cep
                })

                
        normalized_df = pd.DataFrame(normalized_records)
        print(f"Total de anúncios limpos e válidos: {len(normalized_df)}")
        return normalized_df

    def analyze_and_export(self, discount_threshold=0.20, classifier_mode='ml'):
        """Cross-references ads with PBH data and exports a formatted Excel workbook with opportunities."""
        # 1. Process ITBI
        stats_df, top_20_bairros = self.process_itbi()
        if stats_df.empty:
            print("Processamento interrompido: base de ITBI vazia.")
            return False
            
        # 2. Process IPTU (consolidates buildings)
        building_df = self.process_iptu()
        if building_df.empty:
            print("Aviso: base de IPTU vazia ou nao pôde ser processada. O Motor Deterministico nao funcionara.")
            
        # 3. Process Listings
        listings_df = self.load_and_normalize_listings()
        if listings_df.empty:
            print("Processamento interrompido: sem anúncios válidos para comparar.")
            return False
            
        # --- TRAINING CLASSIFIERS & HEURISTICS ---
        # Compute percentiles per neighborhood
        neighborhood_percentiles = {}
        itbi_df = self.itbi_clean_df
        if not itbi_df.empty:
            grouped_itbi = itbi_df.groupby('CLEAN_BAIRRO')
            for name, group in grouped_itbi:
                p30 = group['PRICE_M2'].quantile(0.30)
                p80 = group['PRICE_M2'].quantile(0.80)
                median_area = group['CLEAN_AREA'].median()
                neighborhood_percentiles[name] = {'p30': p30, 'p80': p80, 'median_area': median_area}
                
        # Calculate P3 median prices for flip detection
        p3_m2_medias = {}
        if not itbi_df.empty:
            p3_data = itbi_df[itbi_df['PADRAO ACABAMENTO (UNIDADE)'].astype(str).str.upper() == 'P3']
            if not p3_data.empty:
                p3_m2_medias = p3_data.groupby('CLEAN_BAIRRO')['PRICE_M2'].median().to_dict()
                
        # Train Random Forest Classifier
        rf_model = None
        bairro_encoder = {}
        if not itbi_df.empty:
            try:
                from sklearn.ensemble import RandomForestClassifier
                from sklearn.preprocessing import LabelEncoder
                
                # Prepare training features
                train_df = itbi_df[['CLEAN_BAIRRO', 'CLEAN_AREA', 'CLEAN_PRICE', 'PRICE_M2', 'PADRAO ACABAMENTO (UNIDADE)']].dropna().copy()
                train_df.rename(columns={'PADRAO ACABAMENTO (UNIDADE)': 'PADRAO'}, inplace=True)
                
                # Filter for valid patterns
                train_df = train_df[train_df['PADRAO'].isin(['P1', 'P2', 'P3', 'P4', 'P5'])]
                
                if len(train_df) > 10:
                    le = LabelEncoder()
                    train_df['BAIRRO_ENC'] = le.fit_transform(train_df['CLEAN_BAIRRO'])
                    bairro_encoder = dict(zip(le.classes_, le.transform(le.classes_)))
                    
                    X = train_df[['BAIRRO_ENC', 'CLEAN_AREA', 'CLEAN_PRICE', 'PRICE_M2']]
                    y = train_df['PADRAO']
                    
                    rf_model = RandomForestClassifier(n_estimators=100, random_state=42)
                    rf_model.fit(X, y)
                    print(f"Random Forest treinado com sucesso com {len(train_df)} registros do ITBI.")
            except Exception as e:
                print(f"Erro ao treinar classificador Random Forest: {e}. Usando Heurística como fallback.")
                
        # --- PIPELINE CLASSIFICATION FLOW ---
        classified_records = []
        for idx, row in listings_df.iterrows():
            bairro = row['BAIRRO']
            norm_street = row['NORM_STREET']
            num = row['NUMERO']
            cep = row['CEP']
            area = row['AREA_M2']
            price_ad = row['PRECO_ANUNCIADO']
            m2_ad = row['M2_ANUNCIADO']
            
            # Default classification variables
            padrao = None
            ano_construcao = None
            metodo = "NAO_CLASSIFICADO"
            confianca = 0.0
            
            # Step 2: Deterministic Matching with IPTU
            matched_row = None
            if not building_df.empty:
                # Try CEP + Number first
                if cep and num:
                    match_cep_num = building_df[(building_df['CEP'] == cep) & (building_df['NUMERO'] == num)]
                    if not match_cep_num.empty:
                        matched_row = match_cep_num.iloc[0]
                
                # Try Street + Number + Bairro
                if matched_row is None and norm_street and num:
                    match_street_num = building_df[(building_df['NORM_STREET'] == norm_street) & (building_df['NUMERO'] == num)]
                    if not match_street_num.empty:
                        if bairro:
                            match_bairro = match_street_num[match_street_num['BAIRRO'] == bairro]
                            if not match_bairro.empty:
                                matched_row = match_bairro.iloc[0]
                            else:
                                matched_row = match_street_num.iloc[0]
                        else:
                            matched_row = match_street_num.iloc[0]
                            
            if matched_row is not None:
                padrao = matched_row['PADRAO_PREDOMINANTE']
                ano_construcao = matched_row['ANO_CONSTRUCAO']
                metodo = "DETERMINISTICO_IPTU"
                confianca = 1.0
            else:
                # Step 3: Probabilistic Motor
                # Option A: Heuristics
                padrao_heuristica = "P3"
                if bairro in neighborhood_percentiles:
                    p_info = neighborhood_percentiles[bairro]
                    if m2_ad <= p_info['p30']:
                        padrao_heuristica = "P3"
                    elif m2_ad <= p_info['p80']:
                        padrao_heuristica = "P4"
                    else:
                        if area >= p_info['median_area'] * 0.8:
                            padrao_heuristica = "P5"
                        else:
                            padrao_heuristica = "P4"
                            
                # Option B: Machine Learning (Random Forest)
                padrao_ml = None
                confianca_ml = 0.0
                if rf_model is not None:
                    bairro_enc = bairro_encoder.get(bairro, -1)
                    if bairro_enc == -1:
                        # Default to first available neighborhood code
                        bairro_enc = list(bairro_encoder.values())[0] if bairro_encoder else 0
                        
                    X_test = pd.DataFrame([{
                        'BAIRRO_ENC': bairro_enc,
                        'CLEAN_AREA': area,
                        'CLEAN_PRICE': price_ad,
                        'PRICE_M2': m2_ad
                    }])
                    
                    try:
                        pred_pattern = rf_model.predict(X_test)[0]
                        probs = rf_model.predict_proba(X_test)[0]
                        class_idx = list(rf_model.classes_).index(pred_pattern)
                        confianca_ml = float(probs[class_idx])
                        padrao_ml = pred_pattern
                    except Exception as e_ml:
                        print(f"Erro na predicao de ML: {e_ml}")
                        
                # Choose active model
                if classifier_mode == 'ml' and padrao_ml is not None:
                    padrao = padrao_ml
                    confianca = confianca_ml
                    metodo = "ESTIMADO_ITBI_ML"
                else:
                    padrao = padrao_heuristica
                    confianca = 0.70
                    metodo = "ESTIMADO_ITBI_HEURISTICA"
                    
            # Step 4: Flip Arbitrage Detection
            is_flip = False
            p3_media = p3_m2_medias.get(bairro, 0.0)
            if padrao == 'P5' and p3_media > 0.0 and m2_ad <= p3_media * 1.15:
                is_flip = True
                
            # Retrieve ITBI stats for discount calculation
            itbi_mean_m2 = 0.0
            itbi_median_m2 = 0.0
            sales_count = 0
            if bairro in neighborhood_percentiles:
                # Merge stats from stats_df
                bairro_stats = stats_df[stats_df['CLEAN_BAIRRO'] == bairro]
                if not bairro_stats.empty:
                    itbi_mean_m2 = float(bairro_stats['itbi_m2_mean'].iloc[0])
                    itbi_median_m2 = float(bairro_stats['itbi_m2_median'].iloc[0])
                    sales_count = int(bairro_stats['itbi_sales_count'].iloc[0])
                    
            discount = 0.0
            if itbi_mean_m2 > 0.0:
                discount = (itbi_mean_m2 - m2_ad) / itbi_mean_m2
                
            classified_row = row.to_dict()
            classified_row.update({
                'PADRAO_FINAL': padrao,
                'ANO_CONSTRUCAO': ano_construcao if (ano_construcao is not None and pd.notna(ano_construcao)) else "",
                'METODO_CLASSIFICACAO': metodo,
                'CONFIANCA': confianca,
                'MEDIA_P3_BAIRRO': p3_media,
                'POTENCIAL_FLIP': "SIM" if is_flip else "NAO",
                'M2_MEDIO_ITBI': itbi_mean_m2,
                'M2_MEDIANO_ITBI': itbi_median_m2,
                'QTD_VENDAS_ITBI_2026': sales_count,
                'DIFERENCA_PERCENTUAL': discount
            })
            classified_records.append(classified_row)
            
        merged = pd.DataFrame(classified_records)
        
        # Sort all ads by flip potential first, then discount descending
        merged.sort_values(by=['POTENCIAL_FLIP', 'DIFERENCA_PERCENTUAL'], ascending=[False, False], inplace=True)
        
        # Create opportunities sheet (announced m2 is cheaper by discount_threshold OR is a flip)
        opportunities = merged[(merged['POTENCIAL_FLIP'] == 'SIM') | (merged['DIFERENCA_PERCENTUAL'] >= discount_threshold)].copy()
        
        # Formats neighborhood summary for output
        stats_export = stats_df[stats_df['CLEAN_BAIRRO'].isin(top_20_bairros)].copy()
        stats_export.sort_values(by='itbi_m2_mean', ascending=False, inplace=True)
        stats_export.rename(columns={
            'CLEAN_BAIRRO': 'BAIRRO',
            'itbi_m2_mean': 'M2_MEDIO_REAL',
            'itbi_m2_median': 'M2_MEDIANO_REAL',
            'itbi_sales_count': 'QTD_VENDAS_2026',
            'itbi_total_volume': 'VOLUME_TOTAL_R$'
        }, inplace=True)
        
        # Prepare datasets for writing
        sheet_opportunities = opportunities.copy()
        sheet_all_ads = merged.copy()
        
        # Write to Excel using xlsxwriter for formatting
        print(f"Gerando planilha formatada: {self.output_excel}")
        with pd.ExcelWriter(self.output_excel, engine='xlsxwriter') as writer:
            sheet_opportunities.to_excel(writer, sheet_name='Oportunidades', index=False)
            stats_export.to_excel(writer, sheet_name='Resumo_Bairros', index=False)
            sheet_all_ads.to_excel(writer, sheet_name='Todos_Anuncios', index=False)
            
            workbook  = writer.book
            
            # Setup Excel Formats
            header_format = workbook.add_format({
                'bold': True,
                'font_color': 'white',
                'bg_color': '#1B365D',
                'align': 'center',
                'valign': 'vcenter',
                'border': 1
            })
            
            # Formatting styles
            fmt_default = workbook.add_format()
            fmt_currency = workbook.add_format({'num_format': 'R$ #,##0.00'})
            fmt_m2 = workbook.add_format({'num_format': '#,##0.00" m²"'})
            fmt_price_m2 = workbook.add_format({'num_format': 'R$ #,##0.00"/m²"'})
            fmt_percent = workbook.add_format({'num_format': '0.0%'})
            fmt_integer = workbook.add_format({'num_format': '#,##0', 'align': 'center'})
            fmt_center = workbook.add_format({'align': 'center'})
            
            # Flip Highlight formats (Coral Red)
            flip_bg = '#F8D7DA'
            flip_fg = '#721C24'
            fmt_flip_default = workbook.add_format({'bg_color': flip_bg, 'font_color': flip_fg})
            fmt_flip_currency = workbook.add_format({'bg_color': flip_bg, 'font_color': flip_fg, 'num_format': 'R$ #,##0.00'})
            fmt_flip_m2 = workbook.add_format({'bg_color': flip_bg, 'font_color': flip_fg, 'num_format': '#,##0.00" m²"'})
            fmt_flip_price_m2 = workbook.add_format({'bg_color': flip_bg, 'font_color': flip_fg, 'num_format': 'R$ #,##0.00"/m²"'})
            fmt_flip_percent = workbook.add_format({'bg_color': flip_bg, 'font_color': flip_fg, 'num_format': '0.0%'})
            fmt_flip_integer = workbook.add_format({'bg_color': flip_bg, 'font_color': flip_fg, 'num_format': '#,##0', 'align': 'center'})
            fmt_flip_center = workbook.add_format({'bg_color': flip_bg, 'font_color': flip_fg, 'align': 'center'})

            # High Discount formats (Green)
            green_bg = '#D4EDDA'
            green_fg = '#155724'
            fmt_green_default = workbook.add_format({'bg_color': green_bg, 'font_color': green_fg})
            fmt_green_currency = workbook.add_format({'bg_color': green_bg, 'font_color': green_fg, 'num_format': 'R$ #,##0.00'})
            fmt_green_m2 = workbook.add_format({'bg_color': green_bg, 'font_color': green_fg, 'num_format': '#,##0.00" m²"'})
            fmt_green_price_m2 = workbook.add_format({'bg_color': green_bg, 'font_color': green_fg, 'num_format': 'R$ #,##0.00"/m²"'})
            fmt_green_percent = workbook.add_format({'bg_color': green_bg, 'font_color': green_fg, 'num_format': '0.0%'})
            fmt_green_integer = workbook.add_format({'bg_color': green_bg, 'font_color': green_fg, 'num_format': '#,##0', 'align': 'center'})
            fmt_green_center = workbook.add_format({'bg_color': green_bg, 'font_color': green_fg, 'align': 'center'})

            # Medium Discount formats (Yellow)
            yellow_bg = '#FFF3CD'
            yellow_fg = '#856404'
            fmt_yellow_default = workbook.add_format({'bg_color': yellow_bg, 'font_color': yellow_fg})
            fmt_yellow_currency = workbook.add_format({'bg_color': yellow_bg, 'font_color': yellow_fg, 'num_format': 'R$ #,##0.00'})
            fmt_yellow_m2 = workbook.add_format({'bg_color': yellow_bg, 'font_color': yellow_fg, 'num_format': '#,##0.00" m²"'})
            fmt_yellow_price_m2 = workbook.add_format({'bg_color': yellow_bg, 'font_color': yellow_fg, 'num_format': 'R$ #,##0.00"/m²"'})
            fmt_yellow_percent = workbook.add_format({'bg_color': yellow_bg, 'font_color': yellow_fg, 'num_format': '0.0%'})
            fmt_yellow_integer = workbook.add_format({'bg_color': yellow_bg, 'font_color': yellow_fg, 'num_format': '#,##0', 'align': 'center'})
            fmt_yellow_center = workbook.add_format({'bg_color': yellow_bg, 'font_color': yellow_fg, 'align': 'center'})
            
            # Helper function to write sheet rows dynamically
            def write_sheet_data(worksheet, df_to_write):
                worksheet.set_row(0, 26, header_format)
                for col_idx, col_name in enumerate(df_to_write.columns):
                    worksheet.write(0, col_idx, col_name, header_format)
                    
                for row_idx, row_data in enumerate(df_to_write.itertuples(index=False), start=1):
                    # Style logic
                    is_flip = getattr(row_data, 'POTENCIAL_FLIP', 'NAO') == 'SIM'
                    discount = getattr(row_data, 'DIFERENCA_PERCENTUAL', 0.0)
                    
                    if is_flip:
                        c_def, c_curr, c_m2, c_pm2, c_pct, c_int, c_cnt = fmt_flip_default, fmt_flip_currency, fmt_flip_m2, fmt_flip_price_m2, fmt_flip_percent, fmt_flip_integer, fmt_flip_center
                    elif discount >= 0.30:
                        c_def, c_curr, c_m2, c_pm2, c_pct, c_int, c_cnt = fmt_green_default, fmt_green_currency, fmt_green_m2, fmt_green_price_m2, fmt_green_percent, fmt_green_integer, fmt_green_center
                    elif discount >= 0.20:
                        c_def, c_curr, c_m2, c_pm2, c_pct, c_int, c_cnt = fmt_yellow_default, fmt_yellow_currency, fmt_yellow_m2, fmt_yellow_price_m2, fmt_yellow_percent, fmt_yellow_integer, fmt_yellow_center
                    else:
                        c_def, c_curr, c_m2, c_pm2, c_pct, c_int, c_cnt = fmt_default, fmt_currency, fmt_m2, fmt_price_m2, fmt_percent, fmt_integer, fmt_center
                        
                    for col_idx, val in enumerate(row_data):
                        col_name = df_to_write.columns[col_idx]
                        if col_name in ['PRECO_ANUNCIADO', 'CONDOMINIO', 'IPTU', 'MEDIA_P3_BAIRRO', 'M2_MEDIO_ITBI', 'M2_MEDIANO_ITBI']:
                            worksheet.write(row_idx, col_idx, val, c_curr)
                        elif col_name in ['AREA_M2']:
                            worksheet.write(row_idx, col_idx, val, c_m2)
                        elif col_name in ['M2_ANUNCIADO']:
                            worksheet.write(row_idx, col_idx, val, c_pm2)
                        elif col_name in ['DIFERENCA_PERCENTUAL', 'CONFIANCA']:
                            worksheet.write(row_idx, col_idx, val, c_pct)
                        elif col_name in ['QUARTOS', 'BANHEIROS', 'VAGAS', 'ANO_CONSTRUCAO', 'QTD_VENDAS_ITBI_2026']:
                            worksheet.write(row_idx, col_idx, val, c_int)
                        elif col_name in ['LINK']:
                            if val:
                                worksheet.write_url(row_idx, col_idx, val, c_cnt, string="Ver Anúncio")
                            else:
                                worksheet.write(row_idx, col_idx, "", c_cnt)
                        elif col_name in ['POTENCIAL_FLIP', 'PADRAO_FINAL', 'METODO_CLASSIFICACAO']:
                            worksheet.write(row_idx, col_idx, val, c_cnt)
                        else:
                            worksheet.write(row_idx, col_idx, val, c_def)
                            
                # Column widths styling
                col_widths = {
                    'BAIRRO': 20, 'TITULO': 35, 'PRECO_ANUNCIADO': 16, 'AREA_M2': 12, 'M2_ANUNCIADO': 16,
                    'LINK': 15, 'QUARTOS': 10, 'BANHEIROS': 12, 'VAGAS': 10, 'CONDOMINIO': 14, 'IPTU': 12,
                    'PADRAO_FINAL': 15, 'ANO_CONSTRUCAO': 15, 'METODO_CLASSIFICACAO': 22, 'CONFIANCA': 12,
                    'POTENCIAL_FLIP': 18, 'DIFERENCA_PERCENTUAL': 22
                }
                for col_idx, col_name in enumerate(df_to_write.columns):
                    width = col_widths.get(col_name, 15)
                    worksheet.set_column(col_idx, col_idx, width)

            # Style Oportunidades
            worksheet_ops = writer.sheets['Oportunidades']
            write_sheet_data(worksheet_ops, sheet_opportunities)
            
            # Style Resumo Bairros
            worksheet_stats = writer.sheets['Resumo_Bairros']
            worksheet_stats.set_row(0, 26, header_format)
            worksheet_stats.set_column('A:A', 22)
            worksheet_stats.set_column('B:B', 20, fmt_price_m2)
            worksheet_stats.set_column('C:C', 20, fmt_price_m2)
            worksheet_stats.set_column('D:D', 18, fmt_integer)
            worksheet_stats.set_column('E:E', 22, fmt_currency)
            
            # Style Todos os Anúncios
            worksheet_all = writer.sheets['Todos_Anuncios']
            write_sheet_data(worksheet_all, sheet_all_ads)
            
        print(f"Sucesso! Relatório criado com {len(opportunities)} oportunidades identificadas.")
        return True


if __name__ == "__main__":
    # Test loading and cleaning local ITBI files
    processor = DataProcessor()
    stats, top_20 = processor.process_itbi()
    print(f"\nStats shape: {stats.shape if not stats.empty else 'Empty'}")
