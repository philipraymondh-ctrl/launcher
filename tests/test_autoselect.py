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
        DOMAINES_INDEX, "https://z.test/domaines.php", scraper.match_producers)
    assert [p for p, _ in found] == ["Ganevat", "Richard Leroy"]
    assert found[0][1] == "https://z.test/domaine-9-12-Jura_DOMAINE_GANEVAT.html"


def test_the_cart_link_is_never_a_producer_page():
    found = autoselect.find_producer_links(
        DOMAINES_INDEX, "https://z.test/domaines.php", scraper.match_producers)
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


# --- the real leszinzinsduvin grower index -----------------------------------

import pathlib

REAL_INDEX = (pathlib.Path(__file__).parent / "fixtures"
              / "leszinzinsduvin-domaines-excerpt.html").read_text()


def test_reads_grower_cards_that_have_no_anchor_at_all():
    """395 cards, zero <a> elements: the destination is in data-url and
    script does the navigating. Looking only at href found nothing."""
    found = autoselect.find_producer_links(
        REAL_INDEX, "https://www.leszinzinsduvin.com/domaines.php", scraper.match_producers)
    urls = {u.rsplit("/", 1)[-1] for _, u in found}
    assert "domaine-6-193-Ganevat_Jean_franecois.html" in urls
    assert "domaine-6-407-Ganevat_Anne_et_jean_franecois_SAS.html" in urls


def test_the_shared_surname_goes_to_the_right_estate():
    found = dict((u.rsplit("/", 1)[-1], p) for p, u in autoselect.find_producer_links(
        REAL_INDEX, "https://www.leszinzinsduvin.com/domaines.php", scraper.match_producers))
    assert found["domaine-6-366-Bruyeere_houillon.html"] == "Bruyere Houillon"
    assert found["domaine-6-37-Overnoy_PierreHouillon_Emmanuel.html"] == "Overnoy/Houillon"


def test_a_blurb_name_dropping_another_grower_is_not_that_grower():
    """Thomas Batardiere's card mentions Richard Leroy in its description.
    Matching the whole card reported his page as Richard Leroy's."""
    found = autoselect.find_producer_links(
        REAL_INDEX, "https://www.leszinzinsduvin.com/domaines.php", scraper.match_producers)
    assert not any("Batardieere" in u for _, u in found)


def test_growers_we_do_not_watch_are_left_alone():
    found = autoselect.find_producer_links(
        REAL_INDEX, "https://www.leszinzinsduvin.com/domaines.php", scraper.match_producers)
    assert not any("Abate_Marino" in u for _, u in found)


def test_the_index_route_uses_the_page_already_fetched():
    """Re-fetching the index by URL wasted a request, and under the probe's
    replay crawler it fetched a different page than the one just parsed --
    so the index was never read and the shop could not be verified."""
    grower = ('<html><body><table>'
              '<tr><td><a href="/vin-1-x.html">Les Chalasses 2018</a></td><td>55,00 &euro;</td></tr>'
              '<tr><td><a href="/vin-2-y.html">Poulprix 2024</a></td><td>29,00 &euro;</td></tr>'
              '<tr><td><a href="/vin-3-z.html">Vin Jaune 2012</a></td><td>118,00 &euro;</td></tr>'
              '</table></body></html>')

    class OnceThenGrowers:
        max_requests = 99

        def __init__(self):
            self.n = 0
            self.urls = []

        def get(self, url, params=None):
            self.n += 1
            self.urls.append(url)
            return scraper.crawler.FetchResult(200, REAL_INDEX if self.n == 1 else grower)

    client = OnceThenGrowers()
    shop = dict(SHOP, catalog_path="vins.php")
    items = scraper.fetch_html(shop, client)

    assert items, "the grower index yielded nothing"
    # The index page itself is fetched once, then one request per grower.
    assert client.urls[0] == "https://shop.test/vins.php"
    assert all("domaine-" in u for u in client.urls[1:])
    producers = {p for i in items for p in scraper.match_producers(i["text"])}
    assert "Ganevat" in producers and "Bruyere Houillon" in producers


def test_a_product_grid_built_from_data_url_cards_parses():
    """Regression: link detection learned to read data-url, but the product
    path still indexed ["href"] and raised KeyError on those elements."""
    html = ('<html><body><div class="grid">'
            '<div class="card" data-url="/vin-1-a.html"><h3>Poulprix 2024</h3>'
            '<span>29,00 &euro;</span></div>'
            '<div class="card" data-url="/vin-2-b.html"><h3>Les Chalasses 2018</h3>'
            '<span>55,00 &euro;</span></div>'
            '<div class="card" data-url="/vin-3-c.html"><h3>Vin Jaune 2012</h3>'
            '<span>118,00 &euro;</span></div>'
            '</div></body></html>')
    items = find(html)
    assert len(items) == 3
    assert items[0]["url"] == "https://shop.test/vin-1-a.html"
    assert items[0]["price"] == 29.0


def test_the_real_grower_index_does_not_crash_the_product_parser():
    """The probe hit KeyError: 'href' on exactly this page."""
    items = autoselect.find_products(
        REAL_INDEX, "https://www.leszinzinsduvin.com/domaines.php",
        scraper.PRICE_PATTERN, scraper.parse_price)
    assert isinstance(items, list)   # no prices on an index: [] is correct


# --- a real grower page ------------------------------------------------------

REAL_GROWER = (pathlib.Path(__file__).parent / "fixtures"
               / "leszinzinsduvin-grower-labet.html").read_text()
GROWER_URL = "https://www.leszinzinsduvin.com/domaine-6-200-Labet_alain.html"


def test_a_grower_page_listing_two_bottles_is_read():
    """Labet's page lists exactly two wines. The three-block rule is for
    deciding whether an unknown page is a catalogue; arriving from the
    producer index already answers that, so it must not apply here."""
    items = autoselect.find_products(
        REAL_GROWER, GROWER_URL, scraper.PRICE_PATTERN, scraper.parse_price, min_blocks=1)
    assert len(items) == 2
    assert sorted(i["price"] for i in items) == [110.0, 130.0]


def test_the_catalogue_threshold_still_applies_to_an_unknown_page():
    """Two priced rows on a page nobody vouched for is a featured strip."""
    assert autoselect.find_products(
        REAL_GROWER, GROWER_URL, scraper.PRICE_PATTERN, scraper.parse_price) == []


def test_sold_out_listings_are_marked_not_dropped():
    """Both of Labet's bottles say "Produit épuisé". Dropping them in the
    parser would make a shop whose stock is out today read as broken to the
    probe, so they are marked and skipped later."""
    items = autoselect.find_products(
        REAL_GROWER, GROWER_URL, scraper.PRICE_PATTERN, scraper.parse_price, min_blocks=1)
    assert items and all(i["in_stock"] is False for i in items)


def test_out_of_stock_matches_accented_french():
    assert autoselect.is_out_of_stock("Produit épuisé")
    assert autoselect.is_out_of_stock("RUPTURE DE STOCK")
    assert autoselect.is_out_of_stock("Sold out")
    assert not autoselect.is_out_of_stock("Chercheurs d'or 2009 Labet 110,00 €")


def test_check_shop_does_not_alert_on_a_bottle_nobody_can_buy():
    class Serve:
        max_requests = 99

        def __init__(self):
            self.n = 0

        def get(self, url, params=None):
            self.n += 1
            return scraper.crawler.FetchResult(
                200, REAL_INDEX if self.n == 1 else REAL_GROWER)

    shop = dict(SHOP, catalog_path="vins.php")
    assert scraper.fetch_html(shop, Serve()), "the adapter must still parse them"
    assert scraper.check_shop(shop, Serve()) == []


# --- every hit must be nameable ----------------------------------------------

def test_a_card_with_only_an_image_and_a_price_still_gets_a_name():
    """vinovivo parsed products whose titles were all empty, and an
    untitled hit is unreadable in a digest. The product URL spells the wine
    out even when the markup does not."""
    html = ('<html><body><div class="grid">'
            '<div><a href="/vin-3120-jura_Blanc__Poulprix_2024_Ganevat.html"></a>'
            '<span>29,00 &euro;</span></div>'
            '<div><a href="/vin-4001-jura_Blanc__Les_Chalasses_2018_Ganevat.html"></a>'
            '<span>55,00 &euro;</span></div>'
            '<div><a href="/vin-5002-jura_Rouge__Poulsard_2020_Labet.html"></a>'
            '<span>41,00 &euro;</span></div>'
            '</div></body></html>')
    items = find(html)
    assert len(items) == 3
    assert all(i["title"] for i in items), "every hit needs a name"
    assert "Poulprix" in items[0]["title"]
    # And the name is still enough for producer matching.
    assert scraper.match_producers(items[0]["title"]) == ["Ganevat"]


def test_a_real_title_is_never_replaced_by_the_slug():
    items = find(WOOCOMMERCE)
    assert items[0]["title"] == "Poulprix 2024 Ganevat"


# --- finding the catalogue from the shop's own menu ---------------------------
#
# Four HTML shops sat verified but nearly empty: winenot 3 products,
# vinnouveau 8, pangee 12, against catalogues that are obviously larger. Two
# causes, both here. The probe guessed catalogue paths from a fixed list, which
# cannot know that a shop calls its catalogue /la-cave or /notre-selection --
# but the shop's own navigation says so. And it accepted the first page that
# parsed at all, so a "featured wines" strip on the landing page won.

NAV = """
<html><body>
  <nav>
    <a href="/">Accueil</a>
    <a href="/la-cave">La cave</a>
    <a href="/nos-vins?page=1">Nos vins</a>
    <a href="/panier">Mon panier</a>
    <a href="/mon-compte">Mon compte</a>
    <a href="/blog/2024/vendanges">Le blog</a>
    <a href="/contact">Contact</a>
    <a href="/cgv">CGV</a>
    <a href="https://instagram.com/shop">Instagram</a>
  </nav>
</body></html>
"""


def test_catalogue_links_come_from_the_menu():
    found = autoselect.find_catalogue_links(NAV, "https://shop.test/")
    assert "https://shop.test/la-cave" in found
    assert "https://shop.test/nos-vins?page=1" in found


def test_the_cart_and_the_blog_are_not_catalogues():
    found = autoselect.find_catalogue_links(NAV, "https://shop.test/")
    for path in ("/panier", "/mon-compte", "/blog", "/contact", "/cgv"):
        assert not any(path in url for url in found), path


def test_another_domain_is_never_followed():
    found = autoselect.find_catalogue_links(NAV, "https://shop.test/")
    assert not any("instagram" in url for url in found)


def test_the_landing_page_itself_is_not_offered_again():
    found = autoselect.find_catalogue_links(NAV, "https://shop.test/")
    assert "https://shop.test/" not in found
    assert "https://shop.test" not in found


def test_catalogue_links_are_capped_and_deduped():
    many = "".join(
        f'<a href="/vins-{i}">Nos vins {i}</a>' for i in range(40)
    ) + '<a href="/vins-1">Nos vins 1 again</a>'
    found = autoselect.find_catalogue_links(f"<html><body>{many}</body></html>",
                                            "https://shop.test/")
    assert len(found) == len(set(found))
    assert len(found) <= autoselect.MAX_CATALOGUE_LINKS


def test_dutch_and_english_menus_count_too():
    html = """<html><body>
      <a href="/wijnen">Alle wijnen</a>
      <a href="/collections/all">Shop all wines</a>
      <a href="/winkelwagen">Winkelwagen</a>
    </body></html>"""
    found = autoselect.find_catalogue_links(html, "https://shop.test/")
    assert "https://shop.test/wijnen" in found
    assert "https://shop.test/collections/all" in found
    assert not any("winkelwagen" in u for u in found)


# --- a product page is not a catalogue ----------------------------------------
#
# vinnouveau's real landing page (probe_pages/capture.index.html) offers
# /12-vins-francais -- its top category -- alongside
# /accueil/4437-zulu-vin-de-france-rouge-l-estanyol-2014-magnum.html, a single
# bottle. Both contain "vin", and at 3s a request the bottles crowd out the
# categories.

PRODUCTS_AND_CATEGORIES = """
<html><body>
  <a href="/accueil/4437-zulu-vin-de-france-rouge-magnum.html">Zulu Vin de France</a>
  <a href="/12-vins-francais">Vins Français</a>
  <a href="/sud-ouest-rouge/5507-simon-busser-vin-de-france.html">Simon Busser</a>
  <a href="/18-jura">Jura</a>
</body></html>
"""


def test_a_page_already_read_as_a_product_is_not_a_catalogue_candidate():
    parsed = ["https://shop.test/accueil/4437-zulu-vin-de-france-rouge-magnum.html"]
    found = autoselect.find_catalogue_links(
        PRODUCTS_AND_CATEGORIES, "https://shop.test/", exclude=parsed)
    assert parsed[0] not in found


def test_shallower_paths_come_first():
    """A category lives near the root; a bottle lives under one. (A region
    name like /18-jura is not catalogue vocabulary and is not offered at all
    -- the parent category is what a scraper wants anyway.)"""
    found = autoselect.find_catalogue_links(
        PRODUCTS_AND_CATEGORIES, "https://shop.test/")
    assert found[0] == "https://shop.test/12-vins-francais"
    deep = "https://shop.test/sud-ouest-rouge/5507-simon-busser-vin-de-france.html"
    assert found.index(deep) > 0


def test_the_real_vinnouveau_menu_yields_its_categories_first():
    """Against the page as captured, not a synthetic menu."""
    from pathlib import Path
    html = (Path(__file__).parent.parent / "probe_pages"
            / "capture.index.html").read_text(encoding="utf-8", errors="replace")
    found = autoselect.find_catalogue_links(html, "https://vinnouveau.fr")
    assert found[:2] == ["https://vinnouveau.fr/12-vins-francais",
                         "https://vinnouveau.fr/14-vins-etrangers"]


def test_the_real_pangee_menu_yields_its_wine_categories():
    from pathlib import Path
    html = (Path(__file__).parent.parent / "probe_pages"
            / "capture.fr.html").read_text(encoding="utf-8", errors="replace")
    found = autoselect.find_catalogue_links(html, "https://la-pangee.com/fr")
    assert "https://la-pangee.com/fr/25-vins" in found
