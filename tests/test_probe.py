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


def test_probe_never_mutates_shops_or_verified_flags():
    before = json.dumps(scraper.SHOPS, sort_keys=True)
    stub = StubCrawler({"/products.json": shopify_response()})

    probe.probe_shop(a_shop(), stub)

    assert json.dumps(scraper.SHOPS, sort_keys=True) == before
    assert all(s.get("verified") is False for s in scraper.SHOPS)
