# Regras de Negócio e Governança do Projeto (imovel-scrapper)

Este arquivo documenta as regras de negócio, critérios estatísticos e diretrizes de saneamento de dados adotados no pipeline de oportunidades imobiliárias em Belo Horizonte.

---

## 1. Saneamento e Validação de Área (Anti-Distorção de R$/m²)
- **Área Útil vs. Área Total / Terreno:**
  - O scraping deve priorizar expressamente expressões e seletores de **área útil/privativa** (`m² útil`, `m² privativa`).
  - Expressões de área total do condomínio ou terreno (`m² tot.`) não devem ser assumidas como área do apartamento.
  - Se apenas a área total estiver disponível ou se a área for duvidosa:
    - Tenta-se reconciliar com a área das unidades no cadastro imobiliário da PBH/IPTU (`buildings.csv`) em caso de correspondência de endereço (rua + número).
    - Caso contrário, o anúncio recebe a flag `area_unverified` e não se qualifica para o ranking principal de oportunidades sem confirmação.
- **Trava de Coerência Tipológica:**
  - Apartamentos convencionais (ex: 2 a 4 quartos) com área anunciada > 300 m² sem termos como *"cobertura"*, *"privativa"*, *"duplex"*, *"triplex"*, *"garden"* ou *"área privativa"* recebem a flag `area_mismatch_warning` e são desqualificados do topo do ranking de oportunidades.

---

## 2. Deduplicação por Imóvel Físico (Multi-Corretora)
- **Problema:** Um mesmo imóvel é frequentemente anunciado por 3 a 5 corretoras simultaneamente em diferentes portais com pequenas variações de título e preço.
- **Regra de Agrupamento:**
  - Agrupar listagens pela chave física aproximada:
    - Bairro normalizado
    - Logradouro/rua normalizada (anúncios sem rua herdam o logradouro se houver correspondência de prédio/área/preço)
    - Quantidade de quartos (tolerância de $\pm 1$ quarto para acomodar divergências em dependência reversível/closet/DCE)
    - Faixa de área útil (tolerância de $\pm 6\%$)
    - Faixa de preço anunciado (tolerância de $\pm 8\%$)
- **Eleição do Anúncio Representativo:**
  - O anúncio **mais recente** (`collected_at` / `collected_date`) é mantido como o registro principal no ranking, herdando nome de rua/número caso outra corretora o tenha informado.
- **Metadados Consolidados:**
  - `duplicate_count`: Quantidade total de anúncios concorrentes identificados para o mesmo imóvel físico.
  - `min_advertised_price`: O menor preço entre todas as corretoras que anunciam o imóvel.
  - `alternate_urls`: Lista/concatenação das URLs dos outros anúncios concorrentes.

---

## 3. Filtro de Recência, IDs Legados e Contaminação Regional
- **Anúncios Fantasmas / Legados:**
  - Anúncios com data explícita de publicação original superior a 180 dias ou com IDs sequenciais legados conhecidos do portal devem ser descartados:
    - **Imovelweb:** IDs $< 3.015.000.000$.
    - **OLX:** IDs $< 1.450.000.000$.
- **Contaminação Regional:**
  - Descartar anúncios promovidos cujo título, URL ou endereço indiquem bairros ou municípios fora da área-alvo (ex: Ribeirão das Neves, Santa Mônica, Barreiro, Betim, Contagem).

---

## 4. Integração com Dados Oficiais (PBH ITBI e IPTU) e Desconto Conservador
- **Desconto Conservador:**
  - A referência de preço de mercado adotada para seleção de oportunidades é baseada em:
    $$\text{Ref. Conservadora} = \min\big(\text{Média Geral do Bairro}, \text{Média do Padrão Construtivo}\big)$$
  - Evita distorções causadas por classificações imprecisas de padrão construtivo ou bairros heterogêneos.
- **Requisito de Vaga de Garagem:**
  - O ranking principal de oportunidades filtra estritamente imóveis com $\ge 1$ vaga de garagem.
- **Conferência Predial:**
  - A conferência de padrão construtivo (P1 a P5) e tipologia predial é referenciada na base de IPTU da PBH (`buildings.csv`).
