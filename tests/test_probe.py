import json
from pathlib import Path

import pytest

import autoselect
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
    """It goes early, but the rest of the list must still be tried -- short
    -circuiting here meant the shop catalogue discovery was written for only
    ever tried its one guessed path. The landing page precedes it, because
    that is where the shop's own menu lives."""
    shop = dict(a_shop(), catalog_path="vins.php")
    urls = [url for _, url, _, _ in probe.candidate_endpoints(shop)]
    html_urls = [u for u in urls if "products.json" not in u and "wp-json" not in u]
    assert html_urls[1].endswith("/vins.php")
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


def test_two_answers_from_one_path_are_kept_separately(monkeypatch, tmp_path):
    """/webshop and /webshop?format=json are the two halves of a Squarespace
    diagnosis -- the HTML you can see and the JSON you want instead. Slugging
    only the path filed both under "webshop", so the second silently replaced
    the first: the same overwrite the host was added to the name to stop."""
    monkeypatch.setattr(probe, "DIAGNOSTIC_DIR", tmp_path)
    probe.save_diagnostic_page("zzzshop", "https://zzz.example/webshop", "<html></html>")
    probe.save_diagnostic_page("zzzshop", "https://zzz.example/webshop?format=json",
                               '{"items": []}')
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == [
        "zzzshop.zzz-example.webshop-format-json.json",
        "zzzshop.zzz-example.webshop.html",
    ]


def test_a_json_body_is_saved_as_it_arrived(monkeypatch, tmp_path):
    """Put through an HTML parser a JSON body comes back as one text node with
    its punctuation re-escaped -- unreadable, and unusable as a fixture."""
    monkeypatch.setattr(probe, "DIAGNOSTIC_DIR", tmp_path)
    payload = '{"items": [{"title": "Vin jaune", "price": "129,00 €"}]}'
    probe.save_diagnostic_page("zzzshop", "https://zzz.example/c?format=json", payload)
    saved = next(p for p in tmp_path.iterdir() if p.is_file())
    assert saved.suffix == ".json"
    assert json.loads(saved.read_text())["items"][0]["title"] == "Vin jaune"


def test_a_script_that_carries_the_prices_is_kept(monkeypatch, tmp_path):
    """purovino's capture recorded "4 currency-adjacent prices" in its header
    and then held not one currency marker: a Squarespace commerce page renders
    its prices from Static.SQUARESPACE_CONTEXT, and stripping every script
    threw away the only evidence a parser could be written from."""
    monkeypatch.setattr(probe, "DIAGNOSTIC_DIR", tmp_path)
    body = (
        "<html><head>"
        "<script>Static.SQUARESPACE_CONTEXT = {\"price\": \"20,00 €\"};</script>"
        "<script type=\"application/ld+json\">{\"@type\": \"Product\"}</script>"
        "<script src='https://cdn.example/behaviour.js'></script>"
        "<script>window.addEventListener('load', spin);</script>"
        "</head><body><p>rien</p></body></html>"
    )
    probe.save_diagnostic_page("zzzshop", "https://zzz.example/webshop", body)
    kept = next(p for p in tmp_path.iterdir() if p.is_file()).read_text()
    assert "SQUARESPACE_CONTEXT" in kept and "20,00" in kept
    assert '"@type": "Product"' in kept
    assert "behaviour.js" not in kept and "addEventListener" not in kept


def test_a_kept_data_script_cannot_blow_up_the_capture(monkeypatch, tmp_path):
    """The point of stripping scripts is that they are most of the bytes."""
    monkeypatch.setattr(probe, "DIAGNOSTIC_DIR", tmp_path)
    huge = "SQUARESPACE_CONTEXT = " + "x" * (probe.DATA_SCRIPT_CAP * 3)
    probe.save_diagnostic_page("zzzshop", "https://zzz.example/", f"<script>{huge}</script>")
    saved = next(p for p in tmp_path.iterdir() if p.is_file()).read_text()
    assert probe.DATA_SCRIPT_CAP <= len(saved) <= probe.DATA_SCRIPT_CAP + 2000


def test_one_page_reached_two_ways_is_recorded_once():
    """A probe run recorded vinovivo's catalogue as both "shop" and
    "http://vinovivo.be/shop": one page, two requests every run, and two
    different orders once catalogue_starts began rotating them."""
    shop = a_shop(name="dup", url="https://vinovivo.be")
    stub = StubCrawler({
        "/products.json": not_found(),
        "/wp-json/": not_found(),
        "https://vinovivo.be": crawler.FetchResult(200, (
            '<html><body><a href="http://vinovivo.be/shop">Shop</a>'
            '<a href="/shop/">Boutique</a></body></html>')),
        "/shop": crawler.FetchResult(200, (FIXTURES / "example-html-shop.html").read_text()),
    })
    result = probe.probe_shop(shop, stub)
    paths = result.get("catalog_paths") or []
    keys = {p.replace("http://", "https://").rstrip("/").rsplit("/", 1)[-1] for p in paths}
    assert len(paths) == len(keys), f"one catalogue recorded twice: {paths}"


def test_an_unverified_shop_is_reported_with_its_reason(monkeypatch, capsys):
    """"Left unverified: mifuguemiraisin" sent three separate attempts into a
    twelve-minute log hunting the one line that said why. The reason is
    already in the result; it belongs in the summary."""
    failed = {
        "shop": "naturavin", "status": "failed", "detected_platform": None,
        "products_parsed": 0, "producer_hits": [],
        "attempts": [{"url": "https://naturavin.example/shop",
                      "outcome": "no connection to the host"}],
    }
    monkeypatch.setattr(probe, "probe_shop", lambda shop, client: failed)
    monkeypatch.setattr(probe.crawler, "Crawler", lambda *a, **k: StubCrawler({}))
    monkeypatch.setattr(probe, "apply_results", lambda results: ([], ["naturavin"]))

    probe.main(["--only", "naturavin", "--apply"])

    out = capsys.readouterr().out
    tail = out.split("Left unverified", 1)[1]
    assert "naturavin" in tail
    assert "no connection to the host" in tail, \
        "the summary named the shop but not the reason"


def test_capturing_pages_does_not_cancel_the_probe(monkeypatch, tmp_path, capsys):
    """A run given both --capture and --only captured its pages and then
    returned, so the shops it named were never fetched -- and the commit step
    said "No shops changed state" while the run reported success."""
    monkeypatch.setattr(probe, "DIAGNOSTIC_DIR", tmp_path)
    probed = []

    class Recording(StubCrawler):
        def get(self, url, params=None):
            probed.append(url)
            return crawler.FetchResult(200, "<html><body><p>x</p></body></html>")

    monkeypatch.setattr(probe.crawler, "Crawler", lambda *a, **k: Recording({}))
    monkeypatch.setattr(probe, "probe_shop",
                        lambda shop, client: {"shop": shop["name"], "status": "failed",
                                              "detected_platform": None, "attempts": [],
                                              "products_parsed": 0, "producer_hits": []})

    probe.main(["--capture", "https://zzz.example/page", "--only", "naturavin"])

    out = capsys.readouterr().out
    assert "CAPTURE" in out
    assert "naturavin" in out, "the shop named in --only was never probed"


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


def test_a_recorded_catalogue_path_is_tried_right_after_the_landing_page():
    shop = {"name": "s", "url": "https://s.test", "platform": "html",
            "catalog_path": "domaines.php"}
    html_urls = [e[1] for e in probe.candidate_endpoints(shop) if e[0] == "html"]
    assert html_urls[0] == "https://s.test", "the menu page must come first"
    assert html_urls[1].endswith("domaines.php")


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


def test_a_paginating_catalogue_wins():
    """No HTML page ends the search any more -- every candidate is weighed and
    the best wins, because accepting one early is how a wrong recorded path
    kept confirming itself. The cost is bounded by the candidate list."""
    shop = {"name": "big", "platform": "html", "url": "https://big.test",
            "verified": False}
    client = MapCrawler({
        "https://big.test": paginated(60, "/?page=2"),
        "https://big.test/?page=2": paginated(60, "/?page=3"),
    })

    result = probe.probe_shop(shop, client)

    assert result["products_parsed"] >= 60
    assert not result.get("thin")
    assert len(client.asked) <= probe.MAX_CATALOGUE_GUESSES + autoselect.MAX_CATALOGUE_LINKS + 2


def test_a_single_page_catalogue_costs_the_whole_search():
    """The deliberate other side of that trade: 60 products on one page with
    no "next" cannot be told from a large shop window without looking, so the
    probe looks -- bounded by the candidate list, not unbounded."""
    shop = {"name": "big", "platform": "html", "url": "https://big.test",
            "verified": False}
    client = MapCrawler({"https://big.test": catalogue_page(60)})

    result = probe.probe_shop(shop, client)

    assert result["products_parsed"] == 60
    assert len(client.asked) <= probe.MAX_CATALOGUE_GUESSES + autoselect.MAX_CATALOGUE_LINKS + 2


# --- a paginating catalogue beats a fuller shop window ------------------------
#
# The first menu-following probe chose pangee's /nouveaux-produits (36
# products on one page) over its catalogue, and winenot's sparkling-wine
# filter (12) over nine region categories. Page-one product count is a poor
# proxy for catalogue size: a "new arrivals" strip is one page, a catalogue
# runs to twenty.

def paginated(count, next_url):
    cells = "".join(
        f'<div><a href="/p/x{i}">Ganevat Cuvee {i}</a><span>{40 + i},00 &euro;</span></div>'
        for i in range(count)
    )
    return (f'<html><body><div class="grid">{cells}</div>'
            f'<a rel="next" href="{next_url}">Suivant</a></body></html>')


def test_a_page_that_paginates_wins_over_a_bigger_one_that_does_not():
    shop = {"name": "pangee", "platform": "html", "url": "https://pangee.test",
            "verified": False}
    client = MapCrawler({
        "https://pangee.test": (
            '<html><body><nav>'
            '<a href="/nouveaux-produits">Nouveaux produits</a>'
            '<a href="/25-vins">Tous les vins</a>'
            '</nav></body></html>'),
        # More products on page one, but that is all there is.
        "https://pangee.test/nouveaux-produits": catalogue_page(36),
        # Fewer on page one, but it runs on.
        "https://pangee.test/25-vins": paginated(20, "/25-vins?page=2"),
        "https://pangee.test/25-vins?page=2": paginated(20, "/25-vins?page=3"),
    })

    result = probe.probe_shop(shop, client)

    assert result["catalog_path"] == "25-vins", (
        f"chose {result['catalog_path']} with {result['products_parsed']} products")


def test_a_winner_outside_the_base_path_is_still_recorded():
    """pangee's base URL is https://la-pangee.com/fr, and the page that won
    was /nouveaux-produits -- not under it. The recorded path came back None,
    so the config kept pointing at the landing page while the fixture showed
    the richer one: a shop whose fixture no longer describes what the run
    fetches."""
    shop = {"name": "pangee", "platform": "html",
            "url": "https://pangee.test/fr", "verified": False}
    client = MapCrawler({
        "https://pangee.test/fr": (
            '<html><body><nav><a href="/vins-tous">Tous les vins</a>'
            '</nav></body></html>'),
        "https://pangee.test/vins-tous": catalogue_page(40),
    })

    result = probe.probe_shop(shop, client)

    assert result["products_parsed"] == 40
    assert result["catalog_path"], "the winning page was not recorded at all"
    assert "vins-tous" in result["catalog_path"]


# --- a catalogue split across regions -----------------------------------------
#
# winenot.fr's menu (tests/fixtures/winenot.html, as captured) offers
# /12-alsace, /14-beaujolais, /16-bourgogne, /17-champagne, /19-jura,
# /20-languedoc, /21-loire, /23-rhone, /26-sud-ouest -- and no "all wines"
# page. Recording one of them reads one region; recording the sparkling-wine
# filter, which is what happened, reads neither.

def test_region_categories_are_recognised_as_catalogues():
    html = """<html><body>
      <a href="/12-alsace">Alsace</a>
      <a href="/19-jura">Jura</a>
      <a href="/21-loire">Loire</a>
      <a href="/mon-compte">Mon compte</a>
    </body></html>"""
    found = autoselect.find_catalogue_links(html, "https://winenot.test/")
    for region in ("12-alsace", "19-jura", "21-loire"):
        assert f"https://winenot.test/{region}" in found, region
    assert not any("compte" in u for u in found)


def test_several_categories_are_recorded_when_no_page_holds_them_all():
    shop = {"name": "winenot", "platform": "html", "url": "https://winenot.test",
            "verified": False}
    client = MapCrawler({
        "https://winenot.test": (
            '<html><body><nav>'
            '<a href="/19-jura">Jura</a><a href="/21-loire">Loire</a>'
            '<a href="/s/3/vin-effervescent">Vin effervescent</a>'
            '</nav></body></html>'),
        "https://winenot.test/19-jura": paginated(20, "/19-jura?page=2"),
        "https://winenot.test/19-jura?page=2": paginated(20, "/19-jura?page=3"),
        "https://winenot.test/21-loire": paginated(18, "/21-loire?page=2"),
        "https://winenot.test/21-loire?page=2": paginated(18, "/21-loire?page=3"),
        "https://winenot.test/s/3/vin-effervescent": catalogue_page(12),
    })

    result = probe.probe_shop(shop, client)

    assert result["status"] == "ok"
    paths = result.get("catalog_paths") or []
    assert "19-jura" in paths and "21-loire" in paths, paths
    assert not any("effervescent" in p for p in paths), \
        "a single-page filter was recorded as a catalogue"


def test_one_rich_catalogue_records_no_list():
    """A shop with a real "all wines" page needs one path, not a list."""
    shop = {"name": "one", "platform": "html", "url": "https://one.test",
            "verified": False}
    client = MapCrawler({
        "https://one.test": '<html><body><a href="/vins">Tous les vins</a></body></html>',
        "https://one.test/vins": paginated(40, "/vins?page=2"),
        "https://one.test/vins?page=2": paginated(40, "/vins?page=3"),
    })

    result = probe.probe_shop(shop, client)

    assert result["catalog_path"] == "vins"
    assert not result.get("catalog_paths")


# --- a recorded path must not short-circuit its own re-examination ------------
#
# Three probes in a row left winenot on s/3/vin-effervescent and pangee on
# /nouveaux-produits. The recorded path is tried first -- deliberately -- and
# a rich paginating page is accepted on the spot, so the landing page was
# never fetched and its menu never read. A wrong path perpetuated itself, and
# each new probe confirmed it.

def test_a_recorded_path_does_not_stop_the_menu_being_read():
    shop = {"name": "pangee", "platform": "html", "url": "https://pangee.test",
            "catalog_path": "nouveaux-produits", "verified": True}
    client = MapCrawler({
        "https://pangee.test": (
            '<html><body><nav><a href="/25-vins">Tous les vins</a></nav></body></html>'),
        # The recorded page: rich and paginating, so the old rule took it.
        "https://pangee.test/nouveaux-produits": paginated(36, "/nouveaux-produits?p=2"),
        "https://pangee.test/nouveaux-produits?p=2": paginated(36, "/nouveaux-produits?p=3"),
        # The real catalogue, richer still.
        "https://pangee.test/25-vins": paginated(48, "/25-vins?p=2"),
        "https://pangee.test/25-vins?p=2": paginated(48, "/25-vins?p=3"),
    })

    result = probe.probe_shop(shop, client)

    assert "https://pangee.test" in client.asked, "the landing page was never read"
    assert result["catalog_path"] == "25-vins", (
        f"kept {result['catalog_path']} without looking at the menu")


def test_a_recorded_path_still_wins_when_it_is_the_best():
    """Re-probing a correctly configured shop must not wander off."""
    shop = {"name": "good", "platform": "html", "url": "https://good.test",
            "catalog_path": "vins", "verified": True}
    client = MapCrawler({
        "https://good.test": '<html><body><nav><a href="/promotions">Promos</a></nav></body></html>',
        "https://good.test/vins": paginated(50, "/vins?p=2"),
        "https://good.test/vins?p=2": paginated(50, "/vins?p=3"),
        "https://good.test/promotions": catalogue_page(10),
    })

    result = probe.probe_shop(shop, client)

    assert result["catalog_path"] == "vins"
