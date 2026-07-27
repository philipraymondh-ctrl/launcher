#!/usr/bin/env python3
"""Turn a submitted issue form into a config change.

GitHub renders issue forms as markdown: a "### Field label" heading per
field, then the value. This parses that back out and edits `PRODUCERS` /
`prices.yaml` / `SHOPS` accordingly.

The design goal is fewest steps for the owner:
  - one form does add AND update (upsert). Fill only the fields you want to
    change and the rest are left alone, so correcting a price is the same
    action as adding a producer.
  - ticking Remove deletes the entry instead.
  - a bulk box takes several producers at once, one per line.
  - marking a price verified stamps today's date; you never type one.

Run from .github/workflows/apply-config.yml, which runs the test suite and
then commits to main. Everything is revertible through git, and a new or
re-pointed shop always lands `verified: false` so it can't go live unprobed.

  python apply_issue.py --kind producer --body-file issue.md
  python apply_issue.py --kind shop --body-file issue.md
"""
import argparse
import datetime as dt
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
SCRAPER_PATH = ROOT / "scraper.py"
PRICES_PATH = ROOT / "prices.yaml"

NO_RESPONSE = "_no response_"
VALID_REGIONS = {"jura", "burgundy", "loire", "beaujolais", "rhone", "alsace", "champagne", "other"}
SHOP_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
PRODUCERS_END = "}\n\n# ---------------------------------------------------------------------------\n# Shops to check."
SHOPS_END = "]\n\n\nclass EmptyResponseError"


class InvalidSubmission(Exception):
    """The form was submitted, but its contents can't be applied safely."""


# --- form parsing ------------------------------------------------------------

def parse_form(body):
    """Return {lowercased field label: value} from an issue-form body."""
    fields, current, lines = {}, None, []
    for line in (body or "").replace("\r\n", "\n").split("\n"):
        heading = re.match(r"^###\s+(.*?)\s*$", line)
        if heading:
            if current:
                fields[current] = "\n".join(lines).strip()
            current, lines = heading.group(1).strip().lower(), []
        elif current:
            lines.append(line)
    if current:
        fields[current] = "\n".join(lines).strip()
    return {
        k: ("" if v.strip().lower() == NO_RESPONSE else v.strip())
        for k, v in fields.items()
    }


def get(fields, *candidates, required=False):
    for key, value in fields.items():
        if any(c in key for c in candidates):
            if required and not value:
                raise InvalidSubmission(f"Field '{key}' is required but was left blank.")
            return value
    if required:
        raise InvalidSubmission(f"Missing required field (looked for: {', '.join(candidates)}).")
    return ""


def checked(value):
    return "[x]" in (value or "").lower()


def checkbox(fields, *phrases):
    """True if a ticked checkbox matches any phrase.

    Checkbox blocks put their text in the *value* ("- [x] Remove this
    producer...") under a generic heading ("Danger zone"), so matching only
    on headings silently misses them -- which it did.
    """
    for key, value in fields.items():
        haystack = f"{key}\n{value}".lower()
        if any(p in haystack for p in phrases):
            return checked(value)
    return False


# --- shared validation --------------------------------------------------------

def safe_literal(value, what):
    if '"' in value or "\\" in value:
        raise InvalidSubmission(f"{what} may not contain quotes or backslashes.")
    return value


def parse_price(raw, what="Reference price"):
    if not raw:
        return None
    cleaned = raw.replace("€", "").replace("EUR", "").replace(",", ".").strip()
    try:
        price = float(cleaned)
    except ValueError:
        raise InvalidSubmission(f"{what} '{raw}' is not a number.")
    if price <= 0:
        raise InvalidSubmission(f"{what} must be greater than zero.")
    return int(price) if price == int(price) else price


def parse_aliases(raw):
    aliases = [a.strip().lower() for a in (raw or "").split(",") if a.strip()]
    for alias in aliases:
        safe_literal(alias, f"Alias '{alias}'")
    return aliases


def parse_region(raw):
    region = (raw or "").strip().lower()
    if region and region not in VALID_REGIONS:
        raise InvalidSubmission(f"Region '{region}' is not one of: {', '.join(sorted(VALID_REGIONS))}.")
    return region


# --- scraper.py source edits ----------------------------------------------------

def _producer_line(name, aliases):
    alias_list = ", ".join('"' + a + '"' for a in aliases)
    return f'    "{name}": [{alias_list}],\n'


def set_producer_in_source(src, name, aliases):
    """Insert or replace this producer's line in the PRODUCERS dict."""
    line = _producer_line(name, aliases)
    existing = re.compile(r'^[ \t]*"' + re.escape(name) + r'":[^\n]*\n', re.M)
    if existing.search(src):
        return existing.sub(lambda _: line, src, count=1)
    if PRODUCERS_END not in src:
        raise InvalidSubmission("Could not locate the end of the PRODUCERS dict in scraper.py.")
    return src.replace(PRODUCERS_END, line + PRODUCERS_END, 1)


def drop_producer_from_source(src, name):
    existing = re.compile(r'^[ \t]*"' + re.escape(name) + r'":[^\n]*\n', re.M)
    if not existing.search(src):
        return src, False
    return existing.sub("", src, count=1), True


def find_shop_block(src, name):
    """Locate a shop's `{ ... },` block in SHOPS by a linear scan.

    Deliberately not a regex: the obvious pattern here nests quantifiers
    that can both match spaces, which backtracks catastrophically on a
    miss and hung the test suite. Scanning lines is O(n) and obvious.
    """
    lines = src.splitlines(keepends=True)
    needle = f'"name": "{name}",'
    for i, line in enumerate(lines):
        if line.strip() != needle:
            continue
        start = i
        while start > 0 and lines[start].strip() != "{":
            start -= 1
        end = i
        while end < len(lines) and lines[end].strip() not in ("},", "}"):
            end += 1
        if lines[start].strip() != "{" or end >= len(lines):
            return None
        offset = sum(len(l) for l in lines[:start])
        return offset, offset + sum(len(l) for l in lines[start:end + 1])
    return None


def drop_shop_from_source(src, name):
    span = find_shop_block(src, name)
    if span is None:
        return src, False
    return src[:span[0]] + src[span[1]:], True


# --- producers -------------------------------------------------------------------

def upsert_producer(name, aliases, region, reference, mark_verified, src, book):
    """Add the producer, or update only the fields that were supplied."""
    name = safe_literal(name.strip(), "Producer name")
    if not name:
        raise InvalidSubmission("Producer name is required.")

    producers = book.setdefault("producers", [])
    entry = next((p for p in producers if p.get("name") == name), None)
    is_new = entry is None

    if is_new:
        if not aliases:
            raise InvalidSubmission(f"'{name}' is new, so it needs at least one alias.")
        if not region:
            raise InvalidSubmission(f"'{name}' is new, so it needs a region.")
        entry = {
            "name": name, "region": region, "reference_750_eur": None,
            "cuvees": [], "last_verified": None, "verified": False,
        }
        producers.append(entry)

    if aliases:
        src = set_producer_in_source(src, name, aliases)
    if region:
        entry["region"] = region
    if reference is not None:
        entry["reference_750_eur"] = reference

    if mark_verified:
        if entry.get("reference_750_eur") is None:
            raise InvalidSubmission(
                f"Can't mark '{name}' verified with no reference price -- supply the figure too."
            )
        entry["verified"] = True
        entry["last_verified"] = dt.date.today().isoformat()

    changes = []
    if aliases:
        changes.append(f"aliases `{', '.join(aliases)}`")
    if region:
        changes.append(f"region `{region}`")
    if reference is not None:
        changes.append(f"reference €{reference}")
    if mark_verified:
        changes.append(f"verified {entry['last_verified']}")
    detail = ", ".join(changes) or "nothing to change"
    return src, book, f"{'Added' if is_new else 'Updated'} producer **{name}** — {detail}."


def remove_producer(name, src, book):
    name = name.strip()
    src, in_source = drop_producer_from_source(src, name)
    producers = book.get("producers", [])
    before = len(producers)
    book["producers"] = [p for p in producers if p.get("name") != name]
    in_book = len(book["producers"]) < before
    if not (in_source or in_book):
        raise InvalidSubmission(f"Producer '{name}' isn't in the config, so there's nothing to remove.")
    return src, book, f"Removed producer **{name}**."


def parse_bulk_producers(raw):
    """One producer per line: Name | aliases | region | price
    (region and price optional)."""
    entries = []
    for lineno, line in enumerate((raw or "").split("\n"), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            raise InvalidSubmission(
                f"Bulk line {lineno} ('{line}') needs at least 'Name | aliases'."
            )
        name = parts[0]
        aliases = parse_aliases(parts[1])
        if not name:
            raise InvalidSubmission(f"Bulk line {lineno} has no producer name.")
        if not aliases:
            raise InvalidSubmission(f"Bulk line {lineno} ('{name}') has no aliases.")
        entries.append({
            "name": name,
            "aliases": aliases,
            "region": parse_region(parts[2]) if len(parts) > 2 and parts[2] else "other",
            "reference": parse_price(parts[3], f"Bulk line {lineno} price") if len(parts) > 3 and parts[3] else None,
        })
    return entries


def handle_producer(fields, src, book):
    if checkbox(fields, "remove this producer"):
        return remove_producer(get(fields, "producer name", "name", required=True), src, book)

    bulk = get(fields, "bulk", "several at once", "one per line")
    if bulk:
        summaries = []
        for e in parse_bulk_producers(bulk):
            src, book, summary = upsert_producer(
                e["name"], e["aliases"], e["region"], e["reference"], False, src, book
            )
            summaries.append(summary)
        if not summaries:
            raise InvalidSubmission("The bulk box had no usable lines.")
        return src, book, "\n".join(f"- {s}" for s in summaries)

    return upsert_producer(
        get(fields, "producer name", "name", required=True),
        parse_aliases(get(fields, "alias")),
        parse_region(get(fields, "region")),
        parse_price(get(fields, "reference", "price")),
        checkbox(fields, "checked myself"),
        src, book,
    )


# --- shops -------------------------------------------------------------------------

def write_shop_fixture(name, url):
    fixture = ROOT / "tests" / "fixtures" / f"{name}.html"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_text(
        f"<!--\n  PLACEHOLDER fixture for {name} ({url}), added via issue form.\n"
        "  Platform and selectors are unconfirmed guesses. Run the Probe Shops\n"
        "  workflow to detect the real platform, then replace this file with a\n"
        "  real saved response before setting verified: true.\n-->\n"
        '<html><body>\n<div class="product">\n'
        f'  <h2 class="product-title">PLACEHOLDER -- replace with a real listing from {name}</h2>\n'
        f'  <a href="{url}">placeholder link</a>\n'
        '  <span class="price">0,00&euro;</span>\n'
        "</div>\n</body></html>\n"
    )


def upsert_shop(name, url, src):
    name = name.strip().lower()
    if not SHOP_NAME_RE.match(name):
        raise InvalidSubmission(
            f"Shop name '{name}' must be 2-40 chars, lowercase letters/digits/hyphens, starting alphanumeric."
        )
    url = url.strip().rstrip("/")
    if not url.startswith("https://"):
        raise InvalidSubmission(f"Shop URL must start with https:// (got '{url}').")
    if '"' in url or "\\" in url or len(url.split()) > 1:
        raise InvalidSubmission("Shop URL contains illegal characters.")

    span = find_shop_block(src, name)
    if span is not None:
        # Re-pointing a shop invalidates whatever was verified about it, so
        # it drops back to unverified until it's probed again.
        block = src[span[0]:span[1]]
        updated = re.sub(r'("url": ")[^"]*(")', lambda m: m.group(1) + url + m.group(2), block, count=1)
        updated = re.sub(r'("verified": )True', lambda m: m.group(1) + "False", updated, count=1)
        src = src[:span[0]] + updated + src[span[1]:]
        return src, f"Updated shop **{name}** → `{url}` (reset to `verified: false`, so re-probe it)."

    if SHOPS_END not in src:
        raise InvalidSubmission("Could not locate the end of the SHOPS list in scraper.py.")
    entry = (
        "    {\n"
        f'        "name": "{name}",\n'
        '        "platform": "html",\n'
        f'        "url": "{url}",\n'
        '        "item_selector": "div.product",\n'
        '        "title_selector": "h2.product-title",\n'
        '        "price_selector": "span.price",\n'
        '        "verified": False,\n'
        "    },\n"
    )
    src = src.replace(SHOPS_END, entry + SHOPS_END, 1)
    write_shop_fixture(name, url)
    return src, (
        f"Added shop **{name}** (`{url}`) as `verified: false`. "
        "Run **Probe Shops** to detect its platform before it goes live."
    )


def remove_shop(name, src):
    name = name.strip().lower()
    src, removed = drop_shop_from_source(src, name)
    if not removed:
        raise InvalidSubmission(f"Shop '{name}' isn't in SHOPS, so there's nothing to remove.")
    for ext in ("html", "json"):
        fixture = ROOT / "tests" / "fixtures" / f"{name}.{ext}"
        if fixture.exists():
            fixture.unlink()
    return src, f"Removed shop **{name}** and its fixture."


def handle_shop(fields, src):
    name = get(fields, "short name", "name", required=True)
    if checkbox(fields, "remove this shop"):
        return remove_shop(name, src)
    return upsert_shop(name, get(fields, "url", required=True), src)


# --- entry point ----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=["producer", "shop"], required=True)
    parser.add_argument("--body-file", required=True)
    args = parser.parse_args()

    fields = parse_form(Path(args.body_file).read_text())
    src = SCRAPER_PATH.read_text()

    if args.kind == "producer":
        with open(PRICES_PATH) as f:
            book = yaml.safe_load(f)
        src, book, summary = handle_producer(fields, src, book)
        SCRAPER_PATH.write_text(src)
        with open(PRICES_PATH, "w") as f:
            yaml.safe_dump(book, f, sort_keys=False, allow_unicode=True, default_flow_style=False)
    else:
        src, summary = handle_shop(fields, src)
        SCRAPER_PATH.write_text(src)

    print(summary)
    Path("apply_summary.txt").write_text(summary)


if __name__ == "__main__":
    main()
