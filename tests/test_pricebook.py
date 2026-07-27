import datetime as dt

import pricebook


def test_unverified_producer_is_stale():
    book = {"producers": [{"name": "X", "verified": False, "last_verified": None}]}
    stale = pricebook.stale_producers(book, today=dt.date(2026, 1, 1))
    assert [p["name"] for p in stale] == ["X"]


def test_verified_recent_producer_is_not_stale():
    book = {"producers": [{"name": "X", "verified": True, "last_verified": "2026-01-01"}]}
    stale = pricebook.stale_producers(book, today=dt.date(2026, 1, 10))
    assert stale == []


def test_verified_old_producer_is_stale_past_180_days():
    book = {"producers": [{"name": "X", "verified": True, "last_verified": "2025-01-01"}]}
    stale = pricebook.stale_producers(book, today=dt.date(2026, 1, 1))
    assert [p["name"] for p in stale] == ["X"]


def test_verified_with_no_last_verified_date_is_stale():
    book = {"producers": [{"name": "X", "verified": True, "last_verified": None}]}
    stale = pricebook.stale_producers(book, today=dt.date(2026, 1, 1))
    assert [p["name"] for p in stale] == ["X"]


def test_real_pricebook_staleness_matches_the_rule():
    # Deliberately NOT "everything is stale" -- that was true when the book
    # was seeded, but it encodes a passing state as an invariant, so the
    # first producer verified through the issue form would fail CI and
    # block every later config change.
    book = pricebook.load_pricebook()
    stale = {p["name"] for p in pricebook.stale_producers(book)}
    for producer in book["producers"]:
        unverified = not producer.get("verified", False) or not producer.get("last_verified")
        if unverified:
            assert producer["name"] in stale, f"{producer['name']} is unverified but not flagged stale"
        else:
            assert producer["name"] not in stale or (
                dt.date.today() - dt.date.fromisoformat(str(producer["last_verified"]))
            ).days > pricebook.STALE_DAYS


def test_real_pricebook_entries_are_well_formed():
    # The issue form writes into this file, so guard its shape.
    for producer in pricebook.load_pricebook()["producers"]:
        assert producer.get("name"), "every producer needs a name"
        assert "region" in producer and "verified" in producer
        reference = producer.get("reference_750_eur")
        assert reference is None or (isinstance(reference, (int, float)) and reference > 0)
        if producer.get("verified"):
            assert producer.get("reference_750_eur") is not None, (
                f"{producer['name']} is verified but has no reference price"
            )
