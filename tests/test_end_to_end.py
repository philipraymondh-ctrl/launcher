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

import notify
import scraper

from canned_shop import (
    FakeCrawler, SHOPS, html_listing, product, shopify, woo, woo_product,
)


def digest(pipeline):
    assert pipeline.sent, "no digest was sent"
    return pipeline.sent[-1]


def rows(body):
    """Only the digest's product rows.

    The digest now also *names* shops that returned nothing and producers
    found nowhere, so asserting a producer is absent from the whole body
    catches those notes -- the opposite of what these tests mean. A row is
    the pipe-delimited kind."""
    return "\n".join(l for l in body.splitlines() if l.count(" | ") >= 4)


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
    listed = rows(digest(pipeline))
    assert "Savagnin 2020" in listed, "the in-stock bottle should be reported"
    assert "Chardonnay 2020" not in listed, "sold-out Shopify variant alerted"
    assert "Poulsard 2022" not in listed, "out-of-stock WooCommerce product alerted"
    assert "Zzz Other" not in listed, "listing marked epuise alerted"


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


def test_a_price_drop_inside_the_cooldown_re_alerts(pipeline):
    """The cooldown suppresses repetition, not news -- see test_notify.py for
    why that decision was reversed. Asserted here so the end-to-end path
    agrees with the unit-level rule rather than quietly diverging."""
    pipeline(BASIC)
    cheaper = dict(BASIC)
    cheaper["https://shopify.test"] = shopify(
        [product("Zzz Domaine Chardonnay 2020", 30)])
    pipeline(cheaper)
    assert len(pipeline.sent) == 2
    assert "Chardonnay 2020" in rows(pipeline.sent[-1])


def test_an_unchanged_shelf_stays_silent(pipeline):
    """The other half of the same rule: nothing new, nothing cheaper, no
    email -- three runs in a row."""
    pipeline(BASIC)
    pipeline(BASIC)
    pipeline(BASIC)
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
    listed = rows(digest(pipeline))
    assert "Zzz Domaine" in listed
    assert "Zzz Other" in listed
    assert "Zzz Negoce" not in listed


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


# --- 11. a week of correct silence still reaches the owner ----------------------

def test_a_week_without_news_still_produces_a_recap(pipeline):
    """A run that says nothing is right to say nothing, but from the inbox it
    looks exactly like expired credentials or a dead workflow. After a week,
    the run reports what it can currently see."""
    import datetime as dt

    pipeline(BASIC)
    assert len(pipeline.sent) == 1

    state_path = pipeline.tmp / "seen.json"
    state = json.loads(state_path.read_text())
    state[notify.META_KEY] = {"last_recap_at": (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=notify.RECAP_DAYS + 1)
    ).isoformat()}
    state_path.write_text(json.dumps(state))

    pipeline(BASIC)
    assert len(pipeline.sent) == 2
    assert pipeline.subjects[-1] == notify.RECAP_SUBJECT
    assert "Zzz Domaine" in rows(pipeline.sent[-1])


def test_the_recap_does_not_silence_a_later_real_find(pipeline):
    import datetime as dt

    pipeline(BASIC)
    state_path = pipeline.tmp / "seen.json"
    state = json.loads(state_path.read_text())
    state[notify.META_KEY] = {"last_recap_at": (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=notify.RECAP_DAYS + 1)
    ).isoformat()}
    state_path.write_text(json.dumps(state))
    pipeline(BASIC)                                   # recap

    cheaper = dict(BASIC)
    cheaper["https://shopify.test"] = shopify(
        [product("Zzz Domaine Chardonnay 2020", 20)])
    pipeline(cheaper)
    assert len(pipeline.sent) == 3
    assert pipeline.subjects[-1] == notify.DIGEST_SUBJECT


# --- 12. a run the owner started -----------------------------------------------

def test_a_hand_started_run_reports_even_when_nothing_is_new(pipeline):
    """Two button presses in a row found 51 matches and emailed nothing,
    which read as a dead scraper. The hourly schedule stays quiet; a run
    somebody asked for answers."""
    pipeline(BASIC)
    assert len(pipeline.sent) == 1

    pipeline(BASIC)                      # scheduled: nothing new, silent
    assert len(pipeline.sent) == 1

    pipeline(BASIC, force=True)          # the button
    assert len(pipeline.sent) == 2
    assert pipeline.subjects[-1] == notify.ONDEMAND_SUBJECT
    assert "Zzz Domaine" in rows(pipeline.sent[-1])


def test_a_hand_started_run_does_not_consume_the_cooldown(pipeline):
    """It must not silence a genuine drop that lands afterwards."""
    pipeline(BASIC)
    pipeline(BASIC, force=True)

    cheaper = dict(BASIC)
    cheaper["https://shopify.test"] = shopify(
        [product("Zzz Domaine Chardonnay 2020", 30)])
    pipeline(cheaper)

    assert len(pipeline.sent) == 3
    assert pipeline.subjects[-1] == notify.DIGEST_SUBJECT


def test_a_hand_started_run_with_nothing_at_all_still_answers(pipeline):
    pipeline({
        "https://shopify.test": shopify([product("Someone Else Chardonnay", 20)]),
        "https://woo.test": woo([]),
        "https://html.test": "<html><body><p>rien</p></body></html>",
    }, force=True)
    assert len(pipeline.sent) == 1
    assert "found nowhere" in pipeline.sent[-1]
