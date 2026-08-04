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
    # The note headings, not the words: the coverage table has a SOLD OUT
    # column on every run.
    assert "Watched but found nowhere" not in body
    assert "Matched but sold out everywhere" not in body


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


# --- B3. and what the second live run taught it ---------------------------------
#
# The breadth filter was not enough. The next run offered:
#
#   Overnoy/Houillon: 'pierra' at lacavedespapilles
#   Overnoy/Houillon: 'pierro' at puurwijnshop
#   Domaine Calice:   'domain' at amberbottleshop
#   Domaine Calice:   'malice' at bbn
#
# Each appears at one shop, so breadth said nothing -- the problem is the
# other end: "pierre", "calice" and "domaine" are not distinctive enough to
# hunt typos of. The corpus can say that too: a word the trade uses at
# several shops is vocabulary, whether it is the target or the candidate.

def test_a_target_the_whole_trade_uses_is_not_worth_hunting():
    corpus = {
        "lacavedespapilles": {"pierra", "pierre"},
        "puurwijnshop": {"pierro", "pierre"},
        "vinibee": {"pierre"},
    }
    got = scraper.near_misses(
        ["Overnoy/Houillon"], corpus,
        producers={"Overnoy/Houillon": ["pierre overnoy"]})
    assert got == [], "hunted typos of a word five shops use in earnest"


def test_a_six_letter_target_is_below_the_floor():
    """"calice" is a French word before it is an estate, and at six letters
    it is one edit from "malice", "calices", "police". The floor is where
    that stops."""
    got = scraper.near_misses(
        ["Domaine Calice"], {"bbn": {"malice"}},
        producers={"Domaine Calice": ["calice", "domaine du calice"]})
    assert got == []


def test_a_distinctive_target_still_works_at_one_shop():
    got = scraper.near_misses(
        ["Tom Gauditiabois"], {"cavepurjus": {"gaudiciabois"}},
        producers={"Tom Gauditiabois": ["gauditiabois"]})
    assert got == ["Tom Gauditiabois: 'gaudiciabois' at cavepurjus"]


def test_the_five_lines_the_live_run_printed_are_all_gone():
    """The regression test for this whole idea: the exact corpus shape that
    produced five useless lines must now produce none."""
    corpus = {
        "lacavedespapilles": {"pierra", "pierre", "domaine"},
        "puurwijnshop": {"pierro", "pierre", "domaine"},
        "vinibee": {"pieure", "pierre", "domaine"},
        "amberbottleshop": {"domain", "domaine"},
        "bbn": {"malice", "domaine"},
    }
    got = scraper.near_misses(
        ["Overnoy/Houillon", "Domaine Calice"], corpus,
        producers={
            "Overnoy/Houillon": ["pierre overnoy", "emmanuel houillon"],
            "Domaine Calice": ["domaine du calice", "du calice", "calice"],
        })
    assert got == []


# --- C. the coverage table ------------------------------------------------------
#
# "Does what we read match what the shop actually sells?" was only answerable
# by reading a run log line by line. It is the question that decides whether a
# shop is worth having, so the run states it: one row per live shop, and a
# TRUNCATED marker when the walk stopped before the catalogue did.

def test_a_run_reports_a_row_per_live_shop(pipeline, capsys):
    pipeline(BASIC)
    out = capsys.readouterr().out
    table = out.split("SHOP", 1)[1]
    for name in ("zzz-shopify", "zzz-woo", "zzz-html"):
        assert name in table
    assert "zzz-dark" not in table, "an unverified shop is not live"


def test_the_row_carries_products_stock_and_producers(pipeline, capsys):
    pipeline(BASIC)
    row = next(l for l in capsys.readouterr().out.splitlines() if "zzz-shopify" in l
               and "|" in l)
    assert "1" in row                      # one product parsed
    assert "Zzz Domaine" in row            # and which producer it matched


def test_sold_out_counts_show_up_in_the_table(pipeline, capsys):
    pipeline(ALL_SOLD_OUT)
    rows = [l for l in capsys.readouterr().out.splitlines() if "|" in l]
    shopify_row = next(l for l in rows if "zzz-shopify" in l)
    assert "1" in shopify_row, "the sold-out listing was still parsed"


def test_the_table_reaches_the_email(pipeline):
    pipeline(BASIC)
    body = pipeline.sent[-1]
    assert "Shop coverage" in body
    assert "zzz-shopify" in body
    # One row per line, not a comma-joined blob.
    coverage = body.split("Shop coverage", 1)[1]
    assert coverage.count("\n") >= 3


def test_a_shop_whose_walk_stopped_early_is_marked_truncated(monkeypatch, watching):
    """The single most important cell: a shop whose walk stopped before its
    catalogue did is not a shop whose selection we have seen."""
    monkeypatch.setattr(scraper, "MAX_PAGES_PER_SHOP", 1)
    monkeypatch.setattr(scraper, "SHOPIFY_PAGE_SIZE", 1)
    client = FakeCrawler({"https://shopify.test": shopify(
        [product("Zzz Domaine Chardonnay 2020", 60)])})

    result = scraper.check_shop(SHOPS[0], client)

    assert result.truncated, "a full last page means there is more we did not read"
    assert scraper.coverage_row(SHOPS[0], result)["status"] == "TRUNCATED"


def test_a_shop_read_to_the_end_is_not_marked_truncated(watching):
    result = scraper.check_shop(SHOPS[0], FakeCrawler(BASIC))
    assert not result.truncated
    assert scraper.coverage_row(SHOPS[0], result)["status"] == "ok"


def test_the_table_says_which_catalogues_were_cut_short():
    rows = [
        {"shop": "big", "platform": "shopify", "status": "TRUNCATED",
         "products": 5000, "in_stock": 4000, "sold_out": 1000, "hits": 2,
         "producers": ["Ganevat"]},
        {"shop": "small", "platform": "html", "status": "ok", "products": 12,
         "in_stock": 12, "sold_out": 0, "hits": 0, "producers": []},
    ]
    table = "\n".join(scraper.coverage_table(rows))
    assert "TRUNCATED at big" in table
    assert "5012 product(s) read" in table
    assert "2 live shop(s)" in table


def test_an_unreachable_shop_still_gets_a_row(pipeline, capsys):
    pipeline(BASIC, fail_hosts=("woo.test",))
    rows = [l for l in capsys.readouterr().out.splitlines() if "zzz-woo" in l and "|" in l]
    assert rows, "a shop that failed is missing from the table entirely"
    assert "unreachable" in rows[0]


def test_coverage_is_written_for_the_artifact(pipeline):
    import json as _json
    pipeline(BASIC)
    rows = _json.loads((pipeline.tmp / "coverage.json").read_text())
    by_shop = {r["shop"]: r for r in rows}
    assert by_shop["zzz-shopify"]["products"] == 1
    assert by_shop["zzz-shopify"]["in_stock"] == 1
    assert by_shop["zzz-shopify"]["producers"] == ["Zzz Domaine"]
    assert by_shop["zzz-html"]["products"] == 3


# --- C2. sold out means sold out, not "sold out and one of ours" ---------------
#
# The first version of this table read its SOLD OUT column from
# ShopResult.sold_out, which holds only out-of-stock listings that matched a
# watched producer. Live, that printed 30 for mareehaute where the run had
# skipped 2139 -- a 70x understatement, in the one column a person would use to
# judge whether a shop's numbers look like its real selection.

MIXED_STOCK = {
    "https://shopify.test": shopify([
        product("Zzz Domaine Chardonnay 2020", 60, available=False),   # ours, gone
        product("Someone Else Savagnin", 30, available=False),         # theirs, gone
        product("Zzz Domaine Savagnin 2019", 70, available=True),      # ours, here
    ]),
    "https://woo.test": woo([]),
    "https://html.test": "<html><body><p>rien</p></body></html>",
}


def test_the_sold_out_column_counts_every_out_of_stock_listing(watching):
    result = scraper.check_shop(SHOPS[0], FakeCrawler(MIXED_STOCK))
    row = scraper.coverage_row(SHOPS[0], result)

    assert row["products"] == 3
    assert row["sold_out"] == 2, "only the watched producer's listing was counted"
    assert row["in_stock"] == 1
    assert row["hits"] == 1


def test_the_matched_sold_out_rows_are_still_only_ours(watching):
    """The note names producers, so that list stays filtered to the roster --
    the two counts answer different questions."""
    result = scraper.check_shop(SHOPS[0], FakeCrawler(MIXED_STOCK))
    assert [h["producer"] for h in result.sold_out] == ["Zzz Domaine"]
    assert result.out_of_stock == 2


def test_in_stock_and_sold_out_add_up_to_products(pipeline):
    import json as _json
    pipeline(MIXED_STOCK)
    for row in _json.loads((pipeline.tmp / "coverage.json").read_text()):
        assert row["in_stock"] + row["sold_out"] == row["products"], row["shop"]
