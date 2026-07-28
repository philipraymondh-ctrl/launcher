"""Telling "your alias is broken" apart from "the wine is gone".

`check_shop` drops sold-out listings before `matched_aliases` ever runs, so
the "Watched but found nowhere" note -- added specifically to make a broken
alias visible -- said the same thing for a producer that is stocked at three
shops and sold out at all of them. One live run reported 13 of 16 producers
"found nowhere" while a single shop hid 2135 sold-out listings.

A sold-out listing is still not a find, and nothing here makes it one. It is
not evaluated, not written to hits.json, not recorded in the market pool and
above all not written to seen.json -- that last one is what makes a restock
read as new.
"""
import json

import pytest

import notify
import scraper

from canned_shop import FakeCrawler, PRODUCERS, SHOPS, product, shopify, woo, woo_product
from test_end_to_end import BASIC, rows


ALL_SOLD_OUT = {
    "https://shopify.test": shopify([
        product("Zzz Domaine Chardonnay 2020", 60, available=False)]),
    "https://woo.test": woo([
        woo_product("Zzz Domaine Chardonnay 2020", 65, in_stock=False),
        woo_product("Zzz Negoce Poulsard 2022", 25, in_stock=True),
    ]),
    "https://html.test": "<html><body><p>rien</p></body></html>",
}


# --- A1. check_shop keeps what it used to throw away --------------------------

@pytest.fixture
def watching(monkeypatch):
    """check_shop reads the module-level roster; these tests use the canned
    one so they never pin today's real producer list."""
    monkeypatch.setattr(scraper, "PRODUCERS", PRODUCERS)


def test_check_shop_reports_the_producers_it_saw_sold_out(watching):
    shop = SHOPS[0]
    client = FakeCrawler({"https://shopify.test": shopify([
        product("Zzz Domaine Chardonnay 2020", 60, available=False)])})
    result = scraper.check_shop(shop, client)
    assert result == [], "a sold-out listing is not a hit"
    assert [h["producer"] for h in result.sold_out] == ["Zzz Domaine"]
    assert result.sold_out[0]["shop"] == "zzz-shopify"


def test_a_sold_out_listing_still_counts_as_a_parsed_product(watching):
    """The probe decides whether an adapter works by counting products, so a
    shop whose stock is out today must not read as broken."""
    shop = SHOPS[0]
    client = FakeCrawler({"https://shopify.test": shopify([
        product("Zzz Domaine Chardonnay 2020", 60, available=False)])})
    assert scraper.check_shop(shop, client).products_parsed == 1


def test_an_in_stock_shop_reports_nothing_sold_out(watching):
    result = scraper.check_shop(SHOPS[0], FakeCrawler(BASIC))
    assert result.sold_out == []
    assert len(result) == 1


# --- A2. the two notes now mean different things ------------------------------

def test_a_producer_sold_out_everywhere_is_not_reported_as_missing(pipeline):
    pipeline(ALL_SOLD_OUT)
    body = pipeline.sent[-1]
    missing = body.split("found nowhere", 1)[1] if "found nowhere" in body else ""
    assert "Zzz Domaine" not in missing, "a stocked-but-empty producer read as absent"
    assert "sold out" in body.lower()
    sold = body.lower().split("sold out", 1)[1]
    assert "Zzz Domaine".lower() in sold


def test_the_note_names_the_shops_to_watch(pipeline):
    """Which shop stocks it at all is the actionable half: it is where to
    look again next week."""
    pipeline(ALL_SOLD_OUT)
    body = pipeline.sent[-1]
    sold = body.lower().split("sold out", 1)[1]
    assert "zzz-shopify" in sold and "zzz-woo" in sold


def test_a_producer_matched_nowhere_at_all_is_still_named(pipeline):
    """The alias signal the note was built for, now that it means it."""
    pipeline({
        "https://shopify.test": shopify([product("Someone Else Chardonnay", 20)]),
        "https://woo.test": woo([woo_product("Zzz Negoce Poulsard 2022", 25)]),
        "https://html.test": "<html><body><p>rien</p></body></html>",
    })
    body = pipeline.sent[-1]
    missing = body.split("found nowhere", 1)[1]
    assert "Zzz Domaine" in missing and "Zzz Other" in missing
    assert "Zzz Negoce" not in missing


def test_a_producer_in_stock_somewhere_is_in_neither_note(pipeline):
    pipeline(BASIC)
    body = pipeline.sent[-1]
    assert "found nowhere" not in body
    assert "sold out" not in body.lower()


def test_the_run_log_says_both_things_too(pipeline, capsys):
    pipeline(ALL_SOLD_OUT)
    out = capsys.readouterr().out
    assert "sold out" in out.lower()
    assert "Zzz Domaine" in out


# --- A3. a sold-out match must not touch anything downstream ------------------

def test_sold_out_matches_stay_out_of_hits_and_the_market_pool(pipeline):
    pipeline(ALL_SOLD_OUT)
    hits = json.loads((pipeline.tmp / "hits.json").read_text())
    assert [h["producer"] for h in hits] == ["Zzz Negoce"]
    store = json.loads((pipeline.tmp / "observations.json").read_text())
    assert all("Zzz Domaine" != r.get("producer") for r in store["records"])


def test_a_sold_out_listing_is_never_written_to_seen_json(pipeline):
    """This is load-bearing. seen.json is what silences an item; an entry for
    a sold-out listing would make its restock look like an old friend
    instead of news."""
    pipeline(ALL_SOLD_OUT)
    state = json.loads((pipeline.tmp / "seen.json").read_text())
    sold_out_key = notify.item_key({
        "shop": "zzz-shopify",
        "url": "https://shopify.test/products/zzz-domaine-chardonnay-2020",
        "variant_title": "Default",
    })
    assert sold_out_key not in state


def test_a_restock_alerts(pipeline):
    """The most valuable alert this scraper can send, and until now it
    worked only as a side effect of sold-out listings being discarded.
    Asserted end to end so recording them can never silently kill it."""
    pipeline(ALL_SOLD_OUT)
    first = len(pipeline.sent)

    back = dict(ALL_SOLD_OUT)
    back["https://shopify.test"] = shopify([
        product("Zzz Domaine Chardonnay 2020", 60, available=True)])
    pipeline(back)

    assert len(pipeline.sent) == first + 1, "a restock sent nothing"
    assert "Zzz Domaine" in rows(pipeline.sent[-1])


# --- B. near-miss diagnosis ----------------------------------------------------

NEAR_MISS = {
    "https://shopify.test": shopify([product("Domaine Zzz Domain Chardonnay", 40)]),
    "https://woo.test": woo([woo_product("Zzz Negoce Poulsard 2022", 25)]),
    "https://html.test": "<html><body><p>rien</p></body></html>",
}


def test_a_one_edit_spelling_is_reported_as_a_suspicion(pipeline):
    """"Zzz Domain" is one deletion from the watched "zzz domaine". Without
    this, the run says only that the producer was found nowhere -- true, and
    useless."""
    pipeline(NEAR_MISS)
    body = pipeline.sent[-1]
    assert "near-miss" in body.lower()
    near = body.lower().split("near-miss", 1)[1]
    assert "domain" in near and "zzz-shopify" in near


def test_a_producer_that_matched_gets_no_near_miss_line(pipeline):
    pipeline(BASIC)
    assert "near-miss" not in pipeline.sent[-1].lower()


def test_unrelated_words_are_not_offered_as_near_misses(pipeline):
    pipeline({
        "https://shopify.test": shopify([product("Chardonnay Savagnin Trousseau", 20)]),
        "https://woo.test": woo([woo_product("Zzz Negoce Poulsard 2022", 25)]),
        "https://html.test": "<html><body><p>rien</p></body></html>",
    })
    body = pipeline.sent[-1]
    assert "near-miss" not in body.lower(), "a long word list read as a typo"


def test_near_misses_is_pure_and_takes_a_corpus():
    got = scraper.near_misses(
        ["Tom Gauditiabois"],
        {"cavepurjus": {"gaudiciabois"}, "winenot": {"chardonnay"}},
        producers={"Tom Gauditiabois": ["tom gauditiabois", "gauditiabois"]},
    )
    assert got == ["Tom Gauditiabois: 'gaudiciabois' at cavepurjus"]


def test_near_misses_ignores_short_tokens():
    """Short words are one edit from everything. "popy" vs "pope" is not a
    lead, it is noise."""
    got = scraper.near_misses(
        ["Thomas Popy"], {"a-shop": {"pope"}},
        producers={"Thomas Popy": ["thomas popy", "popy"]})
    assert got == []


def test_near_misses_needs_more_than_one_edit_to_stay_quiet():
    got = scraper.near_misses(
        ["Tom Gauditiabois"], {"a-shop": {"gaudicialbois"}},
        producers={"Tom Gauditiabois": ["gauditiabois"]})
    assert got == []


# --- C. the same rule against real markup --------------------------------------

def real_page(name):
    from pathlib import Path
    return (Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8")


def test_the_real_grower_list_separates_the_namesakes():
    """purewijnen's landing page is a grower index: 41 links, two of them
    surnames we watch. Widening the separators must find ours and leave the
    other estate alone -- asserted on the markup as captured, because a
    synthetic <ul> cannot be wrong in the ways a real one is."""
    import autoselect

    links = autoselect.find_producer_links(
        real_page("purewijnen-growers-excerpt.html"),
        "https://www.purewijnen.be/", scraper.match_producers)
    hrefs = [url for _, url in links]   # (producer, url) pairs

    assert any("renaud-bruyere-houillon" in h for h in hrefs), \
        "the estate we watch was not found on a page that lists it"
    assert not any("overnoy-crinquand" in h for h in hrefs), \
        "a different estate matched on a shared surname"
    assert len(hrefs) == 1


def test_the_real_grower_list_is_read_as_an_index_not_a_catalogue():
    """No prices on it, so autoselect must decline rather than invent
    products -- this is why the shop needs the producer-index route."""
    import autoselect

    products = autoselect.find_products(
        real_page("purewijnen-growers-excerpt.html"),
        "https://www.purewijnen.be/", scraper.PRICE_PATTERN, scraper.parse_price)
    assert products == []


# --- B2. what the live run taught the near-miss check --------------------------
#
# Its first live run reported, five times over:
#
#   Overnoy/Houillon: 'pierres' at bbn
#
# because "pierre overnoy" contains a six-letter first name and every French
# wine shop sells something "aux Pierres". A hint that fires on vocabulary is
# a hint nobody reads.

def test_a_word_seen_at_more_than_one_shop_is_vocabulary_not_a_typo():
    """The corpus tells us this for free: a misspelling of a rare grower
    appears at the shop that made it. A word at several shops is the trade's
    own vocabulary."""
    got = scraper.near_misses(
        ["Overnoy/Houillon"],
        {"bbn": {"pierres"}, "biowijnclub": {"pierres"}, "levinnaturel": {"pierres"}},
        producers={"Overnoy/Houillon": ["pierre overnoy"]})
    assert got == []


def test_the_same_word_at_a_single_shop_is_still_offered():
    got = scraper.near_misses(
        ["Tom Gauditiabois"], {"cavepurjus": {"gaudiciabois"}},
        producers={"Tom Gauditiabois": ["gauditiabois"]})
    assert got == ["Tom Gauditiabois: 'gaudiciabois' at cavepurjus"]


def test_a_plural_is_never_a_near_miss():
    got = scraper.near_misses(
        ["Domaine des Murmures"], {"one-shop": {"murmure"}},
        producers={"Domaine des Murmures": ["murmures"]})
    assert got == []
