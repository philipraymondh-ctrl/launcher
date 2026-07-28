import inspect
import json
import re
from pathlib import Path

import pytest

import scraper

FIXTURES = Path(__file__).parent / "fixtures"


def load_json(name):
    return json.loads((FIXTURES / name).read_text())


class FakeJSONResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class FakeTextResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class FakeCrawler:
    """Stands in for crawler.Crawler in fetch-shape/producer-matching tests
    -- these don't care about robots/rate-limit/backoff, just about what
    check_shop() does with a canned response."""

    def __init__(self, response):
        self._response = response
        self.request_count = 0
        self.max_requests = 1000

    def get(self, url, params=None):
        self.request_count += 1
        return self._response


def shop_by_name(name):
    return next(s for s in scraper.SHOPS if s["name"] == name)


def test_shopify_fixture_matches_ganevat_and_ignores_other_wines():
    data = load_json("example-shopify-shop.json")
    shop = shop_by_name("example-shopify-shop")

    hits = scraper.check_shop(shop, FakeCrawler(FakeJSONResponse(data)))

    assert len(hits) == 1
    assert hits[0]["producer"] == "Ganevat"
    assert hits[0]["price"] == pytest.approx(68.00)


def test_woocommerce_fixture_matches_labet():
    data = load_json("example-woo-shop.json")
    shop = shop_by_name("example-woo-shop")

    hits = scraper.check_shop(shop, FakeCrawler(FakeJSONResponse(data)))

    assert len(hits) == 1
    assert hits[0]["producer"] == "Labet"
    assert hits[0]["price"] == pytest.approx(39.00)


def test_html_fixture_matches_overnoy_houillon_alias():
    html = (FIXTURES / "example-html-shop.html").read_text()
    shop = shop_by_name("example-html-shop")

    hits = scraper.check_shop(shop, FakeCrawler(FakeTextResponse(html)))

    assert len(hits) == 1
    assert hits[0]["producer"] == "Overnoy/Houillon"
    assert hits[0]["price"] == pytest.approx(189.00)


def test_empty_html_response_raises_empty_response_error():
    shop = shop_by_name("example-html-shop")

    with pytest.raises(scraper.EmptyResponseError):
        scraper.fetch_html(shop, FakeCrawler(FakeTextResponse("   ")))


def test_alias_matching_is_accent_and_case_insensitive():
    assert scraper.match_producers("Domaine GANEVAT Chardonnay") == ["Ganevat"]
    assert scraper.match_producers("cuvee by clémence gerbet") == ["Clemence Gerbet"]
    assert scraper.match_producers("some unrelated winery") == []


def test_price_parser_ignores_bare_vintage_year():
    assert scraper.parse_price("2018 Domaine Ganevat Chardonnay") is None
    assert scraper.parse_price("Domaine Ganevat 2018 - 45,00€") == pytest.approx(45.00)
    assert scraper.parse_price("$89.99 (vintage 2019)") == pytest.approx(89.99)


def test_price_parser_ignores_vintage_with_explicit_currency_code():
    assert scraper.parse_price("Chardonnay 2020 210,00 EUR") == pytest.approx(210.0)


def test_unverified_shops_are_skipped_by_main(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("DRY_RUN", "1")
    monkeypatch.setattr(scraper, "DRY_RUN", True)
    # Keep this test from writing into the repo's real seen.json/hits.json.
    import notify
    monkeypatch.setattr(notify, "STATE_PATH", tmp_path / "seen.json")
    monkeypatch.setattr(notify, "HITS_PATH", tmp_path / "hits.json")

    # main() builds its own Crawler, so once any shop is verified this test
    # would make live requests to real shops -- slow, flaky, and rude. Stub
    # the crawl layer out entirely; this test is about the skip logic.
    import crawler as crawler_mod

    class NoNetwork:
        request_count = 0
        max_requests = 999

        def get(self, url, params=None):
            raise AssertionError(f"test must not hit the network: {url}")

    monkeypatch.setattr(crawler_mod, "Crawler", lambda *a, **k: NoNetwork())

    scraper.main()

    out = capsys.readouterr().out
    for shop in scraper.SHOPS:
        if not shop.get("verified", True):
            assert f"[{shop['name']}] skipped: unverified placeholder" in out


REAL_SHOPS = [s for s in scraper.SHOPS if not s["name"].startswith("example-")]
UNVERIFIED = [s for s in REAL_SHOPS if not s.get("verified")]
VERIFIED = [s for s in REAL_SHOPS if s.get("verified")]


def fixture_for(shop):
    ext = "html" if shop["platform"] == "html" else "json"
    return FIXTURES / f"{shop['name']}.{ext}"


def crawler_for(shop):
    path = fixture_for(shop)
    if shop["platform"] == "html":
        return FakeCrawler(FakeTextResponse(path.read_text()))
    return FakeCrawler(FakeJSONResponse(json.loads(path.read_text())))


@pytest.mark.parametrize("shop_name", [s["name"] for s in UNVERIFIED] or ["__none__"])
def test_unverified_shop_fixture_parses_without_crashing(shop_name):
    # Placeholder content, so this only proves the guessed selectors don't
    # crash -- not that they're right. Verified shops are covered below,
    # against their real fixtures.
    if shop_name == "__none__":
        pytest.skip("no unverified shops left")
    shop = shop_by_name(shop_name)
    scraper.check_shop(shop, crawler_for(shop))


@pytest.mark.parametrize("shop_name", [s["name"] for s in VERIFIED] or ["__none__"])
def test_verified_shop_fixture_yields_real_products(shop_name):
    # A verified shop's fixture is a real saved response, so it must parse
    # into actual products -- an empty parse means the adapter has drifted.
    if shop_name == "__none__":
        pytest.skip("no verified shops yet")
    shop = shop_by_name(shop_name)
    items = scraper.FETCHERS[shop["platform"]](shop, crawler_for(shop))
    if items:
        assert any(i["title"] for i in items), f"{shop_name} fixture has no product titles"
        return

    # Some shops have no crawlable catalogue and are reached through a
    # producer index instead. Their fixture is that index, and one canned
    # response cannot stand in for the grower pages the adapter goes on to
    # follow -- the stub hands the index back sixteen times, so the parse
    # is legitimately empty. What must hold for such a fixture is that it
    # still names producers we watch.
    import autoselect
    growers = autoselect.find_producer_links(
        fixture_for(shop).read_text(), shop["url"], scraper.match_producers)
    assert growers, (
        f"{shop_name} is verified but its fixture yields neither products "
        f"nor links to any producer we watch"
    )


def test_every_shop_has_a_fixture_matching_its_platform():
    # Guards the drift that broke things when platforms were corrected:
    # a JSON shop must not be left holding an .html fixture.
    for shop in REAL_SHOPS:
        assert fixture_for(shop).exists(), (
            f"{shop['name']} is platform={shop['platform']} but has no "
            f"{fixture_for(shop).suffix} fixture"
        )


# The markers the fixture generators actually write -- probe.py and
# apply_issue.write_shop_fixture. Matching the bare word "placeholder"
# instead rejected a real saved page, because every shop's search box is
# an <input placeholder="Rechercher">: the test called a genuine fixture
# fake and blocked the commit that would have brought the shop live.
PLACEHOLDER_MARKER = re.compile(r"PLACEHOLDER\s+(?:FIXTURE|--)", re.I)


def test_verified_shops_do_not_carry_placeholder_fixtures():
    # The whole point of `verified` is that the fixture is real. If a
    # placeholder marker survives, the flag is lying.
    for shop in VERIFIED:
        body = fixture_for(shop).read_text()
        assert not PLACEHOLDER_MARKER.search(body), (
            f"{shop['name']} is verified but its fixture is still a placeholder"
        )


def test_the_placeholder_marker_matches_what_the_generators_write():
    """Both generators must keep writing something this recognises, or the
    check above silently passes on a fake fixture."""
    import apply_issue
    assert PLACEHOLDER_MARKER.search("UNVERIFIED PLACEHOLDER FIXTURE for x")
    assert PLACEHOLDER_MARKER.search("PLACEHOLDER -- replace with a real listing")
    # ...and must not fire on ordinary markup.
    assert not PLACEHOLDER_MARKER.search('<input placeholder="Rechercher un vin">')
    assert not PLACEHOLDER_MARKER.search('placeholder="Search"')

    src = inspect.getsource(apply_issue.write_shop_fixture)
    assert "PLACEHOLDER" in src


def test_levinnaturel_fixture_has_labet_hit():
    # Levinnaturel genuinely stocks Domaine Labet (confirmed by probe run
    # 30255245592), so its fixture -- placeholder or real -- should show it.
    shop = shop_by_name("levinnaturel")
    hits = scraper.check_shop(shop, crawler_for(shop))
    assert any(h["producer"] == "Labet" for h in hits)


class PagingCrawler:
    """Serves a scripted sequence of pages, recording the page numbers
    actually requested so pagination behaviour can be asserted."""

    def __init__(self, pages, budget_after=None):
        self._pages = pages
        self._budget_after = budget_after
        self.request_count = 0
        self.max_requests = 1000
        self.pages_requested = []

    def get(self, url, params=None):
        page = (params or {}).get("page")
        self.pages_requested.append(page)
        if self._budget_after is not None and len(self.pages_requested) > self._budget_after:
            import crawler
            raise crawler.BudgetExceeded(url)
        self.request_count += 1
        index = (page or 1) - 1
        body = self._pages[index] if index < len(self._pages) else self._empty()
        return FakeTextResponseJSON(body)

    def _empty(self):
        return {"products": []} if isinstance(self._pages[0], dict) else []


class FakeTextResponseJSON:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def shopify_page(n, count):
    return {"products": [
        {"title": f"Wine {n}-{i}", "vendor": "V", "handle": f"w-{n}-{i}",
         "body_html": "", "variants": [{"price": "10.00"}]}
        for i in range(count)
    ]}


def woo_page(n, count):
    return [
        {"name": f"Wine {n}-{i}", "permalink": f"https://s/{n}/{i}",
         "short_description": "", "description": "",
         "prices": {"price": "1000", "currency_minor_unit": 2}}
        for i in range(count)
    ]


def test_shopify_walks_all_pages_until_short_page():
    shop = shop_by_name("example-shopify-shop")
    pages = [shopify_page(1, scraper.SHOPIFY_PAGE_SIZE),
             shopify_page(2, scraper.SHOPIFY_PAGE_SIZE),
             shopify_page(3, 7)]
    c = PagingCrawler(pages)

    items = scraper.fetch_shopify(shop, c)

    assert len(items) == scraper.SHOPIFY_PAGE_SIZE * 2 + 7
    assert c.pages_requested == [1, 2, 3]


def test_shopify_stops_on_empty_page():
    shop = shop_by_name("example-shopify-shop")
    c = PagingCrawler([shopify_page(1, scraper.SHOPIFY_PAGE_SIZE), {"products": []}])

    items = scraper.fetch_shopify(shop, c)

    assert len(items) == scraper.SHOPIFY_PAGE_SIZE
    assert c.pages_requested == [1, 2]


def test_shopify_single_short_page_makes_one_request():
    shop = shop_by_name("example-shopify-shop")
    c = PagingCrawler([shopify_page(1, 12)])

    items = scraper.fetch_shopify(shop, c)

    assert len(items) == 12
    assert c.pages_requested == [1]


def test_pagination_respects_max_pages_cap(capsys):
    shop = shop_by_name("example-shopify-shop")
    pages = [shopify_page(n, scraper.SHOPIFY_PAGE_SIZE) for n in range(scraper.MAX_PAGES_PER_SHOP + 5)]
    c = PagingCrawler(pages)

    items = scraper.fetch_shopify(shop, c)

    assert len(c.pages_requested) == scraper.MAX_PAGES_PER_SHOP
    assert len(items) == scraper.SHOPIFY_PAGE_SIZE * scraper.MAX_PAGES_PER_SHOP
    assert "TRUNCATED" in capsys.readouterr().out


def test_budget_exhaustion_mid_pagination_keeps_earlier_pages(capsys):
    shop = shop_by_name("example-shopify-shop")
    pages = [shopify_page(n, scraper.SHOPIFY_PAGE_SIZE) for n in range(5)]
    c = PagingCrawler(pages, budget_after=2)

    items = scraper.fetch_shopify(shop, c)

    # Two full pages survived; the run didn't lose them to the budget.
    assert len(items) == scraper.SHOPIFY_PAGE_SIZE * 2
    assert "TRUNCATED" in capsys.readouterr().out


def test_woocommerce_walks_all_pages():
    shop = shop_by_name("example-woo-shop")
    pages = [woo_page(1, scraper.WOO_PAGE_SIZE), woo_page(2, 4)]
    c = PagingCrawler(pages)

    items = scraper.fetch_woocommerce(shop, c)

    assert len(items) == scraper.WOO_PAGE_SIZE + 4
    assert c.pages_requested == [1, 2]


def test_producer_deep_in_catalogue_is_found():
    # The regression this whole change exists to prevent: a tracked
    # producer sitting on page 3 must not read as "not in stock".
    shop = shop_by_name("example-shopify-shop")
    page3 = shopify_page(3, 2)
    page3["products"][1]["title"] = "Pierre Overnoy Arbois Pupillin Ploussard 2019"
    pages = [shopify_page(1, scraper.SHOPIFY_PAGE_SIZE),
             shopify_page(2, scraper.SHOPIFY_PAGE_SIZE),
             page3]

    hits = scraper.check_shop(shop, PagingCrawler(pages))

    assert [h["producer"] for h in hits] == ["Overnoy/Houillon"]


def test_html_shops_all_declare_selectors():
    # A regression guard: an html shop without selectors raises KeyError
    # deep inside fetch_html at run time rather than failing loudly here.
    for shop in scraper.SHOPS:
        if shop["platform"] == "html":
            for key in ("item_selector", "title_selector", "price_selector"):
                assert key in shop, f"{shop['name']} (html) is missing {key}"


def test_an_index_fixture_satisfies_the_verified_shop_check():
    """The producer-index branch of the check above, exercised directly
    against the real leszinzinsduvin index so it cannot rot unnoticed."""
    import autoselect
    body = (FIXTURES / "leszinzinsduvin-domaines-excerpt.html").read_text()

    # It is an index: no prices on it at all.
    assert autoselect.find_products(
        body, "https://www.leszinzinsduvin.com/domaines.php",
        scraper.PRICE_PATTERN, scraper.parse_price) == []
    # ...but it names growers we watch, which is what makes it usable.
    growers = autoselect.find_producer_links(
        body, "https://www.leszinzinsduvin.com/domaines.php", scraper.match_producers)
    assert {p for p, _ in growers} >= {"Ganevat", "Bruyere Houillon"}


# --- shared surnames must not collide ----------------------------------------

OTHER_ESTATES = [
    "Chardonnay Charmille 2023 - Domaine Overnoy",
    "Trousseau 2023 - Domaine Overnoy",
    "Overnoy-Crinquand Ploussard 2020",
    "Overnoy Jean Louis et Guillaume, Arbois",
    "Corentin Houillon, Savoie",
    "Charlotte et Aurelien Houillon",
    "Fimbel-Houillon",
]


@pytest.mark.parametrize("title", OTHER_ESTATES)
def test_another_estate_is_not_reported_as_pupillin(title):
    """Several unrelated Jura and Savoie growers share these surnames.
    Asserting the misattribution is absent rather than that nothing
    matches, so tracking one of them later stays legitimate."""
    assert "Overnoy/Houillon" not in scraper.match_producers(title)


@pytest.mark.parametrize("title", [
    "Overnoy-Houillon Arbois Pupillin 2018",
    "Overnoy Pierre/Houillon Emmanuel",
    "Pierre Overnoy Arbois Pupillin",
    "Emmanuel Houillon Ploussard",
])
def test_the_pupillin_estate_still_matches_however_it_is_written(title):
    assert scraper.match_producers(title) == ["Overnoy/Houillon"]


def test_bruyere_houillon_is_its_own_estate():
    assert scraper.match_producers("Bruyère houillon Savagnin") == ["Bruyere Houillon"]


# --- stock, as the platform reports it ---------------------------------------

def shopify_payload(available, title="Poulprix 2024 Ganevat"):
    variant = {"price": "29.00", "title": "Default"}
    if available is not None:
        variant["available"] = available
    return {"products": [{"title": title, "vendor": "Ganevat", "handle": "p",
                          "variants": [variant]}]}


def one_page(payload):
    class Paged(FakeCrawler):
        def __init__(self):
            super().__init__(None)
            self.n = 0

        def get(self, url, params=None):
            self.n += 1
            empty = {"products": []} if isinstance(payload, dict) else []
            return FakeJSONResponse(payload if self.n == 1 else empty)
    return Paged()


def test_a_sold_out_shopify_variant_is_not_alerted():
    shop = shop_by_name("example-shopify-shop")
    assert scraper.fetch_shopify(shop, one_page(shopify_payload(False)))
    assert scraper.check_shop(shop, one_page(shopify_payload(False))) == []


def test_an_available_shopify_variant_still_alerts():
    shop = shop_by_name("example-shopify-shop")
    assert len(scraper.check_shop(shop, one_page(shopify_payload(True)))) == 1


def test_an_unstated_shopify_stock_is_not_treated_as_sold_out():
    """Silence from the API is not a reason to hide a wine."""
    shop = shop_by_name("example-shopify-shop")
    assert len(scraper.check_shop(shop, one_page(shopify_payload(None)))) == 1


def test_a_title_saying_epuise_is_caught_even_when_the_api_is_silent():
    shop = shop_by_name("example-shopify-shop")
    payload = shopify_payload(None, title="Poulprix 2024 Ganevat - ÉPUISÉ")
    assert scraper.check_shop(shop, one_page(payload)) == []


def test_a_woocommerce_product_out_of_stock_is_not_alerted():
    shop = shop_by_name("example-woo-shop")
    product = {"name": "Labet Chardonnay", "permalink": "https://x.test/p",
               "prices": {"price": "3900", "currency_minor_unit": 2},
               "is_in_stock": False}
    assert scraper.check_shop(shop, one_page([product])) == []
    product["is_in_stock"] = True
    assert len(scraper.check_shop(shop, one_page([product]))) == 1


def test_the_real_mareehaute_catalogue_reports_only_buyable_bottles():
    shop = shop_by_name("mareehaute")
    data = load_json("mareehaute.json")
    hits = scraper.check_shop(shop, one_page(data))
    assert hits, "the shop should still yield something"
    assert all(h["producer"] != "Overnoy/Houillon" for h in hits), (
        "its Overnoy bottles are Domaine Overnoy, a different estate"
    )
    items = {i["url"]: i for i in scraper.fetch_shopify(shop, one_page(data))}
    assert all(items[h["url"]]["in_stock"] for h in hits)
