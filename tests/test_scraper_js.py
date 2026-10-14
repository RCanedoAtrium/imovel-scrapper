import pytest
from playwright.async_api import async_playwright

from crawlee_scraper import (
    IMOVELWEB_CARD_SCRIPT,
    OLX_CARD_SCRIPT,
    QUINTOANDAR_CARD_SCRIPT,
    VIVAREAL_CARD_SCRIPT,
    ZAP_CARD_SCRIPT,
)


@pytest.fixture(scope="module")
async def browser_page():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        yield page
        await browser.close()


@pytest.mark.anyio
async def test_vivareal_card_extraction(browser_page):
    html = """
    <a class="olx-core-surface" href="https://www.vivareal.com.br/imovel/123" title="Apartamento 2 Quartos Savassi">
        <span data-cy="rp-cardProperty-location-txt">Rua Fernandes Tourinho, 500 - Savassi, Belo Horizonte - MG</span>
        <p data-cy="rp-cardProperty-address-txt">Rua Fernandes Tourinho, 500</p>
        <p data-cy="rp-cardProperty-price-txt">R$ 550.000</p>
        <span data-cy="rp-cardProperty-propertyArea-txt">65 m² útil</span>
        <span data-cy="rp-cardProperty-bedroom-txt">2 Quartos</span>
        <span data-cy="rp-cardProperty-bathroom-txt">2 Banheiros</span>
        <span data-cy="rp-cardProperty-parking-txt">1 Vaga</span>
        <span data-cy="rp-cardProperty-condominium-txt">R$ 450 condo</span>
        <span data-cy="rp-cardProperty-iptu-txt">R$ 150 iptu</span>
    </a>
    """
    await browser_page.set_content(html)
    listings = await browser_page.evaluate(VIVAREAL_CARD_SCRIPT)

    assert len(listings) == 1
    item = listings[0]
    assert item["link"] == "https://www.vivareal.com.br/imovel/123"
    assert item["price"] == "R$ 550.000"
    assert item["area"] == "65 m² útil"
    assert item["street"] == "Rua Fernandes Tourinho, 500"
    assert item["bedrooms"] == "2"
    assert item["bathrooms"] == "2"
    assert item["parking_spaces"] == "1"
    assert item["condominium"] == "R$ 450"
    assert item["advertised_iptu"] == "R$ 150"


@pytest.mark.anyio
async def test_zap_card_extraction(browser_page):
    html = """
    <a class="olx-core-surface" href="https://www.zapimoveis.com.br/imovel/456" title="Apartamento 3 Quartos Lourdes">
        <span data-cy="rp-cardProperty-location-txt">Rua Marília de Dirceu, 200 - Lourdes, Belo Horizonte - MG</span>
        <p data-cy="rp-cardProperty-address-txt">Rua Marília de Dirceu, 200</p>
        <p data-cy="rp-cardProperty-price-txt">R$ 780.000</p>
        <span data-cy="rp-cardProperty-propertyArea-txt">80 m²</span>
        <span data-cy="rp-cardProperty-bedroom-txt">3 Quartos</span>
        <span data-cy="rp-cardProperty-bathroom-txt">2 Banheiros</span>
        <span data-cy="rp-cardProperty-parking-txt">2 Vagas</span>
        <span data-cy="rp-cardProperty-condominium-txt">R$ 650 condo</span>
        <span data-cy="rp-cardProperty-iptu-txt">R$ 220 iptu</span>
    </a>
    """
    await browser_page.set_content(html)
    listings = await browser_page.evaluate(ZAP_CARD_SCRIPT)

    assert len(listings) == 1
    item = listings[0]
    assert item["link"] == "https://www.zapimoveis.com.br/imovel/456"
    assert item["price"] == "R$ 780.000"
    assert item["area"] == "80 m²"
    assert item["street"] == "Rua Marília de Dirceu, 200"
    assert item["bedrooms"] == "3"
    assert item["bathrooms"] == "2"
    assert item["parking_spaces"] == "2"
    assert item["condominium"] == "R$ 650"
    assert item["advertised_iptu"] == "R$ 220"


@pytest.mark.anyio
async def test_olx_card_extraction(browser_page):
    html = """
    <section class="olx-adcard">
        <a data-testid="adcard-link" class="olx-adcard__link" href="https://mg.olx.com.br/imoveis/apto-savassi-789">
            <h2 class="olx-adcard__title">Lindo apartamento na Savassi</h2>
            <h3 class="olx-adcard__price">R$ 600.000</h3>
            <div class="olx-adcard__detail" aria-label="70 metros quadrados">70 m²</div>
            <div class="olx-adcard__detail" aria-label="3 quartos">3 quartos</div>
            <div class="olx-adcard__detail" aria-label="2 banheiros">2 banheiros</div>
            <div class="olx-adcard__detail" aria-label="2 vagas">2 vagas</div>
            <div class="olx-adcard__detail class-condominium">Condomínio: R$ 500</div>
            <div class="olx-adcard__detail class-iptu">IPTU: R$ 200</div>
            <p class="olx-adcard__location">Belo Horizonte, Savassi</p>
        </a>
    </section>
    """
    await browser_page.set_content(html)
    listings = await browser_page.evaluate(OLX_CARD_SCRIPT)

    assert len(listings) == 1
    item = listings[0]
    assert item["link"] == "https://mg.olx.com.br/imoveis/apto-savassi-789"
    assert item["title"] == "Lindo apartamento na Savassi"
    assert item["price"] == "R$ 600.000"
    assert item["area"] == "70 m²"
    assert item["location"] == "Belo Horizonte, Savassi"
    assert item["bedrooms"] == "3"
    assert item["bathrooms"] == "2"
    assert item["parking_spaces"] == "2"
    assert item["condominium"] == "R$ 500"
    assert item["advertised_iptu"] == "R$ 200"


@pytest.mark.anyio
async def test_quintoandar_card_extraction(browser_page):
    html = """
    <div data-testid="house-card-container">
        <a href="/imovel/893456789">
            <div>Apartamento à venda</div>
            <div>R$ 750.000</div>
            <div>R$ 600 condomínio</div>
            <div>85 m²</div>
            <div>Rua Pernambuco, 1000, Funcionários · Belo Horizonte</div>
            <span data-testid="house-card-bedroom" aria-label="3 quartos">3 quartos</span>
            <span data-testid="house-card-bathroom" aria-label="2 banheiros">2 banheiros</span>
            <span data-testid="house-card-parking" aria-label="2 vagas">2 vagas</span>
        </a>
    </div>
    """
    await browser_page.set_content(html)
    listings = await browser_page.evaluate(QUINTOANDAR_CARD_SCRIPT)

    assert len(listings) == 1
    item = listings[0]
    assert "/imovel/893456789" in item["link"]
    assert item["price"] == "R$ 750.000"
    assert item["condominium"] == "R$ 600"
    assert item["area"] == "85 m²"
    assert item["street"] == "Rua Pernambuco"
    assert item["number"] == "1000"
    assert item["bedrooms"] == "3"
    assert item["bathrooms"] == "2"
    assert item["parking_spaces"] == "2"


@pytest.mark.anyio
async def test_imovelweb_card_extraction(browser_page):
    html = """
    <div class="postingCardLayout-module__posting-card-container">
        <h2 class="posting-description"><a href="https://www.imovelweb.com.br/propriedades/apto-savassi-123.html">Apartamento 3 quartos Savassi</a></h2>
        <div data-qa="POSTING_CARD_PRICE">R$ 820.000</div>
        <div data-qa="POSTING_CARD_FEATURES">
            <span>90 m² útil</span>
            <span>3 quartos</span>
            <span>2 banheiros</span>
            <span>2 vagas</span>
        </div>
        <div data-qa="POSTING_CARD_LOCATION">Savassi, Belo Horizonte</div>
        <div data-qa="POSTING_CARD_ADDRESS">Rua Alagoas 1200</div>
        <div data-qa="POSTING_CARD_EXPENSE">R$ 700 Condomínio</div>
        <div data-qa="POSTING_CARD_IPTU">R$ 250 IPTU</div>
    </div>
    """
    await browser_page.set_content(html)
    listings = await browser_page.evaluate(IMOVELWEB_CARD_SCRIPT)

    assert len(listings) == 1
    item = listings[0]
    assert item["link"] == "https://www.imovelweb.com.br/propriedades/apto-savassi-123.html"
    assert item["title"] == "Apartamento 3 quartos Savassi"
    assert item["price"] == "R$ 820.000"
    assert item["area"] == "90 m² útil"
    assert item["street"] == "Rua Alagoas"
    assert item["number"] == "1200"
    assert item["bedrooms"] == "3"
    assert item["bathrooms"] == "2"
    assert item["parking_spaces"] == "2"
    assert item["condominium"] == "R$ 700"
    assert item["advertised_iptu"] == "R$ 250"


@pytest.mark.anyio
async def test_card_without_price_or_area_ignored(browser_page):
    html = """
    <a class="olx-core-surface" href="https://www.vivareal.com.br/imovel/empty">
        <span data-cy="rp-cardProperty-location-txt">Belo Horizonte</span>
    </a>
    """
    await browser_page.set_content(html)
    listings = await browser_page.evaluate(VIVAREAL_CARD_SCRIPT)
    assert len(listings) == 0
