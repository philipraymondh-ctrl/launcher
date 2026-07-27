import json
from datetime import datetime, timedelta, timezone

import pytest

import notify


def make_hit(**overrides):
    hit = {
        "shop": "example-shop",
        "producer": "Labet",
        "cuvee": "Cotes du Jura Chardonnay",
        "title": "Domaine Labet Cotes du Jura Chardonnay 2020",
        "price": 55.0,
        "url": "https://example-shop.test/products/labet-chardonnay",
        "variant_title": "",
        "size_ml": 750,
        "expected_price": 55.0,
        "classification": "FAIR",
        "caveat": False,
    }
    hit.update(overrides)
    return hit


# --- cooldown / dedupe --------------------------------------------------------

def test_new_item_always_alerts():
    hit = make_hit(classification="DEAL")
    alerting, state = notify.select_alerts([hit], state={})
    assert alerting == [hit]
    key = notify.item_key(hit)
    assert state[key]["last_alerted_price"] == 55.0


def test_same_item_twice_in_a_row_alerts_once():
    hit = make_hit(classification="DEAL")
    now = datetime.now(timezone.utc)

    alerting1, state = notify.select_alerts([hit], state={}, now=now)
    assert len(alerting1) == 1

    # Same item, same price, same classification, seen again shortly after.
    alerting2, state = notify.select_alerts([hit], state=state, now=now + timedelta(minutes=5))
    assert alerting2 == []


def test_price_drop_over_10_percent_re_alerts_after_cooldown_window_not_needed():
    hit1 = make_hit(price=100.0, classification="FAIR")
    now = datetime.now(timezone.utc)
    alerting1, state = notify.select_alerts([hit1], state={}, now=now)
    assert len(alerting1) == 1

    # Only 1 day later, but price dropped >10% since the last alert.
    hit2 = make_hit(price=85.0, classification="DEAL")
    alerting2, state = notify.select_alerts([hit2], state=state, now=now + timedelta(days=1))
    assert alerting2 == []  # still within the 30-day cooldown -- cooldown wins


def test_price_drop_over_10_percent_alerts_once_cooldown_has_elapsed():
    hit1 = make_hit(price=100.0, classification="FAIR")
    now = datetime.now(timezone.utc)
    alerting1, state = notify.select_alerts([hit1], state={}, now=now)
    assert len(alerting1) == 1

    hit2 = make_hit(price=85.0, classification="DEAL")
    alerting2, state = notify.select_alerts([hit2], state=state, now=now + timedelta(days=31))
    assert len(alerting2) == 1


def test_classification_improved_to_deal_alerts_once_seen_but_never_alerted():
    # Seen before (e.g. was NOREF/FAIR, never crossed the alert bar), then
    # improves to DEAL -- should alert even though nothing was ever alerted.
    now = datetime.now(timezone.utc)
    hit1 = make_hit(price=100.0, classification="FAIR")
    _, state = notify.select_alerts([], state={})  # start empty
    state[notify.item_key(hit1)] = {"last_price": 100.0}  # seen, never alerted

    hit2 = make_hit(price=90.0, classification="DEAL")
    alerting, state = notify.select_alerts([hit2], state=state, now=now)
    assert len(alerting) == 1


def test_unchanged_deal_does_not_re_alert_within_cooldown():
    hit = make_hit(price=40.0, classification="DEAL")
    now = datetime.now(timezone.utc)
    alerting1, state = notify.select_alerts([hit], state={}, now=now)
    assert len(alerting1) == 1

    # Still DEAL, same price, a few days later -- no re-alert.
    alerting2, state = notify.select_alerts([hit], state=state, now=now + timedelta(days=10))
    assert alerting2 == []


# --- digest formatting ---------------------------------------------------------

def test_build_digest_body_sections_in_order_and_high_last():
    hits = [
        make_hit(classification="HIGH", producer="Roumier"),
        make_hit(classification="DEAL", producer="Labet"),
        make_hit(classification="NOREF", producer="Unknown"),
        make_hit(classification="FAIR", producer="Ganevat"),
    ]
    body = notify.build_digest_body(hits)
    deal_idx = body.index("DEAL")
    fair_idx = body.index("FAIR ")
    noref_idx = body.index("NOREF")
    high_heading_idx = body.index("Flagged as overpriced")
    assert deal_idx < fair_idx < noref_idx < high_heading_idx


def test_caveat_row_gets_asterisk_and_footnote():
    hits = [make_hit(classification="DEAL", caveat=True)]
    body = notify.build_digest_body(hits)
    assert "DEAL*" in body
    assert "reference unverified" in body


def test_no_caveat_no_footnote():
    hits = [make_hit(classification="DEAL", caveat=False)]
    body = notify.build_digest_body(hits)
    assert "DEAL " in body
    assert "reference unverified" not in body


def test_email_capped_at_40_rows():
    hits = [make_hit(classification="DEAL", url=f"https://example.test/{i}") for i in range(50)]
    body = notify.build_digest_body(hits)
    assert body.count("https://example.test/") == 40
    assert "10 more alert-worthy hit(s) omitted" in body


def test_header_row_present():
    body = notify.build_digest_body([make_hit(classification="DEAL")])
    assert "STATUS | Producer | Cuvee | Size | Price | Ref avg | Link" in body


# --- silent run ------------------------------------------------------------------

def test_run_digest_silent_when_nothing_qualifies(tmp_path, capsys):
    state_path = tmp_path / "seen.json"
    hits_path = tmp_path / "hits.json"
    hit = make_hit(classification="FAIR")
    # Pre-seed state so this exact hit is treated as "already alerted, no change".
    state = {notify.item_key(hit): {
        "last_price": hit["price"], "last_alerted_price": hit["price"],
        "last_alerted_at": datetime.now(timezone.utc).isoformat(),
        "last_classification": "FAIR",
    }}
    state_path.write_text(json.dumps(state))

    alerting = notify.run_digest([hit], dry_run=True, state_path=state_path, hits_path=hits_path)

    assert alerting == []
    out = capsys.readouterr().out
    assert "silent run is valid" in out
    assert hits_path.exists()  # full set still written


def test_run_digest_writes_full_hit_set_regardless_of_alerting(tmp_path):
    state_path = tmp_path / "seen.json"
    hits_path = tmp_path / "hits.json"
    hits = [make_hit(classification="DEAL"), make_hit(classification="NOREF", url="https://x/2")]

    notify.run_digest(hits, dry_run=True, state_path=state_path, hits_path=hits_path)

    written = json.loads(hits_path.read_text())
    assert len(written) == 2
