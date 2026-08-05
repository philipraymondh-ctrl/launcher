"""Making silent failure loud.

Everything this scraper gets wrong, it has gotten wrong quietly. A shop
that stops parsing prints the same line as a shop with nothing in stock. A
misattributed alias produces a row that looks exactly like a correct one. A
producer whose alias breaks simply stops appearing. And the request budget
is spent in list order, so if it ever binds it is always the same shops
that go unfetched.

These tests cover the four signals that turn each of those from invisible
into stated.
"""
import datetime as dt
import json

import pytest

import crawler
import market
import notify
import scraper

from canned_shop import FakeCrawler, PRODUCERS, SHOPS, product, shopify, woo
from test_end_to_end import BASIC


# --- A. adapter drift ---------------------------------------------------------

def test_check_shop_reports_how_many_products_it_parsed():
    """The count is the drift signal: a verified shop's fixture always
    parses to more than zero, so zero from a live fetch means the adapter
    has stopped working."""
    shop = SHOPS[0]
    client = FakeCrawler(BASIC)
    result = scraper.check_shop(shop, client)
    assert result.products_parsed == 1


def test_the_product_count_is_not_the_hit_count():
    """A shop can serve plenty and stock nobody we watch. That is healthy;
    zero products is not."""
    shop = SHOPS[0]
    payload = shopify([product("Someone Else Chardonnay", 20),
                       product("Another Grower Savagnin", 30)])
    result = scraper.check_shop(shop, FakeCrawler({"https://shopify.test": payload}))
    assert result.products_parsed == 2
    assert len(result) == 0


def test_check_shop_still_behaves_as_a_plain_list():
    """15 call sites assert on this value directly; it must stay a list."""
    shop = SHOPS[0]
    empty = scraper.check_shop(
        shop, FakeCrawler({"https://shopify.test": shopify([])}))
    assert empty == []
    assert isinstance(empty, list)
    assert len(empty) == 0


def test_a_shop_that_parses_nothing_is_named_as_drift(pipeline, capsys):
    bodies = dict(BASIC)
    bodies["https://shopify.test"] = shopify([])
    pipeline(bodies)
    out = capsys.readouterr().out
    assert "DRIFT" in out
    assert "zzz-shopify" in out.split("DRIFT", 1)[1]


def test_drift_reaches_the_digest_not_only_the_log(pipeline):
    bodies = dict(BASIC)
    bodies["https://shopify.test"] = shopify([])
    pipeline(bodies)
    body = pipeline.sent[-1]
    assert "returned nothing" in body
    assert "zzz-shopify" in body


def test_a_healthy_run_says_nothing_about_drift(pipeline):
    pipeline(BASIC)
    body = pipeline.sent[-1]
    assert "returned nothing" not in body
    assert "found nowhere" not in body


def test_drift_does_not_fail_the_run(pipeline):
    """A shop can be legitimately empty for a night. A red run every hour
    teaches you to ignore red runs."""
    bodies = dict(BASIC)
    bodies["https://shopify.test"] = shopify([])
    pipeline(bodies)   # must not raise


# --- B. the digest says which alias fired -------------------------------------

def test_matched_aliases_exposes_what_actually_matched():
    aliases = scraper.matched_aliases("Poulprix 2024 - Anne et Jean-Francois Ganevat")
    assert aliases == {"Ganevat": "ganevat"}


def test_matched_aliases_keeps_the_longest_alias_rule():
    """The rule that stops one estate being reported as another must hold
    here too, or the two functions disagree."""
    aliases = scraper.matched_aliases("Bruyere Houillon Savagnin 2020")
    assert list(aliases) == ["Bruyere Houillon"]
    assert aliases["Bruyere Houillon"] == "bruyere houillon"


def test_match_producers_is_unchanged_by_the_new_helper():
    assert scraper.match_producers("Domaine GANEVAT Chardonnay") == ["Ganevat"]
    assert scraper.match_producers("nothing here") == []


def test_a_hit_carries_the_alias_that_found_it(monkeypatch):
    monkeypatch.setattr(scraper, "PRODUCERS", PRODUCERS)
    shop = SHOPS[0]
    hits = scraper.check_shop(shop, FakeCrawler(BASIC))
    assert hits[0]["matched_alias"] == "zzz domaine"


def test_the_digest_row_shows_the_alias_so_a_bad_one_is_obvious():
    """Three misattributions were caught by eye. The row should carry its
    own diagnosis instead of needing the shop opened."""
    row = notify.format_row({
        "producer": "Overnoy/Houillon", "matched_alias": "houillon",
        "cuvee": "Savagnin 2020", "price": 30.0, "classification": "DEAL",
        "url": "https://x.test/1",
    })
    assert "Overnoy/Houillon [houillon]" in row


def test_a_row_without_an_alias_still_renders():
    row = notify.format_row({"producer": "Ganevat", "cuvee": "X", "price": 1.0})
    assert "Ganevat" in row
    assert "[" not in row.split("|")[1]


# --- C. producers found nowhere ------------------------------------------------

def test_a_producer_seen_at_no_shop_is_named_in_the_digest(pipeline):
    """The cheapest detector for a broken alias: a typo makes a producer
    vanish from every shop at once."""
    bodies = dict(BASIC)
    bodies["https://woo.test"] = woo([])          # drops Zzz Negoce entirely
    pipeline(bodies)
    body = pipeline.sent[-1]
    assert "found nowhere" in body
    assert "Zzz Negoce" in body.split("found nowhere", 1)[1]


def test_producers_that_were_found_are_not_listed_as_missing(pipeline):
    bodies = dict(BASIC)
    bodies["https://woo.test"] = woo([])
    pipeline(bodies)
    missing = pipeline.sent[-1].split("found nowhere", 1)[1]
    assert "Zzz Domaine" not in missing
    assert "Zzz Other" not in missing


# --- D. the budget must not always starve the same shops ----------------------

def hour(h):
    return dt.datetime(2026, 7, 28, h, 0, tzinfo=dt.timezone.utc)


def test_shop_order_rotates_with_the_hour():
    """Relative, not absolute: the order depends on which hour it is, and
    pinning "hour 0 is the identity" only ever held by arithmetic luck."""
    at_0 = [s["name"] for s in scraper.shop_order(SHOPS, now=hour(0))]
    at_1 = [s["name"] for s in scraper.shop_order(SHOPS, now=hour(1))]
    assert at_1 != at_0
    assert at_1[0] == at_0[1]
    assert sorted(at_1) == sorted(at_0)


def test_rotation_never_drops_or_duplicates_a_shop():
    for h in range(24):
        rotated = scraper.shop_order(SHOPS, now=hour(h))
        assert sorted(s["name"] for s in rotated) == sorted(s["name"] for s in SHOPS)


def test_every_shop_gets_the_first_slot_within_a_day():
    firsts = {scraper.shop_order(SHOPS, now=hour(h))[0]["name"] for h in range(24)}
    assert firsts == {s["name"] for s in SHOPS}


def test_every_real_shop_can_lead_a_run():
    """Against the real SHOPS, not the three canned ones -- which is why the
    test above passed while five shops could never lead at all. `hour % 29`
    only ever produced 24 of the 29 offsets, so biowijnclub, puurwijnshop,
    purovino, lavinoterie and pangee sat permanently behind the others.

    Asserts the rule (every shop leads within len(SHOPS) hours), not today's
    roster, so adding a shop cannot turn this into a spurious failure."""
    real = scraper.SHOPS
    start = dt.datetime(2026, 8, 5, 0, 0, tzinfo=dt.timezone.utc)
    firsts = {
        scraper.shop_order(real, now=start + dt.timedelta(hours=h))[0]["name"]
        for h in range(len(real))
    }
    assert firsts == {s["name"] for s in real}


def test_every_category_of_a_split_catalogue_gets_read_eventually():
    """One page budget is shared across every catalog_paths entry, so a fixed
    order spends all of it on the first few. winenot read 233 products over
    20 pages and never once reached rose, effervescent, moelleux or mute --
    not at this budget, and not at any budget, because the order never moved."""
    shop = {"name": "many", "url": "https://shop.test",
            "catalog_paths": ["a", "b", "c", "d", "e", "f"]}
    start = dt.datetime(2026, 8, 5, 0, 0, tzinfo=dt.timezone.utc)
    led = {scraper.catalogue_starts(shop, now=start + dt.timedelta(hours=h))[0]
           for h in range(len(shop["catalog_paths"]))}
    assert led == {f"https://shop.test/{p}" for p in shop["catalog_paths"]}


def test_rotating_the_categories_never_drops_one():
    shop = {"name": "many", "url": "https://shop.test",
            "catalog_paths": ["a", "b", "c"]}
    for h in range(6):
        at = dt.datetime(2026, 8, 5, 0, 0, tzinfo=dt.timezone.utc) + dt.timedelta(hours=h)
        assert sorted(scraper.catalogue_starts(shop, now=at)) == [
            "https://shop.test/a", "https://shop.test/b", "https://shop.test/c"]


def test_a_single_catalogue_path_is_not_rotated():
    shop = {"name": "one", "url": "https://shop.test", "catalog_path": "vins"}
    assert scraper.catalogue_starts(shop) == ["https://shop.test/vins"]


def test_a_single_shop_list_is_left_alone():
    one = [SHOPS[0]]
    assert scraper.shop_order(one, now=hour(7)) == one
    assert scraper.shop_order([], now=hour(7)) == []


def test_the_budget_message_names_the_shops_actually_skipped(pipeline, capsys):
    """After rotation the unfetched shops are no longer a suffix of SHOPS,
    so a message built by slicing the original list would name the wrong
    ones."""
    pipeline(BASIC, max_requests=1)
    out = capsys.readouterr().out
    assert "not reached this run" in out
    # The message is a single line, and the coverage table further down names
    # every shop -- so slice the line, not the remainder of the output.
    named = out.split("not reached this run:", 1)[1].splitlines()[0]
    # The shop that was actually fetched must not be listed as unreached.
    fetched = [line for line in out.splitlines() if "] ok," in line]
    if fetched:
        first = fetched[0].split("]")[0].lstrip("[")
        assert first not in named


def test_a_shop_the_run_never_reached_still_gets_a_row(pipeline, capsys):
    """The coverage table is the one place that answers "did we look at all",
    so a shop that vanishes from it is the exact failure the table exists to
    expose. Reproduced before the fix: at max_requests=1 two of three shops
    disappeared from the table entirely."""
    pipeline(BASIC, max_requests=1, force=True)
    capsys.readouterr()
    table = pipeline.sent[-1].split("Shop coverage", 1)[1]
    for shop in SHOPS:
        if shop.get("verified", True):
            assert shop["name"] in table, f"{shop['name']} is missing from the table"
    assert "not reached" in table


def test_producers_are_not_blamed_for_shops_the_run_never_opened(pipeline):
    """"Watched but found nowhere" means an alias that matches nothing, and
    can only mean that when every shop was read. With the budget binding, the
    same list also named producers whose shop was never opened -- turning a
    cut-off into an accusation against the aliases."""
    pipeline(BASIC, max_requests=1, force=True)
    body = pipeline.sent[-1]
    assert "Shops not reached this run" in body
    assert "Watched but found nowhere\n" not in body
    assert "found nowhere in the" in body, \
        "the note must say how many shops it is speaking for"


# --- E. running out of wall clock, cleanly ------------------------------------
#
# A cold-cache run of 22 shops took 8m38s against a 10-minute job timeout
# (run 30930725045). Politeness is the cost -- 3s+ per host, per request -- so
# the margin shrinks with every shop added. A job killed at the ceiling loses
# the whole crawl: no hits.json, no email, a red run and no explanation. The
# request budget already knows how to stop cleanly and name what it missed;
# the clock has to do the same.

class JumpingClock:
    """Every reading is a minute later than the last, so "out of time" is a
    fact rather than a race against the test runner."""

    def __init__(self, step=60):
        self.step, self.now = step, 0.0

    def __call__(self):
        self.now += self.step
        return self.now


def test_a_run_that_is_out_of_time_stops_cleanly(pipeline, capsys, monkeypatch):
    monkeypatch.setattr(scraper.time, "monotonic", JumpingClock())
    pipeline(BASIC, max_run_seconds=30)
    out = capsys.readouterr().out
    assert "out of time" in out.lower()
    assert "not reached this run" in out


def test_running_out_of_time_still_reports_what_was_found(pipeline):
    """Whatever was gathered before the clock ran out is still a real find,
    and must still reach the inbox."""
    pipeline(BASIC)                                   # everything, normally
    assert len(pipeline.sent) == 1
    hits = json.loads((pipeline.tmp / "hits.json").read_text())
    assert hits, "the baseline run found nothing, so this proves nothing"


def test_a_run_that_is_out_of_time_writes_hits_json(pipeline, monkeypatch):
    monkeypatch.setattr(scraper.time, "monotonic", JumpingClock())
    pipeline(BASIC, max_run_seconds=30)
    assert (pipeline.tmp / "hits.json").exists()


def test_a_normal_run_is_not_cut_short(pipeline, capsys):
    pipeline(BASIC)
    assert "out of time" not in capsys.readouterr().out.lower()


def test_no_limit_means_no_limit(pipeline, capsys):
    pipeline(BASIC, max_run_seconds=0)
    assert "out of time" not in capsys.readouterr().out.lower()
