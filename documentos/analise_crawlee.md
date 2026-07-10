# Análise de Software: Crawlee (https://crawlee.dev/)

Esta análise apresenta um estudo detalhado sobre o **Crawlee**, um framework open-source voltado para web scraping e automação de navegadores, avaliando sua arquitetura, recursos contra bloqueios e aplicabilidade no desenvolvimento de ferramentas de coleta de dados.

---

## 1. Introdução ao Crawlee

O **Crawlee** é uma biblioteca open-source de alta performance mantida pela **Apify** e sob licença **Apache 2.0**. Ele foi desenvolvido com o propósito de resolver os principais desafios enfrentados na criação de crawlers estáveis: evitar bloqueios por mecanismos anti-bot, gerenciar concorrência de forma eficiente e estruturar o código de raspagem.

Originalmente construído para o ecossistema **Node.js (JavaScript/TypeScript)**, o Crawlee recentemente lançou suporte estável para **Python** (v1.0+), permitindo que desenvolvedores de ambas as stacks utilizem suas primitivas e facilidades.

---

## 2. Arquitetura e Componentes Core

O design do Crawlee é altamente modular. Em vez de forçar o desenvolvedor a gerenciar requisições manuais ou abrir instâncias do Playwright/Puppeteer na unha, o Crawlee abstrai esses processos em classes estruturadas:

```
[BasicCrawler] -> Abstração base de fluxo de requisições
   |
   +---> [HTTP Crawlers] (Rápido, estático, baixo consumo de RAM)
   |        |
   |        +---> CheerioCrawler (JS) / BeautifulSoupCrawler (Python)
   |        +---> ParselCrawler (Python) / JSDOMCrawler (JS)
   |
   +---> [Browser Crawlers] (Processa JS dinâmico, interage com a página)
            |
            +---> PlaywrightCrawler (JS/Python)
            +---> PuppeteerCrawler (JS)
```

### Componentes de Armazenamento e Estado
*   **Request Queue (Fila de Requisições):** Um gerenciador dinâmico de URLs que garante persistência. Se o crawler cair ou a máquina reiniciar, ele consegue retomar de onde parou sem reprocessar links já visitados.
*   **Dataset:** Primitiva de armazenamento para dados estruturados. Ele salva os resultados em disco (geralmente como arquivos JSON individuais em modo desenvolvimento) e permite exportações fáceis para CSV, Excel ou JSON unificado.
*   **Key-Value Store:** Utilizado para salvar arquivos binários (imagens, PDFs), capturas de tela (screenshots) ou estados globais de persistência do crawler.
*   **Session Pool (Pool de Sessões):** Gerencia sessões (cookies, tokens e IP/Proxy associado) para que o tráfego pareça vir de múltiplos usuários reais, rotacionando credenciais de forma inteligente.

---

## 3. Principais Recursos e Diferenciais

O Crawlee se destaca por automatizar dores comuns de desenvolvimento que normalmente exigiriam bibliotecas externas ou código complexo:

### A. Evasão de Bloqueios (Anti-Bot Bypass)
Plataformas como VivaReal, ZAP Imóveis e OLX utilizam CDNs (Cloudflare, GoCache) e WAFs para barrar scripts de automação. O Crawlee combate isso de forma ativa:
*   **Browser Fingerprinting:** Modifica assinaturas de navegadores (User-Agent, headers HTTP/2 ordens de chaves, objetos do objeto `navigator`) para mimetizar navegadores reais de usuários de desktop ou mobile.
*   **TLS Client Hello Spoofing:** Em crawlers HTTP, ele modifica a assinatura do aperto de mão SSL/TLS (TLS fingerprint) para parecer a de um navegador real (ex: Chrome), enganando sistemas de segurança que bloqueiam assinaturas típicas de Python (`requests`, `httpx`) ou Node.js (`axios`, `fetch`).

### B. Auto-Escalonamento (Auto-Scaling)
O framework monitora constantemente o consumo de CPU e memória RAM da máquina onde está executando. Ele aumenta a concorrência (número de páginas abertas simultaneamente) se houver recursos livres e diminui automaticamente caso a máquina comece a gargalar, evitando travamentos e estouros de memória.

### C. Roteamento Inteligente (Router)
Permite organizar o código de raspagem criando rotas para diferentes tipos de páginas (ex: página de listagem de anúncios vs. página de detalhe do imóvel):
```python
# Exemplo em Python usando a primitiva Router do Crawlee
from crawlee.router import Router

router = Router()

@router.default_handler
async def default_handler(context):
    # Trata página principal / listagem
    await context.enqueue_links(label='DETAIL', selector='a.property-card')

@router.handler('DETAIL')
async def detail_handler(context):
    # Trata a extração de dados do anúncio específico
    data = {
        'url': context.request.url,
        'title': await context.page.locator('h1').inner_text(),
    }
    await context.push_data(data)
```

---

## 4. Ecossistema: JavaScript vs. Python

| Recurso | Crawlee (JavaScript / TypeScript) | Crawlee (Python) |
| :--- | :--- | :--- |
| **Maturidade** | Altíssima (antigo Apify SDK, consolidado há anos) | Média/Alta (Lançado recentemente em versão estável) |
| **Biblioteca de Análise Estática** | Cheerio, JSDOM | BeautifulSoup, Parsel (Scrapy Selectors) |
| **Biblioteca de Automação** | Puppeteer, Playwright | Playwright |
| **Suporte Assíncrono** | Nativo (Node.js Promises/async-await) | Nativo (`asyncio` e `async/await` do Python 3) |
| **Integração Cloud** | Perfeita (Deploy direto na plataforma Apify) | Perfeita (Deploy direto na plataforma Apify) |

---

## 5. Aplicabilidade no Projeto `imovel-scrapper`

No nosso projeto atual, tomamos a decisão de utilizar um **Actor pronto da Apify (`gio21/vivareal-zap-scraper`)**. Essa decisão se alinha com a premissa de *não reinventar a roda*, pois:
1.  **Custo/Tempo:** A plataforma do VivaReal atualiza frequentemente seletores de HTML e defesas contra robôs. Manter um crawler local exige manutenção semanal.
2.  **Infraestrutura:** O Actor da Apify utiliza proxies residenciais brasileiros internos deles para evitar o bloqueio da Cloudflare/GoCache.

No entanto, caso fôssemos migrar para uma solução **100% local e gratuita** (sem taxas da API do Apify), o **Crawlee** seria a **melhor stack possível** por conta dos seguintes fatores:
*   Substituiria o `pbh_client.py` e o `apify_scraper.py` sob um único framework padronizado.
*   O uso de `PlaywrightCrawler` no Crawlee nos forneceria stealth nativo e controle sobre o GoCache localmente.
*   Permitiria gerenciar o download dos CSVs de ITBI usando a biblioteca HTTP do Crawlee com retentativas automáticas e gerenciamento de fila de forma resiliente.

## 6. Conclusão

O **Crawlee** redefine a forma de criar raspadores de dados modernos. Ele não é apenas um "wrapper" para Playwright ou BeautifulSoup, mas sim um framework de engenharia completo que gerencia filas, concorrência, sessões e evasão de bots de maneira transparente. Para projetos que demandam escalabilidade local ou pretendem migrar para ambientes auto-hospedados (on-premise), o Crawlee é a recomendação líder de mercado atualmente.
