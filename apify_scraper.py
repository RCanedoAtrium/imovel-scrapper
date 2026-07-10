import os
import re
import unicodedata
import pandas as pd
from dotenv import load_dotenv
from apify_client import ApifyClient

# Load environment variables
load_dotenv()

class ApifyScraper:
    """Scrapes property ads from VivaReal/Zap Imoveis using Apify Actor."""
    
    ACTOR_ID = "gio21/vivareal-zap-scraper"
    
    def __init__(self, raw_data_path="data/raw/anuncios_vivareal.csv"):
        self.raw_data_path = raw_data_path
        self.api_token = os.getenv("APIFY_API_TOKEN")
        
    @staticmethod
    def slugify(text):
        """Converts neighborhood name to VivaReal URL slug format."""
        if not isinstance(text, str):
            return ""
        text = text.lower().strip()
        # Remove accents
        text = unicodedata.normalize('NFD', text)
        text = "".join([c for c in text if unicodedata.category(c) != 'Mn'])
        # Replace non-alphanumeric with hyphens
        text = re.sub(r'[^a-z0-9]+', '-', text)
        # Strip duplicate or outer hyphens
        text = re.sub(r'-+', '-', text)
        return text.strip('-')

    def generate_vivareal_urls(self, neighborhoods):
        """Generates VivaReal search URLs for a list of neighborhoods."""
        urls = []
        for neighborhood in neighborhoods:
            slug = self.slugify(neighborhood)
            if slug:
                # Target residential apartments for sale in Belo Horizonte
                url = f"https://www.vivareal.com.br/venda/mg/belo-horizonte/bairros/{slug}/apartamento_residencial/"
                urls.append({
                    "url": url,
                    "userData": {"neighborhood": neighborhood}
                })
        return urls

    def run(self, neighborhoods, max_items_per_neighborhood=100):
        """Dispatches the Apify Actor to scrape ads in the target neighborhoods."""
        if not self.api_token or self.api_token.strip() == "your_apify_api_token_here":
            print("\n" + "="*80)
            print("AVISO: Chave de API da Apify (APIFY_API_TOKEN) não configurada no arquivo .env!")
            print("Por favor, adicione seu token do Apify no arquivo .env para executar o scraper.")
            print("="*80 + "\n")
            return None
            
        client = ApifyClient(self.api_token)
        
        start_urls = self.generate_vivareal_urls(neighborhoods)
        if not start_urls:
            print("Nenhum bairro válido para gerar URLs de busca.")
            return None
            
        print(f"Iniciando scrape na Apify para {len(neighborhoods)} bairros...")
        print(f"Total estimado de itens a coletar (máximo): {len(neighborhoods) * max_items_per_neighborhood}")
        
        # Calculate total maxItems to pass to Apify
        total_max_items = len(neighborhoods) * max_items_per_neighborhood
        
        run_input = {
            "location": "Belo Horizonte",
            "state": "MG",
            "city": "Belo Horizonte",
            "startUrls": start_urls,
            "maxItems": total_max_items,
        }
        
        try:
            print(f"Chamando o Actor: {self.ACTOR_ID}...")
            # Trigger run and wait for it to complete
            run = client.actor(self.ACTOR_ID).call(run_input=run_input)
            
            if hasattr(run, "default_dataset_id") and getattr(run, "default_dataset_id"):
                dataset_id = run.default_dataset_id
            elif isinstance(run, dict):
                dataset_id = run.get("defaultDatasetId") or run.get("default_dataset_id")
            else:
                try:
                    dataset_id = run["defaultDatasetId"]
                except:
                    dataset_id = getattr(run, "defaultDatasetId", None)
            print(f"Execução concluída! Dataset ID: {dataset_id}")
            print("Baixando dados brutos do dataset da Apify...")
            
            # Fetch all items from the dataset
            dataset_items = client.dataset(dataset_id).list_items().items
            print(f"Total de registros retornados da Apify: {len(dataset_items)}")
            
            if not dataset_items:
                print("Nenhum item retornado no dataset da Apify.")
                return None
                
            # Convert to DataFrame and save
            df = pd.DataFrame(dataset_items)
            
            # Ensure the output directory exists
            os.makedirs(os.path.dirname(self.raw_data_path), exist_ok=True)
            df.to_csv(self.raw_data_path, index=False, encoding='utf-8-sig')
            print(f"Dados brutos de anúncios salvos em: {self.raw_data_path}")
            return df
            
        except Exception as e:
            print(f"Erro durante a execução do scraper no Apify: {e}")
            return None

if __name__ == "__main__":
    # Test slugification
    test_bairros = ["Savassi", "Lourdes", "Sion", "Padre Eustáquio", "São Pedro"]
    scraper = ApifyScraper()
    urls = scraper.generate_vivareal_urls(test_bairros)
    print("Test URL Generation:")
    for u in urls:
        print(f" - {u['userData']['neighborhood']} -> {u['url']}")
