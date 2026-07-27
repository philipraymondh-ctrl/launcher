import json
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

    scraper.main()

    out = capsys.readouterr().out
    unverified_shops = [s for s in scraper.SHOPS if not s.get("verified", True)]
    assert unverified_shops, "expected at least one unverified placeholder shop"
    for shop in unverified_shops:
        assert f"[{shop['name']}] skipped: unverified placeholder" in out


REAL_SHOPS = [s for s in scraper.SHOPS if not s["name"].startswith("example-")]


@pytest.mark.parametrize(
    "shop_name",
    [s["name"] for s in REAL_SHOPS if s["platform"] == "html"],
)
def test_placeholder_html_fixture_parses_without_crashing(shop_name):
    shop = shop_by_name(shop_name)
    assert shop["verified"] is False
    html = (FIXTURES / f"{shop_name}.html").read_text()

    hits = scraper.check_shop(shop, FakeCrawler(FakeTextResponse(html)))

    # Placeholder content deliberately contains no producer alias -- this
    # only proves the guessed selectors don't crash, not that they're right.
    assert hits == []


@pytest.mark.parametrize(
    "shop_name",
    [s["name"] for s in REAL_SHOPS if s["platform"] in ("shopify", "woocommerce")],
)
def test_placeholder_json_fixture_parses_without_crashing(shop_name):
    # These shops' platforms are confirmed by probe run 30255245592, but
    # the fixture bodies are still placeholders, so this asserts the right
    # fetcher runs cleanly -- not that the data is real.
    shop = shop_by_name(shop_name)
    assert shop["verified"] is False
    data = load_json(f"{shop_name}.json")

    scraper.check_shop(shop, FakeCrawler(FakeJSONResponse(data)))


def test_every_shop_has_a_fixture_matching_its_platform():
    # Guards the drift that broke things when platforms were corrected:
    # a JSON shop must not be left holding an .html fixture.
    for shop in REAL_SHOPS:
        ext = "html" if shop["platform"] == "html" else "json"
        assert (FIXTURES / f"{shop['name']}.{ext}").exists(), (
            f"{shop['name']} is platform={shop['platform']} but has no .{ext} fixture"
        )


def test_levinnaturel_placeholder_fixture_has_labet_hit():
    shop = shop_by_name("levinnaturel")
    assert shop["verified"] is False
    data = load_json("levinnaturel.json")

    hits = scraper.check_shop(shop, FakeCrawler(FakeJSONResponse(data)))

    assert any(h["producer"] == "Labet" for h in hits)


def test_vinopura_placeholder_fixture_parses_without_crashing():
    shop = shop_by_name("vinopura")
    assert shop["verified"] is False
    data = load_json("vinopura.json")

    hits = scraper.check_shop(shop, FakeCrawler(FakeJSONResponse(data)))

    assert hits == []


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
