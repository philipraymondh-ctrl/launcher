"""Finding shops worth adding, and the three questions that decide it.

Ten shops were suggested from search snippets and none survived contact: they
failed on questions nobody had asked. These are those questions, and the
danger in each is a false *yes* -- a restaurant added as a shop, or a shop
added that cannot ship to Denmark, costs a probe and a coverage row for ever.
"""
import json

import crawler
import discover
import scraper

BASE = "https://shop.test"


def a_grid(count=6, producer="Ganevat"):
    cells = "".join(
        f'<div class="card"><a href="/vin/{i}">{producer} Cuvee {i} 2022</a>'
        f'<span class="prix">{20 + i},00 &euro;</span></div>'
        for i in range(count)
    )
    return f'<div class="grid">{cells}</div>'


SHOP_PAGE = f"""
<html><body>
  <nav><a href="/panier">Panier</a><a href="/livraison">Livraison</a></nav>
  {a_grid()}
</body></html>
"""

RESTAURANT_PAGE = """
<html><body>
  <nav><a href="/reservation">Reserver une table</a>
       <a href="https://www.thefork.com/restaurant/x">TheFork</a></nav>
  <h1>Menu du jour</h1>
  <ul>
    <li>Ganevat Poulprix 2022 &mdash; 12,00 &euro; le verre</li>
    <li>Overnoy Arbois Pupillin 2015 &mdash; 18,00 &euro; le verre</li>
    <li>Assiette de fromages &mdash; 9,00 &euro;</li>
  </ul>
  <p>Horaires d'ouverture: 18h-minuit</p>
</body></html>
"""


# --- question 1: shop or restaurant -------------------------------------------

def test_a_shop_is_a_shop():
    is_shop, why = discover.looks_like_shop(SHOP_PAGE, BASE)
    assert is_shop
    assert any("product blocks" in r for r in why["for"])
    assert "has a cart" in why["for"]


def test_a_restaurant_wine_list_is_not_a_shop():
    """A menu has the prices and the producers and no way to buy a bottle.
    Counting prices cannot tell the two apart; counting *product links* can,
    which is what find_products already requires."""
    is_shop, why = discover.looks_like_shop(RESTAURANT_PAGE, BASE)
    assert not is_shop
    assert why["against"], "nothing was recorded against an obvious restaurant"


def test_a_wine_bar_that_also_sells_bottles_is_a_shop():
    """A bar with a booking link and a real catalogue is worth having. The
    booking signals only decide it when nothing says shop."""
    page = f"""<html><body>
      <a href="/reservation">Reserver une table</a>
      <a href="/panier">Panier</a>
      {a_grid()}
    </body></html>"""
    is_shop, why = discover.looks_like_shop(page, BASE)
    assert is_shop
    assert why["against"], "the booking link should still be recorded"


def test_a_page_with_prices_but_no_products_is_not_a_shop():
    page = ("<html><body><p>Nos vins au verre: 8,00 &euro; a 14,00 &euro;."
            "</p></body></html>")
    assert discover.looks_like_shop(page, BASE)[0] is False


# --- question 2: does it ship to Denmark --------------------------------------

def test_shipping_page_is_found_by_its_name():
    assert discover.find_shipping_page(SHOP_PAGE, BASE) == f"{BASE}/livraison"


def test_a_shipping_page_elsewhere_is_not_followed():
    """Following an off-host link would assess somebody else's terms."""
    page = '<html><body><a href="https://dhl.example/delivery">Delivery</a></body></html>'
    assert discover.find_shipping_page(page, BASE) is None


def test_denmark_named_in_the_shipping_text():
    assert discover.ships_to_denmark(
        "<html><body><p>Livraison en France, Belgique, Danemark et Suede."
        "</p></body></html>") is True


def test_denmark_in_a_country_selector():
    assert discover.ships_to_denmark(
        '<html><body><select name="country"><option value="FR">France</option>'
        '<option value="DK">Danmark</option></select></body></html>') is True


def test_a_shipping_page_that_excludes_denmark_says_no():
    assert discover.ships_to_denmark(
        "<html><body><h1>Livraison</h1><p>Nous livrons en France "
        "metropolitaine uniquement.</p></body></html>") is False


def test_a_page_that_never_mentions_shipping_is_unknown_not_no():
    """Recording "no" for a page we simply failed to find would drop a shop
    for our own miss."""
    assert discover.ships_to_denmark("<html><body><p>Nos vins</p></body></html>") is None
    assert discover.ships_to_denmark(None) is None


# --- question 3: does it stock anyone we watch --------------------------------

def test_producers_are_read_from_the_listings():
    assert discover.producers_on(SHOP_PAGE, BASE) == ["Ganevat"]


def test_a_blog_post_about_a_producer_is_not_stock():
    """A shop that sells none of them still names half the Jura in its prose
    and its tag cloud."""
    page = ("<html><body><article><h1>Pierre Overnoy, une visite</h1>"
            "<p>Nous avons visite Overnoy-Houillon a Pupillin.</p></article>"
            "</body></html>")
    assert discover.producers_on(page, BASE) == []


# --- putting one candidate through all three ----------------------------------

class FakeCrawler:
    def __init__(self, pages):
        self.pages = pages
        self.requested = []

    def get(self, url, params=None):
        self.requested.append(url)
        if url not in self.pages:
            raise crawler.UpstreamError("HTTP 404", status_code=404)
        return crawler.FetchResult(200, self.pages[url])


def test_a_candidate_is_assessed_in_three_requests():
    client = FakeCrawler({
        BASE: SHOP_PAGE,
        f"{BASE}/livraison": "<html><body>Livraison: France, Danmark.</body></html>",
    })
    result = discover.assess(BASE, client)
    assert result["is_shop"] is True
    assert result["ships_to_denmark"] is True
    assert result["producers"] == ["Ganevat"]
    assert len(client.requested) <= discover.MAX_PAGES_PER_CANDIDATE


def test_a_dead_candidate_is_recorded_not_raised():
    result = discover.assess("https://gone.test", FakeCrawler({}))
    assert result["status"].startswith("UpstreamError")
    assert result["is_shop"] is False


def test_ranking_puts_stock_first_then_shipping():
    results = [
        {"host": "c", "is_shop": True, "ships_to_denmark": True, "producers": []},
        {"host": "a", "is_shop": True, "ships_to_denmark": False, "producers": ["Ganevat"]},
        {"host": "b", "is_shop": True, "ships_to_denmark": None, "producers": []},
    ]
    assert [r["host"] for r in discover.rank(results)] == ["a", "c", "b"]


def test_the_suggested_entry_is_never_verified():
    """probe.py --apply is what turns a candidate into a shop, against a real
    response. A discovery pass may not shortcut that."""
    entry = discover.suggested_entry({"host": "www.example-wine.fr", "producers": []})
    assert entry["verified"] is False
    assert entry["name"] == "examplewine"
    assert entry["url"] == "https://www.example-wine.fr"


# --- the search key must never reach a log ------------------------------------

def test_a_search_key_is_redacted_from_any_message():
    """The key travels as a query parameter and this repo's run logs are
    public. Nothing that can carry a URL may print one unredacted."""
    noisy = ("HTTP 429 for https://www.googleapis.com/customsearch/v1"
             "?key=SUPERSECRET&cx=abc123&q=ganevat")
    cleaned = discover._redact(noisy)
    assert "SUPERSECRET" not in cleaned
    assert "abc123" not in cleaned
    assert "[redacted]" in cleaned


def test_no_search_key_means_no_search_rather_than_scraping_one(monkeypatch, capsys):
    """Reading a search engine's HTML results is against its terms, and
    Instagram's hashtag pages need a login. Absent a key the answer is to do
    less, not to find another way in."""
    monkeypatch.delenv("SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("SEARCH_ENGINE_ID", raising=False)
    client = FakeCrawler({})
    assert discover.search_candidates("ganevat", client) == []
    assert client.requested == [], "it tried to fetch something without a key"
    assert "skipping web search" in capsys.readouterr().out


def test_the_module_never_reaches_for_instagram():
    """A standing decision kept where it cannot be quietly reversed.

    Hashtag pages are behind a login wall, the Graph API's hashtag search
    returns only the last 24 hours and does not name the account that posted,
    and automated collection is against Meta's terms -- the same reason
    nothing here fetches Wine-Searcher. The docstring may say all that; no
    string the code actually *uses* may name the host."""
    import ast

    tree = ast.parse(open(discover.__file__).read())
    docstrings = {id(ast.get_docstring(n, clean=False)) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))}
    used = [node.value.lower() for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node.value) not in docstrings]
    for forbidden in ("instagram", "graph.facebook", "explore/tags", "cdninstagram"):
        assert not [s for s in used if forbidden in s], \
            f"a string the code uses names {forbidden}"


# --- candidates from an importer's stockist page ------------------------------

def test_outbound_hosts_are_collected_from_a_stockist_page():
    """These growers are allocated through one importer per country, and the
    importer's stockist page names the shops that actually receive bottles."""
    page = ("<html><body>"
            '<a href="https://caviste-a.test/shop">Caviste A</a>'
            '<a href="https://caviste-b.test/vins">Caviste B</a>'
            '<a href="https://caviste-a.test/contact">Caviste A again</a>'
            '<a href="/about">Our own page</a>'
            "</body></html>")
    client = FakeCrawler({"https://importer.test/stockists": page})
    hosts = discover.links_from_page("https://importer.test/stockists", client)
    assert hosts == ["https://caviste-a.test", "https://caviste-b.test"]
