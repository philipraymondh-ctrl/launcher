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


def test_shopify_fixture_matches_ganevat_and_ignores_other_wines(monkeypatch):
    data = load_json("example-shopify-shop.json")
    monkeypatch.setattr(scraper.requests, "get", lambda *a, **k: FakeJSONResponse(data))
    shop = next(s for s in scraper.SHOPS if s["platform"] == "shopify")

    hits = scraper.check_shop(shop)

    assert len(hits) == 1
    assert hits[0]["producer"] == "Ganevat"
    assert hits[0]["price"] == pytest.approx(68.00)


def test_woocommerce_fixture_matches_labet(monkeypatch):
    data = load_json("example-woo-shop.json")
    monkeypatch.setattr(scraper.requests, "get", lambda *a, **k: FakeJSONResponse(data))
    shop = next(s for s in scraper.SHOPS if s["platform"] == "woocommerce")

    hits = scraper.check_shop(shop)

    assert len(hits) == 1
    assert hits[0]["producer"] == "Labet"
    assert hits[0]["price"] == pytest.approx(39.00)


def test_html_fixture_matches_overnoy_houillon_alias(monkeypatch):
    html = (FIXTURES / "example-html-shop.html").read_text()
    monkeypatch.setattr(scraper.requests, "get", lambda *a, **k: FakeTextResponse(html))
    shop = next(s for s in scraper.SHOPS if s["platform"] == "html")

    hits = scraper.check_shop(shop)

    assert len(hits) == 1
    assert hits[0]["producer"] == "Overnoy/Houillon"
    assert hits[0]["price"] == pytest.approx(189.00)


def test_empty_html_response_raises_empty_response_error(monkeypatch):
    monkeypatch.setattr(scraper.requests, "get", lambda *a, **k: FakeTextResponse("   "))
    shop = next(s for s in scraper.SHOPS if s["platform"] == "html")

    with pytest.raises(scraper.EmptyResponseError):
        scraper.fetch_html(shop)


def test_alias_matching_is_accent_and_case_insensitive():
    assert scraper.match_producers("Domaine GANEVAT Chardonnay") == ["Ganevat"]
    assert scraper.match_producers("cuvee by clémence gerbet") == ["Clemence Gerbet"]
    assert scraper.match_producers("some unrelated winery") == []


def test_price_parser_ignores_bare_vintage_year():
    assert scraper.parse_price("2018 Domaine Ganevat Chardonnay") is None
    assert scraper.parse_price("Domaine Ganevat 2018 - 45,00€") == pytest.approx(45.00)
    assert scraper.parse_price("$89.99 (vintage 2019)") == pytest.approx(89.99)


def shop_by_name(name):
    return next(s for s in scraper.SHOPS if s["name"] == name)


def test_unverified_shops_are_skipped_by_main(monkeypatch, capsys):
    monkeypatch.setenv("DRY_RUN", "1")
    monkeypatch.setattr(scraper, "DRY_RUN", True)
    monkeypatch.setattr(
        scraper.requests,
        "get",
        lambda *a, **k: (_ for _ in ()).throw(scraper.requests.RequestException("no network in test")),
    )

    scraper.main()

    out = capsys.readouterr().out
    unverified_shops = [s for s in scraper.SHOPS if not s.get("verified", True)]
    assert unverified_shops, "expected at least one unverified placeholder shop"
    for shop in unverified_shops:
        assert f"[{shop['name']}] skipped: unverified placeholder" in out


@pytest.mark.parametrize(
    "shop_name",
    [
        s["name"]
        for s in [
            {"name": "leszinzinsduvin"}, {"name": "winenot"}, {"name": "vinnouveau"},
            {"name": "lespeauxdevins"}, {"name": "lacavedespapilles"}, {"name": "vinnaturel"},
            {"name": "whynat"}, {"name": "vinibee"}, {"name": "vinscheznous"},
            {"name": "petitescaves"}, {"name": "cavepurjus"}, {"name": "bbn"},
            {"name": "purewijnen"}, {"name": "amberbottleshop"}, {"name": "naturavin"},
            {"name": "vinnaturelbe"}, {"name": "vinovivo"}, {"name": "vinifine"},
            {"name": "zuiverwijnen"}, {"name": "volatilewines"}, {"name": "biowijnclub"},
            {"name": "puurwijnshop"}, {"name": "purovino"},
        ]
    ],
)
def test_placeholder_html_fixture_parses_without_crashing(monkeypatch, shop_name):
    shop = shop_by_name(shop_name)
    assert shop["verified"] is False
    html = (FIXTURES / f"{shop_name}.html").read_text()
    monkeypatch.setattr(scraper.requests, "get", lambda *a, **k: FakeTextResponse(html))

    hits = scraper.check_shop(shop)

    # Placeholder content deliberately contains no producer alias -- this
    # only proves the guessed selectors don't crash, not that they're right.
    assert hits == []


def test_levinnaturel_placeholder_fixture_has_labet_hit(monkeypatch):
    shop = shop_by_name("levinnaturel")
    assert shop["verified"] is False
    data = load_json("levinnaturel.json")
    monkeypatch.setattr(scraper.requests, "get", lambda *a, **k: FakeJSONResponse(data))

    hits = scraper.check_shop(shop)

    assert any(h["producer"] == "Labet" for h in hits)


def test_vinopura_placeholder_fixture_parses_without_crashing(monkeypatch):
    shop = shop_by_name("vinopura")
    assert shop["verified"] is False
    data = load_json("vinopura.json")
    monkeypatch.setattr(scraper.requests, "get", lambda *a, **k: FakeJSONResponse(data))

    hits = scraper.check_shop(shop)

    assert hits == []
