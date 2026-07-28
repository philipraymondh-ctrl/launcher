"""The reference is read off the crawl instead of typed in, so these tests
are mostly about the three things one number per producer got wrong.

The listings here are the real ones from the 2026-07-27 run -- the run that
scored a EUR 30 negoce bottle and a EUR 450 coffret against the same EUR 70
figure and called one a bargain and the other a rip-off.
"""
import json
from datetime import date, timedelta

import market

FORMATS = {375: 0.55, 620: 0.83, 750: 1.0, 1500: 2.3, 3000: 5.0}
ALIASES = {"Ganevat": ["ganevat"], "Labet": ["labet"], "Roumier": ["roumier"]}


def hit(shop, title, price, size_ml=750, bundle=False, producer="Ganevat"):
    return {
        "shop": shop, "title": title, "price": price, "producer": producer,
        "size_ml": size_ml, "size_confidence": "high", "bundle": bundle,
        "url": f"https://{shop}.test/x",
    }


def store_of(*hits, today=None):
    records = [market.observation(h, FORMATS, ALIASES, today=today) for h in hits]
    return {"records": [r for r in records if r]}


# --- vintage ----------------------------------------------------------------

def test_parses_the_vintage_from_a_title():
    assert market.parse_vintage("La Croix des Batailles 2024 - Anne et Jean Francois") == 2024
    assert market.parse_vintage("Vin jaune, 2012, Jaune") == 2012


def test_a_price_is_never_read_as_a_vintage():
    """The mirror of the rule in scraper.py. There, a number only counts as
    a price when a currency marker touches it; here a number only counts as
    a vintage when none does. The two can never claim the same digits, so a
    EUR 2018 bottle is not a 2018 bottle."""
    assert market.parse_vintage("€2019") is None
    assert market.parse_vintage("2019 EUR") is None
    assert market.parse_vintage("1200 EUR") is None
    assert market.parse_vintage("Coffret 2018 EUR") is None
    # A bare year in the same title still reads, even next to a price.
    assert market.parse_vintage("Poulprix 2024 - €29") == 2024


def test_no_vintage_is_not_an_error():
    assert market.parse_vintage("Coffret Les Tetes d'Affiche") is None


def test_the_earlier_year_wins_when_a_title_carries_two():
    assert market.parse_vintage("Vin Jaune 2012, mis en bouteille 2020") == 2012


# --- cuvee identity ---------------------------------------------------------

def test_the_same_wine_named_differently_still_matches():
    a = market.cuvee_tokens("Les Grands Teppes VV, 2018, Blanc", "Ganevat", ["ganevat"])
    b = market.cuvee_tokens("Ganevat Grands Teppes VV 2018", "Ganevat", ["ganevat"])
    assert market.same_cuvee(a, b)


def test_two_different_cuvees_do_not_match():
    a = market.cuvee_tokens("Les Grands Teppes VV 2018", "Ganevat", ["ganevat"])
    b = market.cuvee_tokens("La Croix des Batailles 2024", "Ganevat", ["ganevat"])
    assert not market.same_cuvee(a, b)


def test_the_producer_name_is_not_part_of_the_cuvee():
    tokens = market.cuvee_tokens("Domaine Ganevat Poulprix 2024", "Ganevat", ["ganevat"])
    assert "ganevat" not in tokens
    assert "poulprix" in tokens


# --- negoce vs domaine, without being told ----------------------------------

def test_negoce_and_domaine_land_in_different_segments():
    """The whole point. Same producer, two price worlds, no configuration."""
    negoce = market.segment("La Croix des Batailles 2024 - Anne et Jean Francois Ganevat",
                            "Ganevat", ["ganevat"])
    domaine = market.segment("Les grands teppes VV, 2018, Blanc - Ganevat",
                             "Ganevat", ["ganevat"])
    assert negoce != domaine
    assert "anne" in negoce


def test_domaine_prefix_does_not_split_the_estate_from_itself():
    """"Domaine Ganevat" and "Ganevat" are one producer line, not two."""
    assert (market.segment("Domaine Ganevat Vin Jaune", "Ganevat", ["ganevat"])
            == market.segment("Ganevat Vin Jaune", "Ganevat", ["ganevat"]))


def test_a_title_without_the_producer_name_falls_back_to_the_producer():
    assert market.segment("les Ignorants", "Ganevat", ["ganevat"]) == "ganevat"


# --- observations -----------------------------------------------------------

def test_a_magnum_is_recorded_as_its_per_bottle_equivalent():
    record = market.observation(hit("a", "Magnum J'en Veux Encore 2024", 72, size_ml=1500),
                                FORMATS, ALIASES)
    assert record["price750"] == round(72 / 2.3, 2)


def test_a_plain_bottle_counts_even_though_its_size_is_a_default():
    """parse_size returns (750, "low") for any title without a size word --
    i.e. almost every bottle. Treating that as unusable emptied the pool."""
    h = hit("a", "Poulprix 2024", 29)
    h["size_confidence"] = "low"
    record = market.observation(h, FORMATS, ALIASES)
    assert record is not None and record["price750"] == 29


def test_a_clavelin_is_normalised_not_taken_at_face_value():
    record = market.observation(hit("a", "Vin Jaune 2012", 118, size_ml=620),
                                FORMATS, ALIASES)
    assert record["price750"] == round(118 / 0.83, 2)


def test_a_coffret_is_recorded_whole_and_kept_separate():
    record = market.observation(hit("a", "COFFRET ANNIVERSAIRE", 450, bundle=True),
                                FORMATS, ALIASES)
    assert record["bucket"] == "coffret"
    assert record["price750"] == 450


def test_merging_keeps_the_newest_price_per_shop():
    old = date.today() - timedelta(days=3)
    store = store_of(hit("a", "Poulprix 2024", 29), today=old)
    store = market.merge(store, store_of(hit("a", "Poulprix 2024", 24))["records"])
    assert [r["price750"] for r in store["records"]] == [24]


def test_stale_observations_age_out():
    ancient = date.today() - timedelta(days=market.MAX_AGE_DAYS + 10)
    store = store_of(hit("a", "Poulprix 2020", 29), today=ancient)
    assert market.merge(store, []) == {"records": [], "updated": date.today().isoformat()}


# --- the reference ladder ---------------------------------------------------

def test_same_wine_at_two_other_shops_is_a_high_confidence_reference():
    store = store_of(
        hit("b", "Poulprix 2024 - Anne et Jean Francois Ganevat", 32),
        hit("c", "Poulprix 2024 - Anne et Jean Francois Ganevat", 34),
    )
    ref = market.reference_from_market(
        hit("a", "Poulprix 2024 - Anne et Jean Francois Ganevat", 29), store, FORMATS, ALIASES)
    assert ref["confidence"] == "high"
    assert ref["price"] == 33
    assert ref["shops"] == ["b", "c"]


def test_a_shop_cannot_be_its_own_reference():
    """Otherwise one shop's pricing defines its own bargains."""
    store = store_of(
        hit("a", "Poulprix 2024", 60),
        hit("a", "Poulprix 2024", 62),
    )
    assert market.reference_from_market(hit("a", "Poulprix 2024", 29), store, FORMATS, ALIASES) is None


def test_one_other_shop_is_a_comparison_but_not_a_market():
    """Requiring two *other* shops meant three had to stock the same wine,
    which across 13 catalogues never happened. One counts -- at medium."""
    store = store_of(hit("b", "Poulprix 2024", 32))
    ref = market.reference_from_market(hit("a", "Poulprix 2024", 29), store, FORMATS, ALIASES)
    assert ref["confidence"] == "medium"
    assert ref["price"] == 32


def test_hyphenation_does_not_split_a_producer_line_in_two():
    """"Jean-Francois" and "Jean Francois" are one label, so the two shops
    spelling it differently must still compare."""
    store = store_of(hit("b", "Poulprix 2024 - Anne et Jean Francois Ganevat", 32))
    ref = market.reference_from_market(
        hit("a", "Poulprix 2024 Anne et Jean-Francois Ganevat", 29), store, FORMATS, ALIASES)
    assert ref is not None and ref["price"] == 32


def test_a_short_cuvee_is_not_swamped_by_the_negoce_name():
    """"SUL Q" and "Poulprix" share only "Anne et Jean Francois", which is
    the label, not the wine. They came out 60% similar and matched."""
    store = store_of(hit("b", "Poulprix 2024 - Anne et Jean Francois Ganevat", 30))
    assert market.reference_from_market(
        hit("a", "SUL Q - Anne et Jean-Francois Ganevat", 53), store, FORMATS, ALIASES) is None


def test_another_vintage_is_a_hint_not_a_verdict():
    store = store_of(
        hit("b", "Vin Jaune 2014", 120),
        hit("c", "Vin Jaune 2015", 130),
    )
    ref = market.reference_from_market(hit("a", "Vin Jaune 2012", 118), store, FORMATS, ALIASES)
    assert ref["confidence"] == "medium"
    assert "other vintages" in ref["basis"]


def test_an_unknown_cuvee_falls_back_to_its_own_line_not_the_whole_producer():
    """A negoce bottle must not be measured against domaine prices."""
    store = store_of(
        hit("b", "Poulprix 2024 - Anne et Jean Francois Ganevat", 30),
        hit("c", "J'en Veux Encore 2024 - Anne et Jean Francois Ganevat", 31),
        hit("d", "La Colline des Dames 2024 - Anne et Jean Francois Ganevat", 40),
        hit("b", "Les Grands Teppes VV 2018 - Ganevat", 105),
        hit("c", "Les Devoiles 2012 - Ganevat", 120),
        hit("d", "Vin Jaune 2012 - Ganevat", 118),
    )
    ref = market.reference_from_market(
        hit("a", "Un Cuvee Inconnue 2024 - Anne et Jean Francois Ganevat", 33),
        store, FORMATS, ALIASES)
    assert ref["confidence"] == "low"
    # The negoce median (~31), nowhere near the domaine wines in the same pool.
    assert ref["price"] < 60


def test_a_coffret_is_only_compared_to_other_coffrets():
    store = store_of(
        hit("b", "Poulprix 2024", 30),
        hit("c", "J'en Veux Encore 2024", 31),
        hit("d", "La Colline 2024", 40),
    )
    coffret = hit("a", "COFFRET ANNIVERSAIRE", 450, bundle=True)
    assert market.reference_from_market(coffret, store, FORMATS, ALIASES) is None

    store = market.merge(store, store_of(
        hit("b", "COFFRET ANNIVERSAIRE", 420, bundle=True),
        hit("c", "COFFRET ANNIVERSAIRE", 480, bundle=True),
    )["records"])
    ref = market.reference_from_market(coffret, store, FORMATS, ALIASES)
    assert ref["price"] == 450


def test_a_producer_with_no_observations_gets_nothing_rather_than_a_guess():
    store = store_of(hit("b", "Bonnes Mares 2019", 400, producer="Roumier"))
    assert market.reference_from_market(hit("a", "Poulprix 2024", 29), store, FORMATS, ALIASES) is None


# --- durability -------------------------------------------------------------

def test_a_corrupt_store_does_not_take_the_run_down(tmp_path):
    path = tmp_path / "observations.json"
    path.write_text("{not json")
    assert market.load_observations(path) == {"records": []}


def test_a_missing_store_is_an_empty_one(tmp_path):
    assert market.load_observations(tmp_path / "nope.json") == {"records": []}


def test_the_store_round_trips(tmp_path):
    path = tmp_path / "observations.json"
    store = market.merge(store_of(hit("b", "Poulprix 2024", 30)), [])
    market.save_observations(store, path)
    assert market.load_observations(path) == json.loads(path.read_text())


# --- producer matching must stay specific -----------------------------------

def test_a_shared_surname_reports_only_the_more_specific_producer(monkeypatch):
    """Two real estates share "Houillon". Without preferring the longer
    alias, every bottle of one is also reported as the other."""
    import scraper
    monkeypatch.setattr(scraper, "PRODUCERS", {
        "Zzz Alpha": ["zzzsurname"],
        "Zzz Beta Zzzsurname": ["zzzbeta zzzsurname"],
    })
    assert scraper.match_producers("Zzzbeta Zzzsurname 2020") == ["Zzz Beta Zzzsurname"]
    assert scraper.match_producers("Zzzsurname alone 2020") == ["Zzz Alpha"]


def test_two_unrelated_producers_in_one_title_both_still_report(monkeypatch):
    """Specificity must not collapse genuinely distinct matches -- a mixed
    case or a comparison page lists several producers."""
    import scraper
    monkeypatch.setattr(scraper, "PRODUCERS", {
        "Zzz One": ["zzzone"],
        "Zzz Two": ["zzztwo"],
    })
    assert sorted(scraper.match_producers("Zzzone and Zzztwo")) == ["Zzz One", "Zzz Two"]


def test_every_producer_has_at_least_one_alias():
    """Assert the rule, not today's roster: a producer with no alias can
    never match anything, so it is silently dead config."""
    import scraper
    for name, aliases in scraper.PRODUCERS.items():
        assert aliases, f"{name} has no aliases and can never match"


def test_no_alias_is_shorter_than_four_characters():
    """Short aliases match inside unrelated words and flood the digest."""
    import scraper
    for name, aliases in scraper.PRODUCERS.items():
        for alias in aliases:
            assert len(alias) >= 4, f"{name}: alias {alias!r} is too short to be safe"


def test_every_watched_producer_has_a_pricebook_entry():
    """Not a check on which producers exist -- a check that the two config
    files agree, so a producer added to one is never missing from the other."""
    import scraper
    import evaluate
    book = {p["name"] for p in evaluate.load_pricebook()["producers"]}
    missing = set(scraper.PRODUCERS) - book
    assert not missing, f"in PRODUCERS but not prices.yaml: {sorted(missing)}"
