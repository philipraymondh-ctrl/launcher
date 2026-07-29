"""Notification hygiene: one digest email per run, sha256-keyed cooldown
state in seen.json, and the full evaluated hit set in hits.json for the
workflow artifact.

Never one email per hit. A run where nothing newly qualifies sends nothing
and exits 0 -- a silent run is a valid run, not a failure. But a whole week
of them is not distinguishable from a broken one from the inbox, so after
RECAP_DAYS of quiet the run sends a recap of what it can currently see.
"""
import hashlib
import json
import os
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

STATE_PATH = Path(os.environ.get("SEEN_STATE_PATH", Path(__file__).parent / "seen.json"))
HITS_PATH = Path(os.environ.get("HITS_OUTPUT_PATH", Path(__file__).parent / "hits.json"))

COOLDOWN_DAYS = 30
PRICE_DROP_THRESHOLD = 0.10
EMAIL_ROW_CAP = 40
SECTION_ORDER = ["DEAL", "FAIR", "NOREF", "HIGH"]

# How long the tracker may stay silent before it says so unprompted. A run
# with no news is right to send nothing, but from the inbox that is
# indistinguishable from expired credentials or a dead adapter.
RECAP_DAYS = 7
DIGEST_SUBJECT = "Wine tracker digest"
RECAP_SUBJECT = "Wine tracker weekly recap"
# seen.json is keyed by sha256 hex, so a non-hex key cannot collide with an
# item, and select_alerts only ever writes keys it computed itself.
META_KEY = "_meta"


def item_key(hit):
    """sha256(shop + product_url + variant) -- stable identity for a single
    listing, distinct across shops/variants even if the same wine appears
    at more than one shop or in more than one bottle size."""
    raw = f"{hit.get('shop', '')}|{hit.get('url', '')}|{hit.get('variant_title', '')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_state(path=None):
    path = path or STATE_PATH
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state, path=None):
    path = path or STATE_PATH
    path.write_text(json.dumps(state, indent=2, sort_keys=True))


def _parse_iso(ts):
    return datetime.fromisoformat(ts)


def should_alert(hit, prev, now):
    """new item -> always. A >10% drop since the last alert -> always, too:
    the cooldown is there to stop the digest repeating itself, and a price
    drop is not a repeat. Otherwise the 30-day cooldown blocks re-alerting,
    and past it only a classification improving to DEAL qualifies.

    The drop rule ignoring the cooldown is deliberate and was once the other
    way round. An *unchanged* item never re-alerts either way -- the
    post-cooldown branch also requires news -- so all the 30-day gate ever
    did to a price drop was delay it by up to a month, which is how a live
    tracker goes quiet for weeks while a wine it already reported halves in
    price. Nothing runs away as a result: the comparison is against
    `last_alerted_price`, which every alert resets, so a decline alerts once
    per further -10% step and a round trip alerts not at all.

    Classification stays behind the cooldown on purpose. It is derived from
    the observed market pool, which moves every hour as other shops are
    crawled, so DEAL -> FAIR -> DEAL flapping is realistic in a way a price
    round trip is not."""
    if prev is None:
        return True

    last_alerted_price = prev.get("last_alerted_price")
    price = hit.get("price")
    price_dropped = (
        last_alerted_price is not None
        and price is not None
        and price <= last_alerted_price * (1 - PRICE_DROP_THRESHOLD)
    )
    if price_dropped:
        return True

    last_alerted_at = prev.get("last_alerted_at")
    if last_alerted_at and (now - _parse_iso(last_alerted_at)).days < COOLDOWN_DAYS:
        return False

    return (hit.get("classification") == "DEAL"
            and prev.get("last_classification") != "DEAL")


def _update_state(state, hit, key, alerted, now):
    entry = dict(state.get(key, {}))
    entry["last_price"] = hit.get("price")
    if alerted:
        entry["last_alerted_price"] = hit.get("price")
        entry["last_alerted_at"] = now.isoformat()
        entry["last_classification"] = hit.get("classification")
    state[key] = entry
    return state


def select_alerts(hits, state=None, now=None):
    """Decide which hits are alert-worthy this run, and return the updated
    state. last_price is refreshed for every hit seen, alerted or not, so
    future price-drop comparisons stay accurate."""
    now = now or datetime.now(timezone.utc)
    state = {} if state is None else dict(state)
    alerting = []
    for hit in hits:
        key = item_key(hit)
        prev = state.get(key)
        alert = should_alert(hit, prev, now)
        if alert:
            alerting.append(hit)
        state = _update_state(state, hit, key, alert, now)
    return alerting, state


def format_row(hit):
    status = hit.get("classification", "NOREF") + ("*" if hit.get("caveat") else "")
    price = f"EUR {hit['price']:.0f}" if hit.get("price") is not None else "EUR ?"
    ref = f"EUR {hit['expected_price']:.0f}" if hit.get("expected_price") is not None else "EUR ?"
    size = hit.get("size_label") or f"{hit.get('size_ml', 750)}ml"
    cuvee = hit.get("cuvee") or hit.get("title", "")
    producer = hit.get("producer", "")
    # The alias that fired is the whole diagnosis for a misattribution --
    # three estates were reported under the wrong producer, each caught only
    # by someone recognising the name and opening the shop.
    alias = hit.get("matched_alias")
    if alias:
        producer = f"{producer} [{alias}]"
    # Where the reference came from is the difference between "cheaper than
    # three other shops" and "cheaper than a number someone guessed once".
    basis = hit.get("reference_basis") or "no reference"
    return (f"{status:<5} | {producer} | {cuvee} | {size} | {price} | {ref} | "
            f"{basis} | {hit.get('url', '')}")


def recap_due(state, now, every_days=RECAP_DAYS):
    """True when nothing has been emailed for `every_days`. The clock is
    reset by any digest, not only by a recap -- the promise is "you hear from
    it at least weekly", not "you get an extra email weekly"."""
    last = (state.get(META_KEY) or {}).get("last_recap_at")
    if not last:
        return True
    return (now - _parse_iso(last)).total_seconds() >= every_days * 86400


def _stamp_recap(state, now):
    meta = dict(state.get(META_KEY) or {})
    meta["last_recap_at"] = now.isoformat()
    state[META_KEY] = meta
    return state


def build_digest_body(alerting_hits, notes=None, recap=False):
    ordered = []
    for section in SECTION_ORDER:
        ordered.extend(h for h in alerting_hits if h.get("classification") == section)
    shown = ordered[:EMAIL_ROW_CAP]

    lines = []
    if recap:
        lines += [
            f"Weekly recap. Nothing new and nothing more than "
            f"{PRICE_DROP_THRESHOLD:.0%} cheaper in the last {RECAP_DAYS} days, so "
            f"this is everything currently matched rather than a set of fresh "
            f"finds -- and confirmation that the tracker is still running.",
            "",
        ]
    lines += ["STATUS | Producer | Cuvee | Size | Price | Ref | Basis | Link", ""]
    has_caveat = False
    for section in SECTION_ORDER:
        items = [h for h in shown if h.get("classification") == section]
        if not items:
            continue
        heading = "Flagged as overpriced" if section == "HIGH" else section
        lines.append(heading)
        for hit in items:
            lines.append(format_row(hit))
            has_caveat = has_caveat or bool(hit.get("caveat"))
        lines.append("")

    if len(ordered) > EMAIL_ROW_CAP:
        kind = "matched" if recap else "alert-worthy"
        lines.append(f"... {len(ordered) - EMAIL_ROW_CAP} more {kind} hit(s) omitted; see hits.json")

    if has_caveat:
        lines.append("* reference unverified or size/tier confidence low -- treat with caution")

    # Notes are how a silent failure reaches the person rather than only the
    # run log. Rendered only when non-empty, so a clean run gains nothing.
    for heading, names in (notes or {}).items():
        if names:
            lines.append("")
            lines.append(f"{heading} ({len(names)}): {', '.join(names)}")

    return "\n".join(lines).rstrip() + "\n"


class NotConfigured(Exception):
    """SMTP credentials are missing, so the digest cannot be delivered."""


def send_email(body, subject=DIGEST_SUBJECT):
    missing = [k for k in ("GMAIL_SENDER", "GMAIL_APP_PASSWORD", "NOTIFY_EMAIL") if not os.environ.get(k)]
    if missing:
        raise NotConfigured(
            f"Cannot send the digest: {', '.join(missing)} not set. "
            "Add them under Settings > Secrets and variables > Actions. "
            "The hits are still in hits.json, and nothing has been marked "
            "as alerted, so they will be re-reported on the next run."
        )
    sender = os.environ["GMAIL_SENDER"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ["NOTIFY_EMAIL"]
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.sendmail(sender, [recipient], msg.as_string())


def write_hits_json(all_hits, path=None):
    path = path or HITS_PATH
    path.write_text(json.dumps(all_hits, indent=2, sort_keys=True, default=str))


def run_digest(all_hits, dry_run=False, state_path=None, hits_path=None,
               notes=None, now=None):
    """Full pipeline: decide alerts, write the full hit set to hits.json,
    send at most one email, and only then persist the cooldown state.
    Returns the list of alerting hits.

    Ordering matters. Marking an item "alerted" is what silences it for the
    next 30 days, so it must happen only once the email has actually gone
    out. Saving first meant a dry run consumed the alert -- the following
    real run would find it in cooldown and say nothing -- and a failed send
    (missing SMTP credentials, Gmail down) discarded the find entirely.
    Both are silent misses, which is the failure this scraper exists to
    avoid.

    With nothing to alert on, one of two things happens. Usually: silence,
    which is a valid run. But if nothing has been emailed for RECAP_DAYS,
    the run sends a recap of everything currently matched instead --
    otherwise a correct week of quiet looks exactly like a broken one. A
    recap marks nothing as alerted; it is not a find.
    """
    now = now or datetime.now(timezone.utc)
    state = load_state(state_path)
    alerting, updated_state = select_alerts(all_hits, state, now)
    write_hits_json(all_hits, hits_path)

    if alerting:
        body, subject = build_digest_body(alerting, notes), DIGEST_SUBJECT
    elif all_hits and recap_due(state, now):
        body, subject = build_digest_body(all_hits, notes, recap=True), RECAP_SUBJECT
    else:
        # Nothing was alerted, so nothing is being silenced; persisting here
        # just refreshes last_price for future drop comparisons.
        if not dry_run:
            save_state(updated_state, state_path)
        print("No newly alert-worthy hits this run (cooldown or no change) -- silent run is valid.")
        return alerting

    if dry_run:
        print("DRY_RUN=1 set, skipping SMTP send and leaving state untouched.")
        print(f"{subject} would be:\n")
        print(body)
        return alerting

    send_email(body, subject=subject)
    # The recap clock is reset by whichever email went out, so a digest and a
    # recap can never both fire for the same stretch of quiet.
    save_state(_stamp_recap(updated_state, now), state_path)
    if alerting:
        print(f"Sent digest email with {len(alerting)} alert-worthy hit(s).")
    else:
        print(f"Nothing new for {RECAP_DAYS} days; sent a recap of "
              f"{len(all_hits)} currently matched hit(s).")
    return alerting
