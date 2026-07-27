#!/usr/bin/env python3
"""Turn a submitted issue form into a config change, for the workflow to PR.

GitHub renders issue forms as markdown: a "### Field label" heading per
field, then the value. This parses that back out and edits `PRODUCERS` /
`prices.yaml` / `SHOPS` accordingly.

Only ever run from .github/workflows/apply-config.yml, which opens a pull
request with the result -- nothing here writes to main directly, and a new
shop is always added `verified: false` so it cannot go live unprobed.

  python apply_issue.py --kind producer --body-file issue.md
  python apply_issue.py --kind shop --body-file issue.md
"""
import argparse
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
SCRAPER_PATH = ROOT / "scraper.py"
PRICES_PATH = ROOT / "prices.yaml"

NO_RESPONSE = "_no response_"
VALID_REGIONS = {"jura", "burgundy", "loire", "beaujolais", "rhone", "alsace", "champagne", "other"}
SHOP_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")


class InvalidSubmission(Exception):
    """The form was submitted, but its contents can't be applied safely."""


def parse_form(body):
    """Return {lowercased field label: value} from an issue-form body."""
    fields = {}
    current = None
    lines = []
    for line in (body or "").replace("\r\n", "\n").split("\n"):
        heading = re.match(r"^###\s+(.*?)\s*$", line)
        if heading:
            if current:
                fields[current] = "\n".join(lines).strip()
            current = heading.group(1).strip().lower()
            lines = []
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
        for candidate in candidates:
            if candidate in key:
                if required and not value:
                    raise InvalidSubmission(f"Field '{key}' is required but was left blank.")
                return value
    if required:
        raise InvalidSubmission(f"Missing required field (looked for: {', '.join(candidates)}).")
    return ""


def checked(value):
    return "[x]" in (value or "").lower()


# --- producers ---------------------------------------------------------

def add_producer(fields, scraper_src, pricebook):
    name = get(fields, "producer name", "name", required=True)
    aliases_raw = get(fields, "alias", required=True)
    region = (get(fields, "region", required=True) or "").lower()
    reference_raw = get(fields, "reference")
    is_verified = checked(get(fields, "price source", "verified"))

    if '"' in name or "\\" in name:
        raise InvalidSubmission("Producer name may not contain quotes or backslashes.")
    if region not in VALID_REGIONS:
        raise InvalidSubmission(f"Region '{region}' is not one of: {', '.join(sorted(VALID_REGIONS))}.")

    aliases = [a.strip().lower() for a in aliases_raw.split(",") if a.strip()]
    if not aliases:
        raise InvalidSubmission("At least one alias is required.")
    for alias in aliases:
        if '"' in alias or "\\" in alias:
            raise InvalidSubmission(f"Alias '{alias}' may not contain quotes or backslashes.")

    reference = None
    if reference_raw:
        cleaned = reference_raw.replace("€", "").replace("EUR", "").replace(",", ".").strip()
        try:
            reference = float(cleaned)
        except ValueError:
            raise InvalidSubmission(f"Reference price '{reference_raw}' is not a number.")
        if reference <= 0:
            raise InvalidSubmission("Reference price must be greater than zero.")
        if reference == int(reference):
            reference = int(reference)

    if f'"{name}":' in scraper_src:
        raise InvalidSubmission(f"Producer '{name}' is already in PRODUCERS.")

    alias_list = ", ".join(f'"{a}"' for a in aliases)
    marker = "}\n\n# ---------------------------------------------------------------------------\n# Shops to check."
    if marker not in scraper_src:
        raise InvalidSubmission("Could not locate the end of the PRODUCERS dict in scraper.py.")
    scraper_src = scraper_src.replace(
        marker, f'    "{name}": [{alias_list}],\n' + marker, 1
    )

    producers = pricebook.setdefault("producers", [])
    if any(p.get("name") == name for p in producers):
        raise InvalidSubmission(f"Producer '{name}' is already in prices.yaml.")
    producers.append({
        "name": name,
        "region": region,
        "reference_750_eur": reference,
        "cuvees": [],
        "last_verified": None,
        "verified": bool(is_verified and reference is not None),
    })

    note = f"reference €{reference}" if reference is not None else "no reference price (hits will show NOREF)"
    return scraper_src, pricebook, f"Added producer **{name}** ({region}, {note}), aliases: {', '.join(aliases)}."


# --- shops -------------------------------------------------------------

def add_shop(fields, scraper_src):
    name = get(fields, "short name", "name", required=True).strip().lower()
    url = get(fields, "url", required=True).strip().rstrip("/")

    if not SHOP_NAME_RE.match(name):
        raise InvalidSubmission(
            f"Shop name '{name}' must be 2-40 chars, lowercase letters/digits/hyphens, starting alphanumeric."
        )
    if not url.startswith("https://"):
        raise InvalidSubmission(f"Shop URL must start with https:// (got '{url}').")
    if '"' in url or "\\" in url or len(url.split()) > 1:
        raise InvalidSubmission("Shop URL contains illegal characters.")
    if f'"name": "{name}"' in scraper_src:
        raise InvalidSubmission(f"Shop '{name}' is already in SHOPS.")

    # New shops start as html with placeholder selectors and verified:false.
    # The probe corrects the platform; nothing fetches it until then.
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
    marker = "]\n\n\nclass EmptyResponseError"
    if marker not in scraper_src:
        raise InvalidSubmission("Could not locate the end of the SHOPS list in scraper.py.")
    scraper_src = scraper_src.replace(marker, entry + marker, 1)

    fixture = ROOT / "tests" / "fixtures" / f"{name}.html"
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

    return scraper_src, (
        f"Added shop **{name}** (`{url}`) as `verified: false`. "
        "Run the **Probe Shops** workflow to detect its platform before it goes live."
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=["producer", "shop"], required=True)
    parser.add_argument("--body-file", required=True)
    args = parser.parse_args()

    fields = parse_form(Path(args.body_file).read_text())
    scraper_src = SCRAPER_PATH.read_text()

    if args.kind == "producer":
        with open(PRICES_PATH) as f:
            pricebook = yaml.safe_load(f)
        scraper_src, pricebook, summary = add_producer(fields, scraper_src, pricebook)
        SCRAPER_PATH.write_text(scraper_src)
        with open(PRICES_PATH, "w") as f:
            yaml.safe_dump(pricebook, f, sort_keys=False, allow_unicode=True, default_flow_style=False)
    else:
        scraper_src, summary = add_shop(fields, scraper_src)
        SCRAPER_PATH.write_text(scraper_src)

    print(summary)
    Path("apply_summary.txt").write_text(summary)


if __name__ == "__main__":
    main()
