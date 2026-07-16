# Contexto do modelo de padrão de acabamento

## Objetivo

Prever o `Padrão de Acabamento` (`P1` a `P5`) de anúncios que não obtiverem
match determinístico no IPTU. O modelo deve usar **somente**:

- bairro;
- rua;
- área construída.

Preço e preço por m² foram deliberadamente excluídos para evitar circularidade:
o preço do anúncio não deve definir o padrão que será posteriormente usado para
avaliar esse mesmo preço.

## Dados e normalização

- Fonte: CSVs mensais de ITBI em `data/itbi`, de jun/2024 a jun/2026.
- Apenas transações residenciais, área > 10 m², valor de transação > R$ 20 mil
  e padrões P1–P5.
- Valor de transação: máximo entre valor declarado e valor-base de cálculo.
- A rua é extraída de `Endereco` ou `Endereco Completo`; o portal PBH alterou
  esse nome de coluna nos arquivos recentes.
- Ruas são normalizadas para maiúsculas, sem acentos/pontuação, sem prefixos
  como `RUA`, `AVENIDA`, `AVE`, `AV`, `ALAMEDA`, `TRAVESSA` e `PRACA`.
  Exemplo: `AVE ÁLVARES CABRAL 1181` vira `ALVARES CABRAL`.
- A regra de normalização está em `src/imovel_scrapper/normalization.py` e em
  `investigacao/avaliar_modelos_padrao.py`. Após alterar dados canônicos, rodar
  `python main.py prepare` para regenerá-los.

## Experimentos realizados

Scripts em `investigacao/`:

- `avaliar_modelos_padrao.py`: prepara CSVs ITBI e faz os experimentos
  iniciais, incluindo a análise de rua rara.
- `comparar_modelos_sem_preco.py`: compara Random Forest, CatBoost, XGBoost e
  classificação ordinal CatBoost sem preço.
- `otimizar_random_forest_sem_preco.py`: seleção temporal de hiperparâmetros
  do Random Forest.

Comando da otimização:

```powershell
python investigacao\otimizar_random_forest_sem_preco.py --config config.json
```

## Melhor configuração encontrada

Protocolo:

- treino final: 44.125 transações de toda BH, jun/2024 a mar/2026;
- teste temporal final: 644 transações dos bairros-alvo, abr–jun/2026;
- validação interna: jan–mar/2026, sem usar o teste final.

Random Forest selecionado:

- `street_threshold=20` (ruas com menos de 20 transações viram `RUA_RARA`);
- `min_samples_leaf=1`;
- `max_features="sqrt"`;
- 300 árvores;
- `class_weight="balanced_subsample"`;
- semente 42.

Resultado no teste final:

| Métrica | Resultado | Meta |
| --- | ---: | ---: |
| F1 macro | 0,770 | >= 0,700 |
| Balanced accuracy | 0,758 | >= 0,720 |
| Acurácia | 0,831 | — |

Artefatos:

- `investigacao/resultados/random_forest_otimizado/relatorio_otimizacao.md`;
- `investigacao/resultados/random_forest_otimizado/resultado_teste_final.csv`;
- `investigacao/resultados/random_forest_otimizado/validacao_temporal_resumo.csv`.

## Comparação de métodos sem preço

No mesmo teste temporal, a melhor abordagem foi Random Forest. Nos testes de
comparação, XGBoost, CatBoost multiclasse e CatBoost ordinal ficaram abaixo do
Random Forest em F1 macro.

## Como a regra de rua rara deve funcionar em produção

O modelo pronto precisa persistir, além da floresta e do encoder:

- o limiar `20`;
- a lista de ruas frequentes do treino;
- versão, período de treino e métricas do modelo.

Na previsão de um anúncio:

```text
normalizar rua
se rua estiver na lista de ruas frequentes: usar a própria rua
senão: usar RUA_RARA
```

No experimento, rua rara conhecida e rua inédita entram ambas como `RUA_RARA`;
o relatório de cobertura apenas as distingue para fins de diagnóstico.

## Estado de integração

O modelo ainda **não está integrado** ao pipeline principal nem é persistido.
A integração recomendada é:

1. manter match IPTU confirmado como fonte determinística de padrão;
2. aplicar o Random Forest somente quando não houver match IPTU;
3. salvar o bundle do modelo (floresta, encoder, ruas frequentes e metadados);
4. emitir `finish_pattern`, método, versão e confiança (`predict_proba`) no
   resultado do `analyze`.

O modelo não deve usar preço de anúncio em nenhuma dessas etapas.
