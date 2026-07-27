"""Reading a catalogue nobody wrote selectors for.

Each fixture below is a different real-world shape. leszinzinsduvin is
hand-rolled PHP whose product URLs look like
/vin-2669-alsace_Rouge__Pinot_Noir_2018_Pierre_Andrey.html, which is the
case that motivated the module: nine shops serve HTML like this and match
none of the generic div.product guesses in SHOPS.
"""
import autoselect
import scraper

PRICE = scraper.PRICE_PATTERN
PARSE = scraper.parse_price
BASE = "https://shop.test/catalogue"


def find(html):
    return autoselect.find_products(html, BASE, PRICE, PARSE)


# --- the shapes real shops serve --------------------------------------------

TABLE_PHP = """
<html><body><table class="liste">
 <tr>
   <td><a href="/vin-2669-alsace_Rouge__Pinot_Noir_2018_Pierre_Andrey.html">
       Pinot Noir Gamay 2018 Pierre Andrey</a></td>
   <td><span>14,50 &euro;</span></td>
 </tr>
 <tr>
   <td><a href="/vin-3120-jura_Blanc__Poulprix_2024_Ganevat.html">
       Poulprix 2024 Ganevat</a></td>
   <td><span>29,00 &euro;</span></td>
 </tr>
 <tr>
   <td><a href="/vin-4001-loire_Blanc__Les_Noels_2019_Richard_Leroy.html">
       Les Noels de Montbenault 2019 Richard Leroy</a></td>
   <td><span>85,00 &euro;</span></td>
 </tr>
</table></body></html>
"""

WOOCOMMERCE = """
<html><body><ul class="products columns-4">
  <li class="product type-product">
    <a href="/produit/poulprix-2024/" class="woocommerce-LoopProduct-link">
      <h2 class="woocommerce-loop-product__title">Poulprix 2024 Ganevat</h2>
      <span class="price"><span class="amount">29,00&nbsp;&euro;</span></span>
    </a>
  </li>
  <li class="product type-product">
    <a href="/produit/vin-jaune-2012/" class="woocommerce-LoopProduct-link">
      <h2 class="woocommerce-loop-product__title">Vin Jaune 2012 Ganevat</h2>
      <span class="price"><span class="amount">118,00&nbsp;&euro;</span></span>
    </a>
  </li>
  <li class="product type-product">
    <a href="/produit/lattard-rouge/" class="woocommerce-LoopProduct-link">
      <h2 class="woocommerce-loop-product__title">Lattard Cotes du Jura Rouge</h2>
      <span class="price"><span class="amount">24,00&nbsp;&euro;</span></span>
    </a>
  </li>
</ul></body></html>
"""

CARD_GRID = """
<html><body>
<nav><a href="/panier">Panier 0,00 &euro;</a></nav>
<div class="grid">
  <article><a href="/products/a" title="Poulprix 2024 Ganevat"><img alt="Poulprix"></a>
    <div class="money">29,00 &euro;</div></article>
  <article><a href="/products/b" title="Les Grands Teppes 2018 Ganevat"><img alt="Teppes"></a>
    <div class="money">105,00 &euro;</div></article>
  <article><a href="/products/c" title="Lattard Chardonnay 2021"><img alt="Lattard"></a>
    <div class="money">24,00 &euro;</div></article>
</div></body></html>
"""


def test_reads_a_hand_rolled_php_table():
    items = find(TABLE_PHP)
    assert len(items) == 3
    assert items[1]["price"] == 29.0
    assert "Poulprix 2024 Ganevat" in items[1]["title"]
    assert items[1]["url"] == "https://shop.test/vin-3120-jura_Blanc__Poulprix_2024_Ganevat.html"


def test_reads_a_woocommerce_loop():
    items = find(WOOCOMMERCE)
    assert [i["price"] for i in items] == [29.0, 118.0, 24.0]
    assert items[0]["title"] == "Poulprix 2024 Ganevat"


def test_reads_a_card_grid_and_ignores_the_cart_link():
    items = find(CARD_GRID)
    assert len(items) == 3
    assert all("/panier" not in i["url"] for i in items)
    assert items[0]["title"] == "Poulprix 2024 Ganevat"


def test_relative_links_become_absolute():
    for item in find(TABLE_PHP):
        assert item["url"].startswith("https://shop.test/")


def test_the_block_text_is_enough_for_producer_matching():
    """The point of all this: the text handed on must still let
    match_producers do its job."""
    hits = [scraper.match_producers(i["text"]) for i in find(TABLE_PHP)]
    assert ["Ganevat"] in hits
    assert ["Richard Leroy"] in hits


# --- what it must refuse to do ----------------------------------------------

def test_a_vintage_is_never_read_as_a_price():
    """The rule from CLAUDE.md, enforced here too: only a currency-adjacent
    number is a price, so a page of vintages yields no products at all."""
    html = """<html><body><ul>
      <li><a href="/a">Chardonnay 2018</a></li>
      <li><a href="/b">Savagnin 2019</a></li>
      <li><a href="/c">Trousseau 2020</a></li>
    </ul></body></html>"""
    assert find(html) == []


def test_a_single_featured_bottle_is_not_a_catalogue():
    html = """<html><body><div class="hero">
      <a href="/products/one">Poulprix 2024</a><span>29,00 &euro;</span>
    </div></body></html>"""
    assert find(html) == []


def test_a_page_with_no_structure_returns_nothing_rather_than_guessing():
    assert find("<html><body><p>Bienvenue</p></body></html>") == []
    assert find("") == []


def test_the_same_product_is_not_returned_twice():
    html = """<html><body><div class="grid">
      <div><a href="/p/a">A</a><span>10,00 &euro;</span></div>
      <div><a href="/p/a">A again</a><span>10,00 &euro;</span></div>
      <div><a href="/p/b">B</a><span>20,00 &euro;</span></div>
      <div><a href="/p/c">C</a><span>30,00 &euro;</span></div>
    </div></body></html>"""
    assert sorted(i["url"] for i in find(html)) == [
        "https://shop.test/p/a", "https://shop.test/p/b", "https://shop.test/p/c",
    ]


# --- pagination -------------------------------------------------------------

def test_follows_rel_next():
    html = '<html><body><a rel="next" href="/catalogue?page=2">2</a></body></html>'
    assert autoselect.find_next_page(html, BASE) == "https://shop.test/catalogue?page=2"


def test_follows_a_next_worded_link_in_french():
    html = '<html><body><a href="/vins.php?p=2">Suivant</a></body></html>'
    assert autoselect.find_next_page(html, BASE) == "https://shop.test/vins.php?p=2"


def test_a_next_link_pointing_at_the_current_page_is_not_followed():
    """Otherwise the fetcher loops until the page budget runs out."""
    html = f'<html><body><a rel="next" href="{BASE}">Next</a></body></html>'
    assert autoselect.find_next_page(html, BASE) is None


def test_no_next_link_means_the_last_page():
    assert autoselect.find_next_page("<html><body><p>fin</p></body></html>", BASE) is None


def test_a_javascript_next_link_is_ignored():
    html = '<html><body><a href="javascript:void(0)">Next</a></body></html>'
    assert autoselect.find_next_page(html, BASE) is None


# --- the fetcher around it ---------------------------------------------------

class PagedCrawler:
    """Serves a fixed map of url -> html and counts what was asked for."""

    def __init__(self, pages):
        self.pages = pages
        self.requested = []

    def get(self, url, params=None):
        self.requested.append(url)
        return scraper.crawler.FetchResult(200, self.pages.get(url, ""))


def page(items, next_url=None):
    rows = "".join(
        f'<div><a href="/p/{n}">Ganevat cuvee {n}</a><span>{10 + n},00 &euro;</span></div>'
        for n in items
    )
    nxt = f'<a rel="next" href="{next_url}">Next</a>' if next_url else ""
    return f'<html><body><div class="grid">{rows}</div>{nxt}</body></html>'


SHOP = {
    "name": "zzzshop", "platform": "html", "url": "https://shop.test",
    "item_selector": "div.product", "title_selector": "h2.product-title",
    "price_selector": "span.price", "verified": True,
}


def test_the_fetcher_walks_every_page():
    """Catalogues are paged; reading only page one turns a real hit into a
    silent miss."""
    crawler_client = PagedCrawler({
        "https://shop.test": page([1, 2, 3], "https://shop.test/?p=2"),
        "https://shop.test/?p=2": page([4, 5, 6], "https://shop.test/?p=3"),
        "https://shop.test/?p=3": page([7, 8, 9]),
    })
    items = scraper.fetch_html(SHOP, crawler_client)
    assert len(items) == 9
    assert len(crawler_client.requested) == 3


def test_the_fetcher_starts_at_the_catalogue_path_when_one_is_set():
    shop = dict(SHOP, catalog_path="vins.php")
    crawler_client = PagedCrawler({"https://shop.test/vins.php": page([1, 2, 3])})
    assert len(scraper.fetch_html(shop, crawler_client)) == 3
    assert crawler_client.requested == ["https://shop.test/vins.php"]


def test_a_pager_that_points_at_itself_does_not_loop():
    crawler_client = PagedCrawler(
        {"https://shop.test": page([1, 2, 3], "https://shop.test")})
    scraper.fetch_html(SHOP, crawler_client)
    assert len(crawler_client.requested) == 1


def test_configured_selectors_still_win_when_they_match():
    """Auto-detection is the fallback, not a replacement -- a shop that has
    been given real selectors keeps using them."""
    html = ('<html><body><div class="product">'
            '<h2 class="product-title">Ganevat Poulprix</h2>'
            '<span class="price">29,00 &euro;</span>'
            '<a href="/p/1">link</a></div></body></html>')
    items = scraper.fetch_html(SHOP, PagedCrawler({"https://shop.test": html}))
    assert len(items) == 1
    assert items[0]["title"] == "Ganevat Poulprix"


def test_an_empty_first_page_is_still_an_error():
    import pytest
    with pytest.raises(scraper.EmptyResponseError):
        scraper.fetch_html(SHOP, PagedCrawler({"https://shop.test": "   "}))


# --- producer indexes --------------------------------------------------------

DOMAINES_INDEX = """
<html><body>
 <a href="/index.php">Accueil</a>
 <a href="/panier.php">Panier</a>
 <a href="/domaine-4-31-Languedoc__Roussillon_MAS_COUTELOU.html">Mas Coutelou</a>
 <a href="/domaine-14-58-Rhone_DOMAINE_GRAMENON.html">Gramenon</a>
 <a href="/domaine-9-12-Jura_DOMAINE_GANEVAT.html">Domaine Ganevat</a>
 <a href="/domaine-3-20-Loire_RICHARD_LEROY.html">Richard Leroy</a>
</body></html>
"""


def test_finds_only_the_growers_we_watch():
    found = autoselect.find_producer_links(
        DOMAINES_INDEX, "https://z.test/domaines.php", scraper.PRODUCERS, scraper.normalize)
    assert [p for p, _ in found] == ["Ganevat", "Richard Leroy"]
    assert found[0][1] == "https://z.test/domaine-9-12-Jura_DOMAINE_GANEVAT.html"


def test_the_cart_link_is_never_a_producer_page():
    found = autoselect.find_producer_links(
        DOMAINES_INDEX, "https://z.test/domaines.php", scraper.PRODUCERS, scraper.normalize)
    assert all("/panier" not in u for _, u in found)


def test_a_shop_with_no_crawlable_catalogue_falls_back_to_its_producer_index():
    """leszinzinsduvin's /vins.php is a POST search form -- nothing to walk.
    Its grower index is the way in."""
    grower_page = ('<html><body><table>'
                   '<tr><td><a href="/vin-1-a.html">Les Chalasses 2018</a></td><td>55,00 &euro;</td></tr>'
                   '<tr><td><a href="/vin-2-b.html">Poulprix 2024</a></td><td>29,00 &euro;</td></tr>'
                   '<tr><td><a href="/vin-3-c.html">Vin Jaune 2012</a></td><td>118,00 &euro;</td></tr>'
                   '</table></body></html>')
    crawler_client = PagedCrawler({
        "https://shop.test/vins.php": DOMAINES_INDEX,
        "https://shop.test/domaine-9-12-Jura_DOMAINE_GANEVAT.html": grower_page,
        "https://shop.test/domaine-3-20-Loire_RICHARD_LEROY.html": grower_page,
    })
    shop = dict(SHOP, catalog_path="vins.php")
    items = scraper.fetch_html(shop, crawler_client)

    assert len(items) == 6, "both grower pages should be read"
    # The grower's page need not repeat their name on every row, so the
    # producer is carried over from the index entry.
    assert any("Ganevat" in i["text"] for i in items)
    assert any("Richard Leroy" in i["text"] for i in items)
    assert scraper.match_producers(items[0]["text"]) == ["Ganevat"]


def test_the_index_fallback_only_runs_when_the_catalogue_is_empty():
    """It costs a request per grower, so a shop that parses normally must
    never pay for it."""
    crawler_client = PagedCrawler({"https://shop.test": page([1, 2, 3])})
    scraper.fetch_html(SHOP, crawler_client)
    assert crawler_client.requested == ["https://shop.test"]
