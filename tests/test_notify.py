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


def test_a_price_drop_alerts_inside_the_cooldown():
    """Reversed decision. This used to assert the cooldown won, on the
    reasoning that it stops the digest repeating itself. It does not -- an
    unchanged item never re-alerts either way, because the post-cooldown
    branch also requires news. All the 30-day gate did to a price drop was
    delay it by up to a month, which is how a live tracker goes silent for
    weeks while a wine we already reported halves in price."""
    hit1 = make_hit(price=100.0, classification="FAIR")
    now = datetime.now(timezone.utc)
    alerting1, state = notify.select_alerts([hit1], state={}, now=now)
    assert len(alerting1) == 1

    # One day later, well inside the cooldown, but >10% cheaper.
    hit2 = make_hit(price=85.0, classification="DEAL")
    alerting2, state = notify.select_alerts([hit2], state=state, now=now + timedelta(days=1))
    assert len(alerting2) == 1


def test_a_shallow_price_drop_stays_silent_inside_the_cooldown():
    hit1 = make_hit(price=100.0)
    now = datetime.now(timezone.utc)
    _, state = notify.select_alerts([hit1], state={}, now=now)

    alerting, state = notify.select_alerts(
        [make_hit(price=95.0)], state=state, now=now + timedelta(days=1))
    assert alerting == [], "a 5% move is noise, not news"


def test_a_price_rise_stays_silent_inside_the_cooldown():
    now = datetime.now(timezone.utc)
    _, state = notify.select_alerts([make_hit(price=100.0)], state={}, now=now)
    alerting, _ = notify.select_alerts(
        [make_hit(price=130.0)], state=state, now=now + timedelta(days=1))
    assert alerting == []


def test_each_alert_resets_the_drop_baseline():
    """What stops an hourly re-alert: the comparison is against the price we
    last alerted at, not the original one. A decline alerts once per further
    -10% step, and a round trip alerts not at all."""
    now = datetime.now(timezone.utc)
    _, state = notify.select_alerts([make_hit(price=100.0)], state={}, now=now)

    alerting, state = notify.select_alerts(
        [make_hit(price=85.0)], state=state, now=now + timedelta(hours=1))
    assert len(alerting) == 1                      # baseline is now 85

    alerting, state = notify.select_alerts(
        [make_hit(price=80.0)], state=state, now=now + timedelta(hours=2))
    assert alerting == [], "-6% from the last alert is not another find"

    alerting, state = notify.select_alerts(
        [make_hit(price=76.0)], state=state, now=now + timedelta(hours=3))
    assert len(alerting) == 1

    # Back up to 85 and down to 80 again: nothing, the baseline is 76.
    _, state = notify.select_alerts(
        [make_hit(price=85.0)], state=state, now=now + timedelta(hours=4))
    alerting, _ = notify.select_alerts(
        [make_hit(price=80.0)], state=state, now=now + timedelta(hours=5))
    assert alerting == []


def test_a_classification_improvement_still_waits_for_the_cooldown():
    """Deliberately not relaxed with the price rule. Classification is
    derived from the observed market pool, which moves every hour as other
    shops are crawled, so DEAL -> FAIR -> DEAL flapping is realistic in a
    way a price round trip is not."""
    now = datetime.now(timezone.utc)
    _, state = notify.select_alerts(
        [make_hit(price=100.0, classification="FAIR")], state={}, now=now)

    alerting, _ = notify.select_alerts(
        [make_hit(price=100.0, classification="DEAL")],
        state=state, now=now + timedelta(days=3))
    assert alerting == []


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
    assert "STATUS | Producer | Cuvee | Size | Price | Ref | Basis | Link" in body


# --- silent run ------------------------------------------------------------------

def test_run_digest_silent_when_nothing_qualifies(tmp_path, capsys):
    state_path = tmp_path / "seen.json"
    hits_path = tmp_path / "hits.json"
    hit = make_hit(classification="FAIR")
    # Pre-seed state so this exact hit is treated as "already alerted, no
    # change". last_recap_at is recent too, so this isolates the cooldown
    # path from the weekly recap.
    state = {notify.item_key(hit): {
        "last_price": hit["price"], "last_alerted_price": hit["price"],
        "last_alerted_at": datetime.now(timezone.utc).isoformat(),
        "last_classification": "FAIR",
    }, notify.META_KEY: {"last_recap_at": datetime.now(timezone.utc).isoformat()}}
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


# --- an alert must not be silenced unless it was actually delivered ---------

def test_dry_run_does_not_consume_the_alert(tmp_path, monkeypatch):
    # A dry run that marked items alerted would silence the next real run
    # for the whole cooldown window -- the find would be lost silently.
    state_path, hits_path = tmp_path / "seen.json", tmp_path / "hits.json"
    hit = make_hit(classification="DEAL")

    notify.run_digest([hit], dry_run=True, state_path=state_path, hits_path=hits_path)

    # Nothing persisted, so a subsequent real run still sees it as new.
    sent = {}
    monkeypatch.setattr(notify, "send_email", lambda body, **kw: sent.setdefault("body", body))
    alerting = notify.run_digest([hit], dry_run=False, state_path=state_path, hits_path=hits_path)

    assert len(alerting) == 1
    assert "body" in sent, "the real run must still send after a dry run"


def test_failed_send_does_not_mark_the_item_alerted(tmp_path, monkeypatch):
    state_path, hits_path = tmp_path / "seen.json", tmp_path / "hits.json"
    hit = make_hit(classification="DEAL")

    def boom(body, **kw):
        raise notify.NotConfigured("no credentials")

    monkeypatch.setattr(notify, "send_email", boom)
    with pytest.raises(notify.NotConfigured):
        notify.run_digest([hit], state_path=state_path, hits_path=hits_path)

    # The hits are still recorded, and the item is NOT in cooldown.
    assert json.loads(hits_path.read_text())
    state = notify.load_state(state_path)
    assert state.get(notify.item_key(hit), {}).get("last_alerted_at") is None

    sent = {}
    monkeypatch.setattr(notify, "send_email", lambda body, **kw: sent.setdefault("body", body))
    alerting = notify.run_digest([hit], state_path=state_path, hits_path=hits_path)
    assert len(alerting) == 1 and "body" in sent


def test_successful_send_does_mark_the_item_alerted(tmp_path, monkeypatch):
    state_path, hits_path = tmp_path / "seen.json", tmp_path / "hits.json"
    hit = make_hit(classification="DEAL")
    monkeypatch.setattr(notify, "send_email", lambda body, **kw: None)

    notify.run_digest([hit], state_path=state_path, hits_path=hits_path)
    second = notify.run_digest([hit], state_path=state_path, hits_path=hits_path)

    assert second == [], "an delivered alert must go into cooldown"


def test_missing_credentials_raise_a_legible_error(monkeypatch):
    for key in ("GMAIL_SENDER", "GMAIL_APP_PASSWORD", "NOTIFY_EMAIL"):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(notify.NotConfigured) as excinfo:
        notify.send_email("body")

    message = str(excinfo.value)
    assert "GMAIL_SENDER" in message and "hits.json" in message


# --- the weekly recap: silence must not be ambiguous ---------------------------
#
# Even with news breaking the cooldown, a week can pass with nothing new and
# nothing cheaper. That run is correct to say nothing -- but from the inbox it
# is indistinguishable from expired credentials, a dead adapter or a workflow
# that stopped firing. So: if nothing has been emailed for RECAP_DAYS and
# there are hits, send what we can currently see.


class Recorder:
    """Stands in for SMTP, keeping subjects as well as bodies."""

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def __call__(self, body, subject=None):
        if self.fail:
            raise notify.NotConfigured("no credentials")
        self.calls.append((subject, body))

    @property
    def bodies(self):
        return [b for _, b in self.calls]


def days_ago(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


def seeded_state(hit, alerted_days_ago=1, recap_days_ago=None):
    """State where `hit` was alerted and is now in cooldown with no news."""
    state = {notify.item_key(hit): {
        "last_price": hit["price"],
        "last_alerted_price": hit["price"],
        "last_alerted_at": days_ago(alerted_days_ago),
        "last_classification": hit["classification"],
    }}
    if recap_days_ago is not None:
        state[notify.META_KEY] = {"last_recap_at": days_ago(recap_days_ago)}
    return state


def test_a_week_without_an_email_produces_a_recap(tmp_path, monkeypatch):
    send = Recorder()
    monkeypatch.setattr(notify, "send_email", send)
    hit = make_hit(classification="FAIR")
    state_path, hits_path = tmp_path / "seen.json", tmp_path / "hits.json"
    state_path.write_text(json.dumps(
        seeded_state(hit, alerted_days_ago=8, recap_days_ago=8)))

    alerting = notify.run_digest([hit], state_path=state_path, hits_path=hits_path)

    assert alerting == [], "the recap is not an alert"
    assert len(send.calls) == 1
    assert hit["cuvee"] in send.bodies[0]


def test_a_recap_is_labelled_and_has_its_own_subject(tmp_path, monkeypatch):
    send = Recorder()
    monkeypatch.setattr(notify, "send_email", send)
    hit = make_hit(classification="FAIR")
    state_path, hits_path = tmp_path / "seen.json", tmp_path / "hits.json"
    state_path.write_text(json.dumps(
        seeded_state(hit, alerted_days_ago=8, recap_days_ago=8)))

    notify.run_digest([hit], state_path=state_path, hits_path=hits_path)

    subject, body = send.calls[0]
    assert subject == notify.RECAP_SUBJECT
    assert subject != notify.DIGEST_SUBJECT
    assert "recap" in body.lower()


def test_no_recap_before_the_week_is_up(tmp_path, monkeypatch):
    send = Recorder()
    monkeypatch.setattr(notify, "send_email", send)
    hit = make_hit(classification="FAIR")
    state_path, hits_path = tmp_path / "seen.json", tmp_path / "hits.json"
    state_path.write_text(json.dumps(
        seeded_state(hit, alerted_days_ago=2, recap_days_ago=2)))

    notify.run_digest([hit], state_path=state_path, hits_path=hits_path)

    assert send.calls == [], "an hourly recap would be worse than silence"


def test_a_recap_does_not_put_anything_into_cooldown(tmp_path, monkeypatch):
    """Marking an item alerted is what silences it for 30 days. A recap is
    not a find, so it must leave every item's alert record untouched."""
    send = Recorder()
    monkeypatch.setattr(notify, "send_email", send)
    hit = make_hit(classification="FAIR")
    state_path, hits_path = tmp_path / "seen.json", tmp_path / "hits.json"
    before = seeded_state(hit, alerted_days_ago=8, recap_days_ago=8)
    state_path.write_text(json.dumps(before))

    notify.run_digest([hit], state_path=state_path, hits_path=hits_path)

    entry = notify.load_state(state_path)[notify.item_key(hit)]
    assert entry["last_alerted_at"] == before[notify.item_key(hit)]["last_alerted_at"]
    assert entry["last_alerted_price"] == hit["price"]


def test_a_recap_resets_its_own_clock(tmp_path, monkeypatch):
    send = Recorder()
    monkeypatch.setattr(notify, "send_email", send)
    hit = make_hit(classification="FAIR")
    state_path, hits_path = tmp_path / "seen.json", tmp_path / "hits.json"
    state_path.write_text(json.dumps(
        seeded_state(hit, alerted_days_ago=8, recap_days_ago=8)))

    notify.run_digest([hit], state_path=state_path, hits_path=hits_path)
    notify.run_digest([hit], state_path=state_path, hits_path=hits_path)

    assert len(send.calls) == 1, "the recap repeated itself on the next run"


def test_a_real_digest_also_resets_the_weekly_clock(tmp_path, monkeypatch):
    """The promise is 'you hear from it at least weekly', not 'you get an
    extra email weekly'."""
    send = Recorder()
    monkeypatch.setattr(notify, "send_email", send)
    state_path, hits_path = tmp_path / "seen.json", tmp_path / "hits.json"
    state_path.write_text(json.dumps({notify.META_KEY: {"last_recap_at": days_ago(30)}}))

    notify.run_digest([make_hit(classification="DEAL")],
                      state_path=state_path, hits_path=hits_path)

    assert len(send.calls) == 1, "a digest and a recap went out for the same run"
    assert send.calls[0][0] == notify.DIGEST_SUBJECT
    meta = notify.load_state(state_path)[notify.META_KEY]
    assert (datetime.now(timezone.utc) - notify._parse_iso(meta["last_recap_at"])).days == 0


def test_nothing_to_report_stays_silent_however_long_it_has_been(tmp_path, monkeypatch):
    """The weekly clock is not a heartbeat for the workflow. With no hits
    there is nothing to recap."""
    send = Recorder()
    monkeypatch.setattr(notify, "send_email", send)
    state_path, hits_path = tmp_path / "seen.json", tmp_path / "hits.json"
    state_path.write_text(json.dumps({notify.META_KEY: {"last_recap_at": days_ago(99)}}))

    notify.run_digest([], state_path=state_path, hits_path=hits_path)

    assert send.calls == []


def test_a_first_ever_run_with_no_state_does_not_double_email(tmp_path, monkeypatch):
    send = Recorder()
    monkeypatch.setattr(notify, "send_email", send)
    state_path, hits_path = tmp_path / "seen.json", tmp_path / "hits.json"

    notify.run_digest([make_hit(classification="DEAL")],
                      state_path=state_path, hits_path=hits_path)

    assert len(send.calls) == 1


def test_a_dry_run_recap_sends_nothing_and_persists_nothing(tmp_path, monkeypatch, capsys):
    send = Recorder()
    monkeypatch.setattr(notify, "send_email", send)
    hit = make_hit(classification="FAIR")
    state_path, hits_path = tmp_path / "seen.json", tmp_path / "hits.json"
    state = seeded_state(hit, alerted_days_ago=8, recap_days_ago=8)
    state_path.write_text(json.dumps(state))

    notify.run_digest([hit], dry_run=True, state_path=state_path, hits_path=hits_path)

    assert send.calls == []
    assert notify.load_state(state_path) == state, "a dry run consumed the recap"
    assert "recap" in capsys.readouterr().out.lower()


def test_a_failed_recap_send_does_not_reset_the_clock(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "send_email", Recorder(fail=True))
    hit = make_hit(classification="FAIR")
    state_path, hits_path = tmp_path / "seen.json", tmp_path / "hits.json"
    state = seeded_state(hit, alerted_days_ago=8, recap_days_ago=8)
    state_path.write_text(json.dumps(state))

    with pytest.raises(notify.NotConfigured):
        notify.run_digest([hit], state_path=state_path, hits_path=hits_path)

    assert notify.load_state(state_path) == state


def test_the_recap_carries_the_run_notes(tmp_path, monkeypatch):
    """Drift and missing producers are exactly what someone reads a recap
    for."""
    send = Recorder()
    monkeypatch.setattr(notify, "send_email", send)
    hit = make_hit(classification="FAIR")
    state_path, hits_path = tmp_path / "seen.json", tmp_path / "hits.json"
    state_path.write_text(json.dumps(
        seeded_state(hit, alerted_days_ago=8, recap_days_ago=8)))

    notify.run_digest([hit], state_path=state_path, hits_path=hits_path,
                      notes={"Shops that returned nothing": ["mareehaute"]})

    assert "mareehaute" in send.bodies[0]


def test_the_meta_key_can_never_collide_with_an_item():
    """seen.json is keyed by sha256 hex, so a reserved non-hex key is safe."""
    assert not all(c in "0123456789abcdef" for c in notify.META_KEY)
    key = notify.item_key(make_hit())
    assert len(key) == 64 and key != notify.META_KEY


def test_select_alerts_never_writes_the_meta_key():
    _, state = notify.select_alerts([make_hit()], state={})
    assert notify.META_KEY not in state


def test_state_that_predates_the_recap_gets_one_promptly(tmp_path, monkeypatch):
    """The state carried in the Actions cache has no recap clock, so the
    first run after this lands has nothing to measure a week from. Sending
    the recap is the right reading: that state is exactly the situation the
    recap exists for -- a shelf full of hits, all in cooldown, silent for
    days."""
    send = Recorder()
    monkeypatch.setattr(notify, "send_email", send)
    hit = make_hit(classification="FAIR")
    state_path, hits_path = tmp_path / "seen.json", tmp_path / "hits.json"
    state_path.write_text(json.dumps(seeded_state(hit, alerted_days_ago=3)))

    notify.run_digest([hit], state_path=state_path, hits_path=hits_path)

    assert len(send.calls) == 1
    assert send.calls[0][0] == notify.RECAP_SUBJECT


def test_a_dry_run_with_nothing_to_say_writes_no_state(tmp_path, monkeypatch):
    """A dry run leaves no trace on every path, not just the sending one.
    The silent branch used to refresh last_price even under DRY_RUN."""
    monkeypatch.setattr(notify, "send_email", Recorder())
    hit = make_hit(classification="FAIR")
    state_path, hits_path = tmp_path / "seen.json", tmp_path / "hits.json"
    state = seeded_state(hit, alerted_days_ago=1, recap_days_ago=1)
    state[notify.item_key(hit)]["last_price"] = 999.0   # a save would fix this
    state_path.write_text(json.dumps(state))

    alerting = notify.run_digest([hit], dry_run=True, state_path=state_path,
                                 hits_path=hits_path)

    assert alerting == []
    assert notify.load_state(state_path) == state


# --- a run the owner asked for must always answer -------------------------------
#
# The failure this fixes, from the log of a button press:
#
#   51 raw producer match(es) this run.
#   No newly alert-worthy hits this run (cooldown or no change) -- silent run
#   is valid.
#
# Correct, and useless. The hourly schedule should stay quiet when there is no
# news, but a human who presses "Run scraper" and gets nothing back cannot
# tell that from expired credentials, and has twice now concluded the thing
# had stopped working.


def all_quiet(hit):
    """Everything alerted recently, and the weekly recap not due either --
    the exact state in which a button press currently says nothing."""
    return seeded_state(hit, alerted_days_ago=1, recap_days_ago=1)


def test_a_run_the_owner_asked_for_always_reports(tmp_path, monkeypatch):
    send = Recorder()
    monkeypatch.setattr(notify, "send_email", send)
    hit = make_hit(classification="FAIR")
    state_path, hits_path = tmp_path / "seen.json", tmp_path / "hits.json"
    state_path.write_text(json.dumps(all_quiet(hit)))

    alerting = notify.run_digest([hit], state_path=state_path,
                                 hits_path=hits_path, force=True)

    assert alerting == [], "a forced report is not a set of new finds"
    assert len(send.calls) == 1
    assert hit["cuvee"] in send.bodies[0]


def test_the_same_state_stays_silent_on_a_scheduled_run(tmp_path, monkeypatch):
    """The other half: hourly runs must not start emailing every hour."""
    send = Recorder()
    monkeypatch.setattr(notify, "send_email", send)
    hit = make_hit(classification="FAIR")
    state_path, hits_path = tmp_path / "seen.json", tmp_path / "hits.json"
    state_path.write_text(json.dumps(all_quiet(hit)))

    notify.run_digest([hit], state_path=state_path, hits_path=hits_path)

    assert send.calls == []


def test_a_forced_report_marks_nothing_alerted(tmp_path, monkeypatch):
    """Same rule as the recap: it is not a find, and marking one would
    silence a real price drop for 30 days."""
    send = Recorder()
    monkeypatch.setattr(notify, "send_email", send)
    hit = make_hit(classification="FAIR")
    state_path, hits_path = tmp_path / "seen.json", tmp_path / "hits.json"
    before = all_quiet(hit)
    state_path.write_text(json.dumps(before))

    notify.run_digest([hit], state_path=state_path, hits_path=hits_path, force=True)

    entry = notify.load_state(state_path)[notify.item_key(hit)]
    assert entry["last_alerted_at"] == before[notify.item_key(hit)]["last_alerted_at"]


def test_a_forced_report_answers_even_with_nothing_to_show(tmp_path, monkeypatch):
    """The one place silence is worse than an empty table. A scheduled run
    with no hits still sends nothing; a run someone asked for says so."""
    send = Recorder()
    monkeypatch.setattr(notify, "send_email", send)
    state_path, hits_path = tmp_path / "seen.json", tmp_path / "hits.json"

    notify.run_digest([], state_path=state_path, hits_path=hits_path, force=True,
                      notes={"Watched but found nowhere": ["Ganevat"]})

    assert len(send.calls) == 1
    assert "Ganevat" in send.bodies[0]


def test_a_forced_report_says_which_kind_of_email_it_is(tmp_path, monkeypatch):
    send = Recorder()
    monkeypatch.setattr(notify, "send_email", send)
    hit = make_hit(classification="FAIR")
    state_path, hits_path = tmp_path / "seen.json", tmp_path / "hits.json"
    state_path.write_text(json.dumps(all_quiet(hit)))

    notify.run_digest([hit], state_path=state_path, hits_path=hits_path, force=True)

    subject, body = send.calls[0]
    assert subject == notify.ONDEMAND_SUBJECT
    assert subject not in (notify.DIGEST_SUBJECT, notify.RECAP_SUBJECT)
    assert "asked for" in body.lower() or "on demand" in body.lower()


def test_a_forced_run_with_real_news_sends_the_digest_not_two_emails(tmp_path, monkeypatch):
    send = Recorder()
    monkeypatch.setattr(notify, "send_email", send)
    state_path, hits_path = tmp_path / "seen.json", tmp_path / "hits.json"

    notify.run_digest([make_hit(classification="DEAL")], state_path=state_path,
                      hits_path=hits_path, force=True)

    assert len(send.calls) == 1
    assert send.calls[0][0] == notify.DIGEST_SUBJECT


def test_a_forced_report_resets_the_weekly_clock(tmp_path, monkeypatch):
    """It is an email the owner received, so the recap should not follow it
    a day later."""
    send = Recorder()
    monkeypatch.setattr(notify, "send_email", send)
    hit = make_hit(classification="FAIR")
    state_path, hits_path = tmp_path / "seen.json", tmp_path / "hits.json"
    state_path.write_text(json.dumps(
        seeded_state(hit, alerted_days_ago=1, recap_days_ago=30)))

    notify.run_digest([hit], state_path=state_path, hits_path=hits_path, force=True)
    notify.run_digest([hit], state_path=state_path, hits_path=hits_path)

    assert len(send.calls) == 1


def test_a_forced_dry_run_still_sends_nothing(tmp_path, monkeypatch):
    send = Recorder()
    monkeypatch.setattr(notify, "send_email", send)
    hit = make_hit(classification="FAIR")
    state_path, hits_path = tmp_path / "seen.json", tmp_path / "hits.json"
    state = all_quiet(hit)
    state_path.write_text(json.dumps(state))

    notify.run_digest([hit], dry_run=True, state_path=state_path,
                      hits_path=hits_path, force=True)

    assert send.calls == []
    assert notify.load_state(state_path) == state
