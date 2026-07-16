# Processo de otimização e replicação do scraper para novos portais

## Objetivo operacional

O scraper deve coletar **cards da página de busca**, não depender de páginas de
detalhe, e entregar todos os portais no mesmo contrato de dados. O processo de
otimização prioriza, nesta ordem:

1. correção da localização e dos filtros;
2. cobertura e qualidade dos campos dos cards;
3. deduplicação e paginação eficiente;
4. diagnóstico reproduzível quando o layout ou o acesso do portal mudar.

O código principal está em `crawlee_scraper.py`; a gravação e a validação de
execuções ficam em `src/imovel_scrapper/sources/listings.py` e
`src/imovel_scrapper/card_validation.py`.

## Contrato obrigatório de saída

Todo portal deve produzir as colunas abaixo, mantendo a ordem e usando string
vazia quando um campo não estiver disponível no card:

```text
title, street, number, location, neighborhood, price, area, bedrooms,
bathrooms, parking_spaces, condominium, advertised_iptu, link, site
```

`link`, `price` e `area` são obrigatórios para o anúncio ser aceito. O método
`CrawleeScraper.listing_with_contract()` garante o formato antes de escrever o
CSV com UTF-8 BOM e separador `;`.

## Receita para adicionar um portal

1. **Mapear a busca manualmente.** Identificar a URL de busca por bairro, os
   filtros de preço/área aceitos e o mecanismo real de paginação. Confirmar se
   é parâmetro de URL, botão "ver mais" ou scroll infinito; não supor que
   `?page=N` funciona.
2. **Adicionar o identificador do portal.** Incluir o nome em
   `SUPPORTED_SITES`, na geração de URLs iniciais (`generate_start_urls`) e em
   `config.json` quando o portal estiver aprovado.
3. **Criar um handler Crawlee exclusivo.** Usar um `label` próprio, esperar um
   seletor estável de card e falhar explicitamente se cards não renderizarem.
   Isso aciona retry em vez de transformar bloqueio, Cloudflare ou mudança de
   layout em uma coleta vazia.
4. **Extrair do card com seletores específicos primeiro.** Reutilizar
   `CARD_METADATA_HELPERS` e passar seletores semânticos (`data-testid`,
   `data-qa`, `aria-label`) para rua, área, quartos, banheiros, vagas,
   condomínio e IPTU. Parsing de texto deve ser apenas fallback.
5. **Normalizar o item.** Preencher `title`, `price`, `area`, `link`,
   `location`, `neighborhood` e `site`; depois aplicar
   `listing_with_contract()` para completar o restante do contrato.
6. **Validar localização no card.** A URL de busca não basta, pois alguns
   portais inserem anúncios patrocinados de outros bairros/cidades. Criar uma
   função equivalente a `is_olx_location_match()` e aceitar somente cards cujo
   texto visível contenha Belo Horizonte e o bairro solicitado.
7. **Integrar a paginação adaptativa.** Depois de extrair uma página, chamar
   `store_page()`/`register_page()`. Não duplicar a lógica de deduplicação ou
   de parada dentro do handler.
8. **Validar antes de ativar produção.** Executar `validate-cards` para o
   novo portal, corrigir seletores até obter card completo e só então incluí-lo
   no `config.json` de produção.

## Filtros, deduplicação e paginação

O teto de preço e a área mínima são enviados ao portal quando a URL suporta os
parâmetros, mas são sempre reaplicados localmente. Isso evita que resultados
fora do limite contaminem a base quando o portal ignora o filtro de URL.

Cada combinação `portal + bairro` mantém uma lista de links já vistos. A
identidade do anúncio é a URL normalizada, sem query string e sem barra final.
Uma página repetida, portanto, não aumenta a coleta nem mantém a paginação
aberta artificialmente.

Após o mínimo de páginas configurado, a próxima página só é solicitada se ela
trouxer pelo menos `min_new_listings_per_page` anúncios novos dentro dos
filtros. As decisões e contagens ficam em:

```text
data/listings/<run_id>/pagination.json
```

Campos úteis para diagnosticar eficiência: `raw_listings`, `new_unique_listings`,
`new_within_price`, rejeições por preço/área/localização e `decision`.

## Validação rápida de um portal novo

Executar sem modificar o ponteiro da coleta de produção:

```powershell
python main.py validate-cards --sites novoportal --neighborhood BELVEDERE --max-price 800000 --pages 4 --attempts 3
```

O comando grava em `data/diagnostics/card-validation/...`:

- `report.md` e `report.json` com URL, tentativas, campos ausentes e amostra;
- `samples.csv` no contrato comum;
- `pagination.json` por tentativa.

Só considerar o portal aprovado se houver card completo, localização correta e
URLs sem repetição. Se falhar, preservar a evidência e corrigir o seletor ou a
estratégia de navegação, em vez de aceitar resultado parcial.

## Testes mínimos antes de liberar

Adicionar testes de unidade/integrados para:

- gerar a URL com bairro, teto e área mínima corretos;
- extrair e validar `link`, preço e área;
- rejeitar cards de bairro ou cidade diferente;
- validar paginação nativa do portal (URL, botão ou scroll);
- deduplicar links entre páginas;
- garantir que o resultado tenha exatamente `RAW_LISTING_COLUMNS`;
- testar que filtros locais descartam preço/área fora do limite.

Os testes existentes em `tests/test_pipeline_integration.py` servem como
modelo. Para mudanças de layout, começar por `validate-cards`, não pela coleta
completa.

## Checklist de manutenção

- Rodar `validate-cards` quando um portal reduzir volume ou tiver campos vazios.
- Inspecionar `pagination.json` para detectar repetição ou parada precoce.
- Preferir seletores semânticos a classes CSS geradas.
- Tratar ausência de cards como falha recuperável, com screenshot/diagnóstico.
- Manter o nome do portal consistente entre URL, `site`, CSV e
  `SUPPORTED_SITES`.
- Não alterar silenciosamente o contrato de colunas: qualquer campo novo exige
  atualização do contrato, normalização, relatório e testes.
