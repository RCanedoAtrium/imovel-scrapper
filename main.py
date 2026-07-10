import os
import sys
import argparse
import json
from pbh_client import PBHClient
from crawlee_scraper import CrawleeScraper
from processor import DataProcessor

def main():
    parser = argparse.ArgumentParser(description="Ferramenta para identificar oportunidades de Flip Imobiliário em BH.")
    parser.add_argument("--force-download", action="store_true", help="Força o download dos relatórios de ITBI e IPTU da PBH mesmo se já existirem localmente.")
    parser.add_argument("--force-scrape", action="store_true", help="Força a execução de um novo scrape local mesmo se já existirem anúncios salvos localmente.")
    parser.add_argument("--discount", type=float, default=0.20, help="Margem de desconto mínima desejada comparado à média do ITBI (ex: 0.20 para 20%). Padrao: 0.20")
    parser.add_argument("--pages-per-bairro", type=int, default=2, help="Número de páginas de anúncios a raspar por bairro localmente. Padrao: 2")
    parser.add_argument("--sites", type=str, default="all", help="Lista de portais a raspar separados por vírgula (ex: vivareal,zap,olx,quintoandar,imovelweb) ou 'all'. Padrao: all")
    parser.add_argument("--classifier", type=str, choices=["ml", "heuristics"], default="ml", help="Tipo de motor de classificação probabilística a ser utilizado (ml ou heuristics). Padrao: ml")
    args = parser.parse_args()

    print("="*80)
    print("           BUSCADOR DE IMÓVEIS PARA FLIP EM BELO HORIZONTE (ITBI vs ANÚNCIOS)")
    print("="*80)

    # 1. PBH ITBI & IPTU Integration
    print("\n--- PASSO 1: Sincronização de Dados (ITBI & IPTU PBH) ---")
    itbi_dir = "data/raw/itbi"
    iptu_dir = "data/raw/iptu"
    pbh_client = PBHClient(download_dir=itbi_dir, iptu_dir=iptu_dir)
    
    # Check if we already have CSV files in itbi and iptu directories
    has_itbi_files = False
    if os.path.exists(itbi_dir):
        has_itbi_files = any(f.endswith('.csv') for f in os.listdir(itbi_dir))
        
    has_iptu_files = False
    if os.path.exists(iptu_dir):
        has_iptu_files = any(f.endswith('.csv') for f in os.listdir(iptu_dir))
        
    if not has_itbi_files or not has_iptu_files or args.force_download:
        print("Buscando relatórios de ITBI e base consolidada do IPTU no portal da prefeitura...")
        downloaded = pbh_client.run()
        print(f"Sincronização de bases PBH concluída.")
    else:
        print("Bases do ITBI e IPTU já existem localmente. Pulando download. (Use --force-download para atualizar)")

    # 2. Process ITBI and get Top 20 neighborhoods
    print("\n--- PASSO 2: Processamento do ITBI e Cálculo do Teto do m² por Bairro ---")
    scraper_csv = "data/raw/anuncios_vivareal.csv"
    output_excel = "relatorio_flip_bh.xlsx"
    processor = DataProcessor(itbi_dir=itbi_dir, iptu_dir=iptu_dir, scraper_csv=scraper_csv, output_excel=output_excel)
    
    stats_df, top_20_bairros = processor.process_itbi()
    if not top_20_bairros:
        print("ERRO: Nenhum bairro com volume estatístico válido foi encontrado na base do ITBI.")
        sys.exit(1)

    # Load neighborhoods list from config.json if available
    neighborhoods_to_scrape = []
    config_path = "config.json"
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = json.load(f)
                neighborhoods_to_scrape = config_data.get("neighborhoods", [])
                if neighborhoods_to_scrape:
                    print(f"Carregada lista de {len(neighborhoods_to_scrape)} bairros alvo do arquivo '{config_path}':")
                    for idx, bairro in enumerate(neighborhoods_to_scrape, 1):
                        print(f"  {idx:02d}. {bairro.upper()}")
        except Exception as e:
            print(f"Aviso: Erro ao ler '{config_path}' ({e}). Usando top 20 bairros do ITBI como padrão.")
            
    if not neighborhoods_to_scrape:
        print("Usando top 20 bairros de maior valor/m² do ITBI como padrão para busca:")
        neighborhoods_to_scrape = top_20_bairros
        for idx, bairro in enumerate(neighborhoods_to_scrape, 1):
            print(f"  {idx:02d}. {bairro}")

    # 3. Scrape current listings (Crawlee)
    print("\n--- PASSO 3: Busca de Anúncios Ativos (VivaReal/Zap Imóveis via Crawlee) ---")
    scraper = CrawleeScraper(raw_data_path=scraper_csv)
    
    has_ads = os.path.exists(scraper_csv) and os.path.getsize(scraper_csv) > 100
    
    if not has_ads or args.force_scrape:
        print("Iniciando novo scrape local...")
        sites_to_scrape = None
        if args.sites != "all":
            sites_to_scrape = [s.strip().lower() for s in args.sites.split(",") if s.strip()]
        scraper.run(neighborhoods_to_scrape, max_pages_per_bairro=args.pages_per_bairro, sites=sites_to_scrape)
        
        # Verify if scrape created file successfully
        if not os.path.exists(scraper_csv) or os.path.getsize(scraper_csv) < 100:
            print("ERRO: O scrape de anúncios falhou ou não retornou dados.")
            sys.exit(1)
    else:
        print(f"Dados de anúncios atuais encontrados localmente em '{scraper_csv}'.")
        print("Reutilizando cache local de anúncios. (Use --force-scrape para rodar uma nova extração)")

    # 4. Cross-reference data and generate report
    print("\n--- PASSO 4: Cruzamento de Dados e Identificação de Oportunidades de Flip ---")
    print(f"Filtrando imóveis com m² anunciado pelo menos {args.discount*100:.0f}% abaixo da média de vendas reais do bairro...")
    print(f"Motor de Classificação Probabilística ativo: {args.classifier.upper()}")
    success = processor.analyze_and_export(discount_threshold=args.discount, classifier_mode=args.classifier)
    
    if success:
        print("\n" + "="*80)
        print("                           FLIP FINDER CONCLUÍDO!")
        print(f" Relatório gerado com sucesso: {os.path.abspath(output_excel)}")
        print(f" Abas da Planilha:")
        print("   - 'Oportunidades': Imóveis com desconto ou com potencial de flip construtivo (Luxo a preço de Normal).")
        print("   - 'Resumo_Bairros': Médias de m² e volumes oficiais de transação do ITBI por bairro.")
        print("   - 'Todos_Anuncios': Lista completa de imóveis raspados classificados com o Padrão Construtivo.")
        print("="*80 + "\n")
    else:
        print("ERRO: Falha ao gerar o relatório final.")
        sys.exit(1)

if __name__ == "__main__":
    main()
