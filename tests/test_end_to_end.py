"""One run of the whole pipeline, on canned responses.

Every stage here is unit-tested elsewhere. What is not covered anywhere is
the seam between them, and every bug found in this project so far has
lived in a seam: a fetcher that never set `in_stock` while the stock check
worked perfectly; a helper used correctly in one caller and indexed
directly in another; an index route handed the wrong URL. So this module
runs `scraper.main()` end to end and asserts what comes out the far end.

Nothing here touches the network -- `crawler.Crawler` is replaced, not fed
-- and nothing writes into the repo: seen.json, hits.json and
observations.json are all redirected into tmp_path.
"""
import json

import pytest

import crawler
import market
import notify
import scraper


# --- the canned shop ----------------------------------------------------------

def shopify(products):
    return json.dumps({"products": products})


def product(title, price, available=True, vendor=""):
    return {
        "title": title, "vendor": vendor, "handle": title.lower().replace(" ", "-"),
        "body_html": "", "variants": [{"title": "Default", "price": str(price),
                                       "available": available}],
    }


def woo(products):
    return json.dumps(products)


def woo_product(name, price, in_stock=True):
    return {
        "name": name, "permalink": f"https://woo.test/p/{name.lower().replace(' ', '-')}",
        "prices": {"price": str(int(price * 100)), "currency_minor_unit": 2},
        "is_in_stock": in_stock,
    }


def html_listing(rows):
    cells = "".join(
        f'<div><a href="/p/{n}">{title}</a><span>{price},00 &euro;</span>'
        f'{"<em>Produit épuisé</em>" if sold_out else ""}</div>'
        for n, (title, price, sold_out) in enumerate(rows)
    )
    return f'<html><body><div class="grid">{cells}</div></body></html>'


SHOPS = [
    {"name": "zzz-shopify", "platform": "shopify", "url": "https://shopify.test",
     "verified": True},
    {"name": "zzz-woo", "platform": "woocommerce", "url": "https://woo.test",
     "verified": True},
    {"name": "zzz-html", "platform": "html", "url": "https://html.test",
     "item_selector": "div.product", "title_selector": "h2.product-title",
     "price_selector": "span.price", "verified": True},
    {"name": "zzz-dark", "platform": "shopify", "url": "https://dark.test",
     "verified": False},
]

PRODUCERS = {
    "Zzz Domaine": ["zzz domaine"],
    "Zzz Negoce": ["zzz negoce"],
    "Zzz Other": ["zzz other"],
}


class FakeCrawler:
    """Serves canned bodies by URL prefix and counts requests, standing in
    for the real Crawler that main() constructs for itself."""

    def __init__(self, bodies, max_requests=1000, fail_hosts=()):
        self.bodies = bodies
        self.max_requests = max_requests
        self.request_count = 0
        self.fail_hosts = set(fail_hosts)
        self.urls = []

    def get(self, url, params=None):
        self.request_count += 1
        self.urls.append(url)
        if self.request_count > self.max_requests:
            raise crawler.BudgetExceeded(url)
        for host in self.fail_hosts:
            if host in url:
                raise crawler.UpstreamError("Connection refused")
        page = int((params or {}).get("page", 1))
        for prefix, body in self.bodies.items():
            if url.startswith(prefix):
                if page > 1:
                    return crawler.FetchResult(
                        200, '{"products": []}' if "shopify" in prefix
                        else "[]" if "woo" in prefix else "")
                return crawler.FetchResult(200, body)
        return crawler.FetchResult(200, "")


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    """Runs main() with everything redirected away from the repo."""
    sent = []

    monkeypatch.setattr(scraper, "SHOPS", SHOPS)
    monkeypatch.setattr(scraper, "PRODUCERS", PRODUCERS)
    monkeypatch.setattr(notify, "STATE_PATH", tmp_path / "seen.json")
    monkeypatch.setattr(notify, "HITS_PATH", tmp_path / "hits.json")
    monkeypatch.setattr(market, "OBSERVATIONS_PATH", tmp_path / "observations.json")
    # The real send path must run so state is persisted; only SMTP is stubbed.
    monkeypatch.setattr(notify, "send_email", lambda body: sent.append(body))

    def run(bodies, dry_run=False, max_requests=1000, fail_hosts=()):
        client = FakeCrawler(bodies, max_requests=max_requests, fail_hosts=fail_hosts)
        monkeypatch.setattr(crawler, "Crawler", lambda *a, **k: client)
        monkeypatch.setattr(scraper, "DRY_RUN", dry_run)
        scraper.main()
        return client

    run.sent = sent
    run.tmp = tmp_path
    return run


def digest(pipeline):
    assert pipeline.sent, "no digest was sent"
    return pipeline.sent[-1]


def hits_json(pipeline):
    return json.loads((pipeline.tmp / "hits.json").read_text())


# --- 2. the happy path ---------------------------------------------------------

BASIC = {
    "https://shopify.test": shopify([product("Zzz Domaine Chardonnay 2020", 60)]),
    "https://woo.test": woo([woo_product("Zzz Negoce Poulsard 2022", 25)]),
    "https://html.test": html_listing([
        ("Zzz Other Savagnin 2019", 40, False),
        ("Zzz Other Trousseau 2021", 35, False),
        ("Zzz Other Ploussard 2020", 30, False),
    ]),
}


def test_a_run_reaches_every_platform_and_emails_once(pipeline):
    pipeline(BASIC)
    body = digest(pipeline)
    assert len(pipeline.sent) == 1, "one digest per run, never one per hit"
    assert "STATUS | Producer | Cuvee | Size | Price | Ref | Basis | Link" in body
    for producer in ("Zzz Domaine", "Zzz Negoce", "Zzz Other"):
        assert producer in body, f"{producer} reached no digest row"


def test_every_hit_is_written_to_hits_json(pipeline):
    pipeline(BASIC)
    hits = hits_json(pipeline)
    assert len(hits) == 5
    assert all(h["classification"] in {"DEAL", "FAIR", "HIGH", "NOREF"} for h in hits)


def test_an_unverified_shop_is_never_fetched(pipeline):
    client = pipeline(BASIC)
    assert not any("dark.test" in u for u in client.urls)


# --- 3. stock ------------------------------------------------------------------

SOLD_OUT = {
    "https://shopify.test": shopify([
        product("Zzz Domaine Chardonnay 2020", 60, available=False),
        product("Zzz Domaine Savagnin 2020", 70, available=True),
    ]),
    "https://woo.test": woo([
        woo_product("Zzz Negoce Poulsard 2022", 25, in_stock=False),
    ]),
    "https://html.test": html_listing([
        ("Zzz Other Savagnin 2019", 40, True),
        ("Zzz Other Trousseau 2021", 35, True),
        ("Zzz Other Ploussard 2020", 30, True),
    ]),
}


def test_nothing_sold_out_reaches_the_digest(pipeline):
    pipeline(SOLD_OUT)
    body = digest(pipeline)
    assert "Savagnin 2020" in body, "the in-stock bottle should be reported"
    assert "Chardonnay 2020" not in body, "sold-out Shopify variant alerted"
    assert "Poulsard 2022" not in body, "out-of-stock WooCommerce product alerted"
    assert "Zzz Other" not in body, "listing marked epuise alerted"


def test_sold_out_listings_are_still_parsed(pipeline):
    """The probe counts parsed products to decide whether an adapter works,
    so a shop whose stock is out today must not read as broken."""
    shop = SHOPS[2]
    client = FakeCrawler(SOLD_OUT)
    assert len(scraper.FETCHERS["html"](shop, client)) == 3


# --- 4. market pricing across shops --------------------------------------------

TWO_SHOPS_SAME_WINE = {
    "https://shopify.test": shopify([product("Zzz Domaine Chardonnay 2020", 50)]),
    "https://woo.test": woo([woo_product("Zzz Domaine Chardonnay 2020", 100)]),
    "https://html.test": html_listing([("Zzz Other Filler", 20, False)]),
}


def test_the_same_wine_at_two_shops_prices_itself(pipeline):
    pipeline(TWO_SHOPS_SAME_WINE)
    hits = {h["shop"]: h for h in hits_json(pipeline) if h["producer"] == "Zzz Domaine"}
    cheap = hits["zzz-shopify"]
    assert cheap["classification"] == "DEAL"
    assert cheap["reference_basis"] and "same wine" in cheap["reference_basis"]
    assert "zzz-woo" in cheap["reference_shops"]


def test_observations_survive_to_the_next_run(pipeline):
    pipeline(TWO_SHOPS_SAME_WINE)
    store = json.loads((pipeline.tmp / "observations.json").read_text())
    assert store["records"], "nothing was recorded for the next run to use"


# --- 5. negoce vs domaine ------------------------------------------------------

NEGOCE_AND_DOMAINE = {
    "https://shopify.test": shopify([
        product("Chardonnay 2020 - Zzz Domaine", 200),
        product("Poulsard 2022 - Zzz Negoce", 25),
    ]),
    "https://woo.test": woo([
        woo_product("Chardonnay 2020 - Zzz Domaine", 210),
        woo_product("Poulsard 2022 - Zzz Negoce", 27),
    ]),
    "https://html.test": html_listing([("Zzz Other Filler", 20, False)]),
}


def test_a_cheap_line_is_not_scored_against_an_expensive_one(pipeline):
    """One reference per producer made a EUR 25 bottle a permanent DEAL and
    a EUR 200 bottle a permanent HIGH. Neither should happen here."""
    pipeline(NEGOCE_AND_DOMAINE)
    by_key = {(h["producer"], h["shop"]): h for h in hits_json(pipeline)}
    negoce = by_key[("Zzz Negoce", "zzz-shopify")]
    domaine = by_key[("Zzz Domaine", "zzz-shopify")]
    assert negoce["classification"] != "DEAL", "cheap line read as a bargain"
    assert domaine["classification"] != "HIGH", "expensive line read as overpriced"


# --- 6. cooldown ---------------------------------------------------------------

def test_the_same_finds_are_not_emailed_twice(pipeline):
    pipeline(BASIC)
    assert len(pipeline.sent) == 1
    pipeline(BASIC)
    assert len(pipeline.sent) == 1, "a second run re-alerted on unchanged hits"


def test_a_price_drop_inside_the_cooldown_does_not_re_alert(pipeline):
    """Documented, deliberate behaviour: the 30-day cooldown wins over a
    price drop -- see test_notify.py. Asserted here so the end-to-end path
    agrees with the unit-level decision rather than quietly diverging.

    Worth knowing it means a genuine 50% drop stays silent for up to a
    month; changing that is a product decision, not a bug fix."""
    pipeline(BASIC)
    cheaper = dict(BASIC)
    cheaper["https://shopify.test"] = shopify(
        [product("Zzz Domaine Chardonnay 2020", 30)])
    pipeline(cheaper)
    assert len(pipeline.sent) == 1


def test_a_price_drop_alerts_once_the_cooldown_has_elapsed(pipeline):
    import datetime as dt

    pipeline(BASIC)
    # Age every alert past the cooldown, as a month of hourly runs would.
    state_path = pipeline.tmp / "seen.json"
    state = json.loads(state_path.read_text())
    long_ago = (dt.datetime.now(dt.timezone.utc)
                - dt.timedelta(days=notify.COOLDOWN_DAYS + 1)).isoformat()
    for entry in state.values():
        entry["last_alerted_at"] = long_ago
    state_path.write_text(json.dumps(state))

    cheaper = dict(BASIC)
    cheaper["https://shopify.test"] = shopify(
        [product("Zzz Domaine Chardonnay 2020", 30)])
    pipeline(cheaper)
    assert len(pipeline.sent) == 2, "a >10% drop past the cooldown must re-alert"


# --- 7. a dry run leaves no trace ----------------------------------------------

def test_a_dry_run_sends_nothing_and_persists_nothing(pipeline):
    pipeline(BASIC, dry_run=True)
    assert pipeline.sent == [], "a dry run sent an email"
    assert not (pipeline.tmp / "seen.json").exists(), "a dry run consumed the cooldown"
    assert not (pipeline.tmp / "observations.json").exists(), "a dry run wrote observations"


def test_a_dry_run_does_not_silence_the_next_real_run(pipeline):
    pipeline(BASIC, dry_run=True)
    pipeline(BASIC)
    assert len(pipeline.sent) == 1, "the real run found nothing left to say"


# --- 8. one shop failing must not take the run down ----------------------------

def test_an_unreachable_shop_does_not_stop_the_others(pipeline):
    pipeline(BASIC, fail_hosts=("woo.test",))
    body = digest(pipeline)
    assert "Zzz Domaine" in body
    assert "Zzz Other" in body
    assert "Zzz Negoce" not in body


# --- 9. the request budget -----------------------------------------------------

def test_the_run_stops_cleanly_when_the_budget_runs_out(pipeline, capsys):
    pipeline(BASIC, max_requests=1)
    out = capsys.readouterr().out
    assert "MAX_REQUESTS_PER_RUN" in out
    assert "not reached this run" in out


# --- 10. a silent run is a valid run -------------------------------------------

def test_a_run_with_no_hits_sends_nothing_and_does_not_crash(pipeline):
    pipeline({
        "https://shopify.test": shopify([product("Someone Else Chardonnay", 20)]),
        "https://woo.test": woo([]),
        "https://html.test": "<html><body><p>rien</p></body></html>",
    })
    assert pipeline.sent == []
    assert hits_json(pipeline) == []
