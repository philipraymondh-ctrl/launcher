"""Notification hygiene: one digest email per run, sha256-keyed cooldown
state in seen.json, and the full evaluated hit set in hits.json for the
workflow artifact.

Never one email per hit. A run where nothing newly qualifies sends nothing
and exits 0 -- a silent run is a valid run, not a failure.
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
    """new item -> always. Otherwise: a 30-day cooldown blocks re-alerting
    the same item, except a brand-new item (no prior record) has nothing to
    be "within cooldown" of. Past cooldown, alert only on a >10% price drop
    since the last alert, or a classification improving to DEAL."""
    if prev is None:
        return True

    last_alerted_at = prev.get("last_alerted_at")
    if last_alerted_at and (now - _parse_iso(last_alerted_at)).days < COOLDOWN_DAYS:
        return False

    last_alerted_price = prev.get("last_alerted_price")
    price = hit.get("price")
    price_dropped = (
        last_alerted_price is not None
        and price is not None
        and price <= last_alerted_price * (1 - PRICE_DROP_THRESHOLD)
    )
    classification_improved_to_deal = (
        hit.get("classification") == "DEAL" and prev.get("last_classification") != "DEAL"
    )
    return price_dropped or classification_improved_to_deal


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
    return f"{status:<5} | {producer} | {cuvee} | {size} | {price} | {ref} | {hit.get('url', '')}"


def build_digest_body(alerting_hits):
    ordered = []
    for section in SECTION_ORDER:
        ordered.extend(h for h in alerting_hits if h.get("classification") == section)
    shown = ordered[:EMAIL_ROW_CAP]

    lines = ["STATUS | Producer | Cuvee | Size | Price | Ref avg | Link", ""]
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
        lines.append(f"... {len(ordered) - EMAIL_ROW_CAP} more alert-worthy hit(s) omitted; see hits.json")

    if has_caveat:
        lines.append("* reference unverified or size/tier confidence low -- treat with caution")

    return "\n".join(lines).rstrip() + "\n"


def send_email(body):
    sender = os.environ["GMAIL_SENDER"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ["NOTIFY_EMAIL"]
    msg = MIMEText(body)
    msg["Subject"] = "Wine tracker digest"
    msg["From"] = sender
    msg["To"] = recipient
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.sendmail(sender, [recipient], msg.as_string())


def write_hits_json(all_hits, path=None):
    path = path or HITS_PATH
    path.write_text(json.dumps(all_hits, indent=2, sort_keys=True, default=str))


def run_digest(all_hits, dry_run=False, state_path=None, hits_path=None):
    """Full pipeline: decide alerts, persist cooldown state, write the full
    hit set to hits.json, then send (or print) at most one digest email.
    Returns the list of alerting hits."""
    state = load_state(state_path)
    alerting, state = select_alerts(all_hits, state)
    save_state(state, state_path)
    write_hits_json(all_hits, hits_path)

    if not alerting:
        print("No newly alert-worthy hits this run (cooldown or no change) -- silent run is valid.")
        return alerting

    body = build_digest_body(alerting)
    if dry_run:
        print("DRY_RUN=1 set, skipping SMTP send. Digest email would be:\n")
        print(body)
        return alerting

    send_email(body)
    print(f"Sent digest email with {len(alerting)} alert-worthy hit(s).")
    return alerting
