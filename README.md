# Imóvel Scrapper

Pipeline modular para sincronizar dados oficiais da PBH, coletar anúncios de apartamentos em Belo Horizonte e identificar oportunidades de compra com desconto ou arbitragem de padrão construtivo.

Os datasets canônicos são exclusivamente CSV compatível com Excel em pt-BR: UTF-8 com BOM, separador `;` e decimal `,`.

## Instalação

```powershell
python -m pip install -r requirements.txt
playwright install chromium
```

## Fluxo

```powershell
python main.py sync-pbh
python main.py scrape
python main.py prepare
python main.py analyze
python main.py report
```

O teto global da coleta pode ser persistido em `config.json` e vale para todos os bairros daquela execução:

```json
"max_price": 800000
```

Ele também pode ser sobrescrito pontualmente na execução:

```powershell
python main.py scrape --max-price 800000
```

O coletor envia o limite a cada portal e reaplica a regra localmente antes de salvar os anúncios. Assim, resultados acima do teto são descartados mesmo quando um portal ignora o parâmetro de URL.

## Paginação adaptativa

A profundidade da busca é controlada por três opções:

```json
"pages_per_neighborhood": 4,
"min_pages_per_neighborhood": 2,
"min_new_listings_per_page": 3
```

O scraper sempre visita pelo menos duas páginas de cada combinação portal/bairro. Depois disso, continua enquanto a página atual trouxer pelo menos três anúncios novos dentro do teto, até o máximo de quatro páginas. Repetições e anúncios acima do teto não contam para decidir se a busca deve avançar.

As métricas de cada página ficam em `data/listings/<run_id>/pagination.json`, incluindo quantidade bruta, anúncios novos, anúncios dentro do teto e o motivo da parada.

Para executar todas as etapas em sequência:

```powershell
python main.py run
```

O fluxo orquestrado tem quatro etapas: coleta de referência (ITBI/IPTU), scraper, tratamento dos dados locais e inferência dos padrões. Para reutilizar os dados coletados recentemente e executar somente tratamento e inferência:

```powershell
python main.py run --skip-reference-sync --skip-scrape
```

A inferência usa o Random Forest otimizado com bairro, rua e área. Ruas com menos de 20 ocorrências no treino são agrupadas em `RUA_RARA`; preço e preço por m² não entram no modelo. O match determinístico com IPTU continua tendo prioridade.

Os comandos usam `config.json`. A sincronização consulta a API CKAN da PBH, mantém os snapshots oficiais em `data/iptu` e `data/itbi`, e grava datasets canônicos em `data/processed`. O relatório Excel e os manifestos de execução ficam em `data/reports`.

Uma falha em qualquer portal selecionado interrompe a coleta e impede a geração de relatório parcial.
