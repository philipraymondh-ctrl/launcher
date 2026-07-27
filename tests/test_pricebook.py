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


def test_real_pricebook_all_seeded_producers_are_currently_stale():
    book = pricebook.load_pricebook()
    stale_names = {p["name"] for p in pricebook.stale_producers(book)}
    all_names = {p["name"] for p in book["producers"]}
    assert stale_names == all_names
