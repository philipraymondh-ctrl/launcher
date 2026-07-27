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


@pytest.mark.parametrize(
    "shop_name",
    [
        "leszinzinsduvin", "winenot", "vinnouveau", "lespeauxdevins", "lacavedespapilles",
        "vinnaturel", "whynat", "vinibee", "vinscheznous", "petitescaves", "cavepurjus", "bbn",
        "purewijnen", "amberbottleshop", "naturavin", "vinnaturelbe", "vinovivo", "vinifine",
        "zuiverwijnen", "volatilewines", "biowijnclub", "puurwijnshop", "purovino",
    ],
)
def test_placeholder_html_fixture_parses_without_crashing(shop_name):
    shop = shop_by_name(shop_name)
    assert shop["verified"] is False
    html = (FIXTURES / f"{shop_name}.html").read_text()

    hits = scraper.check_shop(shop, FakeCrawler(FakeTextResponse(html)))

    # Placeholder content deliberately contains no producer alias -- this
    # only proves the guessed selectors don't crash, not that they're right.
    assert hits == []


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
