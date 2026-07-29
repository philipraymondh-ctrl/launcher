"""One rule for comparing names, and the line it must not cross.

`normalize` lived in four modules as four identical copies, each with a
comment explaining why it had been pasted there. That is fine while nobody
changes it and fatal the moment somebody does: a producer added through the
issue form derives its alias in `apply_issue`, and if that disagrees with
`scraper` by one character the producer silently matches nothing, for weeks.

So there is one module, and it exposes two functions with different jobs:
`strip_accents`, which every existing caller already wanted, and
`match_key`, which is only for deciding whether a name matches -- and must
never touch text that a price/vintage regex will read.
"""
import apply_issue
import autoselect
import evaluate
import market
import scraper
import textnorm


# --- one implementation, five callers -----------------------------------------

def test_every_module_shares_the_one_implementation():
    """Not "behaves the same" -- literally the same object, so the next edit
    cannot reach one caller and miss another."""
    for module in (scraper, market, evaluate, apply_issue):
        assert module.normalize is textnorm.strip_accents, module.__name__
    assert autoselect._strip_accents is textnorm.strip_accents


def test_strip_accents_is_exactly_what_it_used_to_be():
    assert textnorm.strip_accents("Côtes du Jura, 2018 €50,00") == "cotes du jura, 2018 €50,00"
    assert textnorm.strip_accents("Bruyère-Houillon") == "bruyere-houillon"
    assert textnorm.strip_accents(None) == ""


# --- match_key ----------------------------------------------------------------

def test_match_key_collapses_the_separators_a_shop_chooses():
    assert textnorm.match_key("Bruyère-Houillon") == "bruyere houillon"
    assert textnorm.match_key("Renaud Bruyère–Houillon") == "renaud bruyere houillon"
    assert textnorm.match_key("l'Allanté") == "l allante"
    assert textnorm.match_key("  Popy,  Thomas  ") == "popy thomas"


def test_match_key_reads_an_ampersand_as_the_word():
    """Shops write both. "Allanté & Boulanger" and "Allante et Boulanger"
    are one estate and must produce one key."""
    assert textnorm.match_key("Allanté & Boulanger") == "allante et boulanger"
    assert textnorm.match_key("Anne & Jean-François Ganevat") == \
        "anne et jean francois ganevat"


def test_match_key_is_idempotent():
    once = textnorm.match_key("Overnoy / Houillon")
    assert textnorm.match_key(once) == once


# --- the line match_key must not cross ----------------------------------------

def test_the_vintage_rule_still_holds():
    """`match_key` would turn "2018,50 €" into "2018 50", stripping the
    currency marker that tells VINTAGE_RE a price from a vintage. market
    must keep using strip_accents, so this stays true."""
    assert market.parse_vintage("Chardonnay 2018") == 2018
    assert market.parse_vintage("Poulsard 24,50 €") is None
    assert market.parse_vintage("Savagnin 2018,50 €") is None


def test_prices_are_still_only_currency_adjacent_numbers():
    assert scraper.parse_price("2018") is None
    assert scraper.parse_price("Chardonnay 2018 - 45,00 €") == 45.0


# --- the two callers that decide a match must agree ---------------------------

def test_a_derived_alias_matches_the_text_it_came_from(monkeypatch):
    """apply_issue derives an alias from a submitted producer name; scraper
    matches it against a listing. If the two disagree the producer is added
    and then never seen again."""
    submitted = "Domaine Léon & Fils"
    alias = apply_issue.match_key(submitted)
    monkeypatch.setattr(scraper, "PRODUCERS", {"Domaine Leon et Fils": [alias]})
    assert scraper.match_producers("2021 DOMAINE LEON ET FILS Savagnin") == \
        ["Domaine Leon et Fils"]
    assert scraper.match_producers("Domaine Léon-Fils Savagnin") == []


# --- matching against real shop spellings -------------------------------------

def test_a_hyphenated_estate_matches_without_its_own_alias(monkeypatch):
    monkeypatch.setattr(scraper, "PRODUCERS", {"Bruyere Houillon": ["bruyere houillon"]})
    assert scraper.match_producers("Renaud Bruyère-Houillon Savagnin 2020") == \
        ["Bruyere Houillon"]


def test_an_ampersand_estate_matches_without_its_own_alias(monkeypatch):
    monkeypatch.setattr(
        scraper, "PRODUCERS", {"Allante et Boulanger": ["allante et boulanger"]})
    assert scraper.match_producers("Allanté & Boulanger, Ploussard 2022") == \
        ["Allante et Boulanger"]


def test_the_namesake_next_to_it_on_the_real_page_still_does_not_match():
    """purewijnen's grower list holds "Overnoy-Crinquand" and "Renaud
    Bruyère-Houillon" in the same <ul>. Collapsing separators widens every
    alias at once, so the two must still land on different sides -- against
    the real PRODUCERS, not a synthetic roster."""
    assert scraper.match_producers("Overnoy-Crinquand Poulsard 2021") == []
    assert scraper.match_producers("Renaud Bruyère-Houillon Savagnin") == \
        ["Bruyere Houillon"]
    # And the estate we do watch under that surname is still found.
    assert scraper.match_producers("Pierre Overnoy / Emmanuel Houillon") == \
        ["Overnoy/Houillon"]


def test_widening_did_not_reach_across_a_third_name():
    """"Overnoy-Crinquand et Houillon Corentin" is two other estates. The
    alias "overnoy houillon" must not appear in it just because the
    punctuation went away."""
    assert scraper.match_producers("Overnoy-Crinquand et Houillon Corentin") == []
