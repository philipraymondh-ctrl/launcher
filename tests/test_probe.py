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
    # Diagnostics land in a committed directory, so an unpatched test wrote
    # testshop.html into the repo and a probe run committed it.
    monkeypatch.setattr(probe, "DIAGNOSTIC_DIR", tmp_path / "probe_pages")
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
    # The rule, not the endpoint count: a 404 means "host is up, wrong
    # platform", so the probe must carry on past it to the HTML attempt.
    # Pinning an exact number here just breaks when the candidate list
    # grows, which is what catalogue discovery did.
    assert "https://testshop.example/products.json" in stub.calls
    assert "https://testshop.example" in stub.calls
    assert stub.calls.index("https://testshop.example/products.json") < \
        stub.calls.index("https://testshop.example")


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


SYNTHETIC_SCRAPER = '''SHOPS = [
    {
        "name": "alpha",
        "platform": "html",
        "url": "https://alpha.example",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
    {
        "name": "beta",
        "platform": "html",
        "url": "https://beta.example",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
    {
        "name": "gamma",
        "platform": "html",
        "url": "https://gamma.example",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
]


class EmptyResponseError(Exception):
    pass
'''


def test_apply_only_verifies_shops_that_actually_probed_ok(tmp_path, monkeypatch):
    """The standing decision, exercised end to end: a shop that did not
    return a real parsed response this run must never come out verified.

    Uses a synthetic SHOPS source on purpose. An earlier version copied the
    real scraper.py and assumed its shops were unverified -- which broke the
    moment `--apply` legitimately verified one on a runner, failing CI and
    blocking the very commit it was meant to protect.
    """
    monkeypatch.setattr(probe, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(probe, "__file__", str(tmp_path / "probe.py"))
    (tmp_path / "tests" / "fixtures").mkdir(parents=True)

    sandbox_src = tmp_path / "scraper_copy.py"
    sandbox_src.write_text(SYNTHETIC_SCRAPER)
    monkeypatch.setattr(probe, "scraper_source_path", lambda: sandbox_src)

    (tmp_path / "alpha.json").write_text(json.dumps({
        "products": [{"title": "Real Wine 2020", "vendor": "V", "handle": "r",
                      "body_html": "", "variants": [{"price": "30.00"}]}]
    }))
    results = [
        {"shop": "alpha", "status": "ok", "detected_platform": "shopify",
         "saved_as": "probe_output/alpha.json"},
        {"shop": "beta", "status": "host_unreachable", "detected_platform": None,
         "saved_as": None},
        {"shop": "gamma", "status": "failed", "detected_platform": None, "saved_as": None},
    ]

    applied, skipped = probe.apply_results(results)

    assert applied == ["alpha"]
    assert set(skipped) == {"beta", "gamma"}

    ns = {}
    exec(compile(sandbox_src.read_text(), "<s>", "exec"), {"__name__": "notmain"}, ns)
    shops = {s["name"]: s for s in ns["SHOPS"]}
    assert shops["alpha"]["verified"] is True
    assert shops["alpha"]["platform"] == "shopify"
    # A JSON platform must not keep html-only selectors.
    assert "item_selector" not in shops["alpha"]
    # Neither failure may be verified, whatever the rest of the run did.
    assert shops["beta"]["verified"] is False
    assert shops["gamma"]["verified"] is False


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


def test_a_guessed_catalog_path_does_not_replace_the_search():
    """It goes first, but the rest of the list must still be tried -- short
    -circuiting here meant the shop catalogue discovery was written for only
    ever tried its one guessed path."""
    shop = dict(a_shop(), catalog_path="vins.php")
    urls = [url for _, url, _, _ in probe.candidate_endpoints(shop)]
    html_urls = [u for u in urls if "products.json" not in u and "wp-json" not in u]
    assert html_urls[0].endswith("/vins.php")
    assert len(html_urls) > 1, "no other catalogue paths were tried"
    assert any(u.endswith("/boutique") for u in html_urls)


def test_a_recorded_path_is_not_tried_twice():
    shop = dict(a_shop(), catalog_path="boutique")
    urls = [url for _, url, _, _ in probe.candidate_endpoints(shop)]
    assert urls.count(urls[2]) == 1
    assert len(urls) == len(set(urls))


def test_each_unparsed_page_is_kept_separately(monkeypatch, tmp_path):
    """One file per shop meant the last failing page overwrote the one that
    mattered -- the catalogue diagnostic was replaced by the home page. The
    host is in the name too: four sites' landing pages all slugged to
    "index", so capturing four in one run left one file."""
    monkeypatch.setattr(probe, "DIAGNOSTIC_DIR", tmp_path)
    body = "<html><body><p>rien</p></body></html>"
    probe.save_diagnostic_page("zzzshop", "https://zzz.example/vins.php", body)
    probe.save_diagnostic_page("zzzshop", "https://zzz.example/", body)
    probe.save_diagnostic_page("zzzshop", "https://other.example/", body)
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == [
        "zzzshop.other-example.index.html",
        "zzzshop.zzz-example.index.html",
        "zzzshop.zzz-example.vins-php.html",
    ]


def test_diagnostics_never_land_in_the_repo_during_tests():
    """The directory is committed, so a leaked test file gets pushed."""
    stray = Path(__file__).parent.parent / "probe_pages" / "testshop.html"
    assert not stray.exists(), "a test wrote a diagnostic into the repo"


# --- who gets probed: forgiving input, loud mistakes ---------------------------
#
# The shop list came from a text box on a phone. "Lapangee, lavinoterie" broke
# the workflow on the space; and even quoted, "Lapangee" matches no shop --
# the entry is named "pangee" -- so the probe would have selected nothing and
# exited 0, reporting success for work it never did.

ROSTER = [
    {"name": "lavinoterie", "url": "https://lavinoterie.fr", "platform": "shopify",
     "verified": False},
    {"name": "pangee", "url": "https://la-pangee.com/fr", "platform": "html",
     "verified": False},
    {"name": "mareehaute", "url": "https://www.mareehaute.vin", "platform": "shopify",
     "verified": True},
    {"name": "example-shopify-shop", "url": "https://example.example.com",
     "platform": "shopify", "verified": False},
]


def test_a_comma_and_a_space_are_both_separators():
    assert probe.parse_names("lavinoterie, pangee") == ["lavinoterie", "pangee"]
    assert probe.parse_names("lavinoterie,pangee") == ["lavinoterie", "pangee"]
    assert probe.parse_names(" lavinoterie   pangee ") == ["lavinoterie", "pangee"]
    assert probe.parse_names("lavinoterie,,  ,pangee") == ["lavinoterie", "pangee"]
    assert probe.parse_names("") == []
    assert probe.parse_names(None) == []


def test_a_name_is_matched_however_it_was_typed():
    chosen, unknown = probe.select_shops(ROSTER, only="LaVinoterie, PANGEE")
    assert [s["name"] for s in chosen] == ["lavinoterie", "pangee"]
    assert unknown == []


def test_a_name_that_matches_nothing_is_reported_not_ignored():
    """"Lapangee" is not a shop. Silently probing nothing is the failure."""
    chosen, unknown = probe.select_shops(ROSTER, only="Lapangee, lavinoterie")
    assert [s["name"] for s in chosen] == ["lavinoterie"]
    assert unknown == ["Lapangee"]


def test_the_examples_are_never_probed():
    chosen, _ = probe.select_shops(ROSTER)
    assert "example-shopify-shop" not in [s["name"] for s in chosen]


def test_verified_shops_are_left_alone_unless_asked_for():
    chosen, _ = probe.select_shops(ROSTER)
    assert [s["name"] for s in chosen] == ["lavinoterie", "pangee"]
    chosen, _ = probe.select_shops(ROSTER, include_verified=True)
    assert "mareehaute" in [s["name"] for s in chosen]


def test_naming_an_already_verified_shop_still_selects_it():
    """Asking for a shop by name is asking for it, verified or not --
    otherwise "probe mareehaute" quietly probes nothing."""
    chosen, unknown = probe.select_shops(ROSTER, only="mareehaute")
    assert [s["name"] for s in chosen] == ["mareehaute"]
    assert unknown == []


def test_an_unknown_name_stops_the_run(monkeypatch, capsys):
    monkeypatch.setattr(probe.scraper, "SHOPS", ROSTER)
    monkeypatch.setattr("sys.argv", ["probe.py", "--only", "Lapangee"])
    with pytest.raises(SystemExit) as exc:
        probe.main()
    assert exc.value.code != 0
    out = capsys.readouterr().out + capsys.readouterr().err
    assert "Lapangee" in out
    assert "lavinoterie" in out, "the error must name the shops that do exist"


def test_selecting_nothing_at_all_stops_the_run(monkeypatch, capsys):
    """Every shop already verified is a legitimate state, but "probed 0 shops,
    success" is not a useful answer to a button press."""
    monkeypatch.setattr(probe.scraper, "SHOPS",
                        [{"name": "a", "url": "https://a.test", "platform": "html",
                          "verified": True}])
    monkeypatch.setattr("sys.argv", ["probe.py"])
    with pytest.raises(SystemExit) as exc:
        probe.main()
    assert exc.value.code != 0
    assert "include-verified" in capsys.readouterr().out.lower()


# --- a read-only probe must say that it saved nothing --------------------------

def test_a_read_only_probe_that_found_shops_says_so_loudly(capsys):
    results = [
        {"shop": "lavinoterie", "status": "ok", "products_parsed": 250},
        {"shop": "purovino", "status": "failed", "products_parsed": 0},
    ]
    probe.report_unsaved(results, applied=False)
    out = capsys.readouterr().out
    assert "lavinoterie" in out
    assert "nothing was saved" in out.lower()
    assert "apply" in out.lower(), "it must name the next step"


def test_nothing_to_say_when_nothing_parsed(capsys):
    probe.report_unsaved([{"shop": "x", "status": "failed", "products_parsed": 0}],
                         applied=False)
    assert capsys.readouterr().out == ""


def test_nothing_to_say_when_the_findings_were_applied(capsys):
    probe.report_unsaved([{"shop": "x", "status": "ok", "products_parsed": 9}],
                         applied=True)
    assert capsys.readouterr().out == ""


# --- the budget must not be spent guessing ------------------------------------

def test_catalogue_guessing_is_capped():
    """One live probe spent 24 requests on a single shop, all 404, and 107 of
    150 across eight shops. With eleven unverified shops that budget binds and
    the last shops go unprobed -- silently, which is the whole problem."""
    shop = {"name": "s", "url": "https://s.test", "platform": "html"}
    endpoints = probe.candidate_endpoints(shop)
    html_attempts = [e for e in endpoints if e[0] == "html"]
    assert len(html_attempts) <= probe.MAX_CATALOGUE_GUESSES
    assert len(endpoints) - len(html_attempts) == 2, "both API endpoints still tried"


def test_the_api_endpoints_come_first():
    shop = {"name": "s", "url": "https://s.test", "platform": "html"}
    platforms = [e[0] for e in probe.candidate_endpoints(shop)]
    assert platforms[:2] == ["shopify", "woocommerce"]


def test_a_recorded_catalogue_path_is_tried_first_among_the_guesses():
    shop = {"name": "s", "url": "https://s.test", "platform": "html",
            "catalog_path": "domaines.php"}
    html_urls = [e[1] for e in probe.candidate_endpoints(shop) if e[0] == "html"]
    assert html_urls[0].endswith("domaines.php")


# --- the richest catalogue wins, and the menu is part of the search ------------
#
# winenot verified with 3 products, vinnouveau with 8, pangee with exactly 12.
# The threshold was `len(items) < BETTER_CATALOGUE_AT` with BETTER_CATALOGUE_AT
# = 12, so pangee's twelve-product shop window was accepted as a catalogue by
# one product -- and for the other two the probe kept looking but had only a
# fixed list of guessed paths to look through.

LANDING = """
<html><body>
  <nav><a href="/la-cave">La cave</a></nav>
  <div class="grid">
    <div><a href="/p/1">Featured One</a><span>20,00 &euro;</span></div>
    <div><a href="/p/2">Featured Two</a><span>25,00 &euro;</span></div>
    <div><a href="/p/3">Featured Three</a><span>30,00 &euro;</span></div>
  </div>
</body></html>
"""


def catalogue_page(count):
    cells = "".join(
        f'<div><a href="/p/c{i}">Ganevat Cuvee {i}</a><span>{40 + i},00 &euro;</span></div>'
        for i in range(count)
    )
    return f'<html><body><div class="grid">{cells}</div></body></html>'


class MapCrawler:
    """Serves a body per exact URL, and 404s anything else -- so what the
    probe chose to fetch is visible in `self.asked`."""

    def __init__(self, pages):
        self.pages = pages
        self.asked = []
        self.max_requests = 200
        self.request_count = 0
        self.skipped_disallowed = []

    def get(self, url, params=None):
        self.request_count += 1
        self.asked.append(url)
        if url in self.pages:
            return crawler.FetchResult(200, self.pages[url])
        # status_code matters: a 404 means "not that path", while None means
        # the host never answered and the probe rightly gives up on it.
        raise crawler.UpstreamError("HTTP 404", status_code=404)


def test_the_probe_follows_the_menu_to_the_real_catalogue():
    shop = {"name": "winenot", "platform": "html", "url": "https://winenot.test",
            "verified": False}
    client = MapCrawler({
        "https://winenot.test": LANDING,
        "https://winenot.test/la-cave": catalogue_page(40),
    })

    result = probe.probe_shop(shop, client)

    assert result["status"] == "ok"
    assert result["products_parsed"] == 40, "the shop window beat the catalogue"
    assert result["catalog_path"] == "la-cave"
    assert "https://winenot.test/la-cave" in client.asked


def test_a_twelve_product_shop_window_does_not_win_on_a_boundary():
    """pangee's landing page parsed exactly twelve, which the old threshold
    read as a catalogue."""
    shop = {"name": "pangee", "platform": "html", "url": "https://pangee.test",
            "verified": False}
    client = MapCrawler({
        "https://pangee.test": catalogue_page(12).replace(
            "</body>", '<a href="/boutique">Boutique</a></body>'),
        "https://pangee.test/boutique": catalogue_page(30),
    })

    result = probe.probe_shop(shop, client)

    assert result["products_parsed"] == 30
    assert result["catalog_path"] == "boutique"


def test_a_thin_page_is_still_better_than_nothing():
    """When the menu leads nowhere, six real products still catch a producer."""
    shop = {"name": "thin", "platform": "html", "url": "https://thin.test",
            "verified": False}
    client = MapCrawler({"https://thin.test": LANDING})

    result = probe.probe_shop(shop, client)

    assert result["status"] == "ok"
    assert result["products_parsed"] == 3
    assert result.get("thin") is True


def test_a_clear_catalogue_is_accepted_without_exhausting_the_guesses():
    """Politeness costs 3s a request, so a page that is plainly the catalogue
    ends the search."""
    shop = {"name": "big", "platform": "html", "url": "https://big.test",
            "verified": False}
    client = MapCrawler({"https://big.test": catalogue_page(60)})

    result = probe.probe_shop(shop, client)

    assert result["products_parsed"] == 60
    assert len(client.asked) <= 4, f"kept guessing after finding a catalogue: {client.asked}"
