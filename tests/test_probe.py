import json
from pathlib import Path

import pytest

import crawler
import probe
import scraper

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_output(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "OUTPUT_DIR", tmp_path / "probe_output")
    probe.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return probe.OUTPUT_DIR


class StubCrawler:
    """Serves canned responses/exceptions keyed by which endpoint is asked
    for, so probe order and fallback behaviour can be asserted."""

    def __init__(self, handlers):
        self._handlers = handlers
        self.request_count = 0
        self.max_requests = 100
        self.user_agent = "test"
        self.skipped_disallowed = []
        self.calls = []

    def get(self, url, params=None):
        self.request_count += 1
        self.calls.append(url)
        for fragment, outcome in self._handlers.items():
            if fragment in url:
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
        raise crawler.UpstreamError("no stub for this url")


def shopify_response():
    return crawler.FetchResult(200, (FIXTURES / "example-shopify-shop.json").read_text())


def woo_response():
    return crawler.FetchResult(200, (FIXTURES / "example-woo-shop.json").read_text())


def html_response():
    return crawler.FetchResult(200, (FIXTURES / "example-html-shop.html").read_text())


def a_shop(name="testshop", platform="html", url="https://testshop.example"):
    return {"name": name, "platform": platform, "url": url, "verified": False}


def test_detects_shopify_first_and_does_not_try_other_endpoints():
    stub = StubCrawler({"/products.json": shopify_response()})

    result = probe.probe_shop(a_shop(), stub)

    assert result["status"] == "ok"
    assert result["detected_platform"] == "shopify"
    assert result["products_parsed"] == 2
    assert result["producer_hits"] == ["Ganevat"]
    # Shopify answered, so Woo/HTML must never have been requested.
    assert len(stub.calls) == 1


def not_found():
    return crawler.UpstreamError("HTTP 404", status_code=404)


def unreachable():
    return crawler.UpstreamError("Connection refused")


def test_falls_back_to_woocommerce_when_shopify_absent():
    stub = StubCrawler({
        "/products.json": not_found(),
        "/wp-json/wc/store/v1/products": woo_response(),
    })

    result = probe.probe_shop(a_shop(), stub)

    assert result["detected_platform"] == "woocommerce"
    assert result["producer_hits"] == ["Labet"]


def test_falls_back_to_html_when_no_json_api():
    stub = StubCrawler({
        "/products.json": not_found(),
        "/wp-json/": not_found(),
        "https://testshop.example": html_response(),
    })

    result = probe.probe_shop(a_shop(), stub)

    assert result["detected_platform"] == "html"
    assert result["producer_hits"] == ["Overnoy/Houillon"]


def test_unreachable_host_stops_after_one_endpoint():
    # A connection-level failure means the host is down; probing its other
    # two endpoints would just burn retries rediscovering that.
    stub = StubCrawler({
        "/products.json": unreachable(),
        "/wp-json/": unreachable(),
        "https://testshop.example": unreachable(),
    })

    result = probe.probe_shop(a_shop(), stub)

    assert result["status"] == "host_unreachable"
    assert len(stub.calls) == 1


def test_http_404_does_not_stop_the_probe():
    # Contrast with the above: the host answered, it just isn't Shopify.
    stub = StubCrawler({
        "/products.json": not_found(),
        "/wp-json/": not_found(),
        "https://testshop.example": html_response(),
    })

    result = probe.probe_shop(a_shop(), stub)

    assert result["status"] == "ok"
    assert len(stub.calls) == 3


def test_saves_real_body_for_fixture_use(isolated_output):
    stub = StubCrawler({"/products.json": shopify_response()})

    result = probe.probe_shop(a_shop(name="realshop"), stub)

    saved = isolated_output / "realshop.json"
    assert saved.exists()
    assert json.loads(saved.read_text())["products"][0]["vendor"] == "Domaine Ganevat"
    assert result["saved_as"].endswith("realshop.json")


def test_empty_body_is_recorded_not_saved(isolated_output):
    stub = StubCrawler({
        "/products.json": crawler.FetchResult(200, "   "),
        "/wp-json/": not_found(),
        "https://testshop.example": crawler.FetchResult(200, "  "),
    })

    result = probe.probe_shop(a_shop(), stub)

    assert result["status"] == "failed"
    assert any("empty body" in a["outcome"] for a in result["attempts"])
    assert list(isolated_output.iterdir()) == []


def test_robots_disallow_is_recorded_and_skipped():
    stub = StubCrawler({
        "/products.json": crawler.Disallowed("blocked"),
        "/wp-json/": crawler.Disallowed("blocked"),
        "https://testshop.example": crawler.Disallowed("blocked"),
    })

    result = probe.probe_shop(a_shop(), stub)

    assert result["status"] == "failed"
    assert all("robots.txt disallows" in a["outcome"] for a in result["attempts"])


def test_budget_exhaustion_marks_shop_not_reached():
    stub = StubCrawler({"/products.json": crawler.BudgetExceeded("budget")})

    result = probe.probe_shop(a_shop(), stub)

    assert result["status"] == "not_reached"


def test_responding_but_unparseable_endpoint_is_not_accepted():
    # A shop that returns HTML from /products.json (a soft-404 landing
    # page) must not be mistaken for a working Shopify endpoint.
    stub = StubCrawler({
        "/products.json": crawler.FetchResult(200, "<html><body>Not found</body></html>"),
        "/wp-json/": not_found(),
        "https://testshop.example": not_found(),
    })

    result = probe.probe_shop(a_shop(), stub)

    assert result["status"] == "failed"
    assert result["detected_platform"] is None


def test_zero_product_response_is_not_treated_as_success():
    stub = StubCrawler({
        "/products.json": crawler.FetchResult(200, json.dumps({"products": []})),
        "/wp-json/": not_found(),
        "https://testshop.example": not_found(),
    })

    result = probe.probe_shop(a_shop(), stub)

    assert result["status"] == "failed"
    assert any("zero products" in a["outcome"] for a in result["attempts"])


def test_probe_without_apply_never_mutates_shops():
    # Deliberately not "no shop is ever verified" -- probe --apply is
    # supposed to verify shops. What must hold is that the read-only path
    # leaves SHOPS untouched.
    before = json.dumps(scraper.SHOPS, sort_keys=True)
    stub = StubCrawler({"/products.json": shopify_response()})

    probe.probe_shop(a_shop(), stub)

    assert json.dumps(scraper.SHOPS, sort_keys=True) == before


def test_apply_only_verifies_shops_that_actually_probed_ok(tmp_path, monkeypatch):
    """The standing decision, exercised end to end: a shop that did not
    return a real parsed response this run must never come out verified."""
    monkeypatch.setattr(probe, "OUTPUT_DIR", tmp_path)
    original_src = probe.scraper_source_path().read_text()
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()

    # Redirect both the source file and the fixture directory into tmp so
    # the real repo is untouched by this test.
    sandbox_src = tmp_path / "scraper_copy.py"
    sandbox_src.write_text(original_src)
    monkeypatch.setattr(probe, "scraper_source_path", lambda: sandbox_src)
    monkeypatch.setattr(probe, "__file__", str(tmp_path / "probe.py"))
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "fixtures").mkdir()

    (tmp_path / "whynat.json").write_text(json.dumps({
        "products": [{"title": "Real Wine 2020", "vendor": "V", "handle": "r",
                      "body_html": "", "variants": [{"price": "30.00"}]}]
    }))
    results = [
        {"shop": "whynat", "status": "ok", "detected_platform": "shopify",
         "saved_as": "probe_output/whynat.json"},
        {"shop": "vinibee", "status": "host_unreachable", "detected_platform": None,
         "saved_as": None},
        {"shop": "purovino", "status": "failed", "detected_platform": None, "saved_as": None},
    ]

    applied, skipped = probe.apply_results(results)

    assert applied == ["whynat"]
    assert set(skipped) == {"vinibee", "purovino"}

    ns = {}
    exec(compile(sandbox_src.read_text(), "<s>", "exec"), {"__name__": "notmain"}, ns)
    shops = {s["name"]: s for s in ns["SHOPS"]}
    assert shops["whynat"]["verified"] is True
    assert shops["vinibee"]["verified"] is False
    assert shops["purovino"]["verified"] is False
    # The real repo's scraper.py must be untouched by this test.
    assert probe.scraper_source_path() == sandbox_src


def test_trim_keeps_every_producer_match():
    products = [
        {"title": f"Filler {i}", "vendor": "V", "handle": f"f{i}", "body_html": "",
         "variants": [{"price": "10.00"}]}
        for i in range(120)
    ]
    products[99] = {"title": "Domaine Ganevat Chardonnay 2020", "vendor": "Ganevat",
                    "handle": "g", "body_html": "", "variants": [{"price": "80.00"}]}
    products[110] = {"title": "Pierre Overnoy Ploussard 2019", "vendor": "Overnoy",
                     "handle": "o", "body_html": "", "variants": [{"price": "300.00"}]}

    trimmed, total = probe.trim_payload("shopify", json.dumps({"products": products}))

    kept = json.loads(trimmed)["products"]
    assert total == 120
    assert len(kept) == probe.FIXTURE_SAMPLE
    titles = " ".join(p["title"] for p in kept)
    # Both matches were far past the sample cut-off, so a naive head-slice
    # would have dropped them and made the fixture prove nothing.
    assert "Ganevat" in titles and "Overnoy" in titles


def test_trim_of_woocommerce_keeps_shape():
    products = [
        {"name": f"Wine {i}", "permalink": f"https://s/{i}", "short_description": "",
         "description": "", "prices": {"price": "1000", "currency_minor_unit": 2}}
        for i in range(60)
    ]
    products[50]["name"] = "Domaine Labet Chardonnay 2020"

    trimmed, total = probe.trim_payload("woocommerce", json.dumps(products))

    kept = json.loads(trimmed)
    assert isinstance(kept, list) and total == 60
    assert any("Labet" in p["name"] for p in kept)


def test_parse_failure_records_a_body_snippet():
    # Without the snippet a "not JSON" failure is undiagnosable from the
    # report: the body is discarded because only successes are saved.
    stub = StubCrawler({
        "/products.json": crawler.FetchResult(200, "<html><body>Attention Required! Cloudflare</body></html>"),
        "/wp-json/": not_found(),
        "https://testshop.example": not_found(),
    })

    result = probe.probe_shop(a_shop(), stub)

    shopify_attempt = next(a for a in result["attempts"] if a["platform"] == "shopify")
    assert "parse failed" in shopify_attempt["outcome"]
    assert "Cloudflare" in shopify_attempt["body_snippet"]


def test_zero_product_html_page_is_saved_for_selector_work(isolated_output):
    # The 9 html shops that return a real page but match no selectors are
    # exactly the ones needing hand-written selectors -- keep their pages.
    page = "<html><body><article class='card'>Some Wine 2020</article></body></html>"
    stub = StubCrawler({
        "/products.json": not_found(),
        "/wp-json/": not_found(),
        "https://testshop.example": crawler.FetchResult(200, page),
    })

    result = probe.probe_shop(a_shop(), stub)

    assert result["status"] == "failed"
    saved = isolated_output / "testshop.unparsed.html"
    assert saved.exists() and "Some Wine 2020" in saved.read_text()
