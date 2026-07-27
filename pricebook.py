#!/usr/bin/env python3
"""CLI for inspecting prices.yaml.

  python pricebook.py --stale   list producers with verified:false, or
                                 last_verified older than 180 days
"""
import argparse
import datetime as dt
from pathlib import Path

import yaml

PRICES_PATH = Path(__file__).parent / "prices.yaml"
STALE_DAYS = 180


def load_pricebook(path=None):
    with open(path or PRICES_PATH) as f:
        return yaml.safe_load(f)


def _as_date(value):
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value))


def stale_producers(pricebook, today=None):
    """Producers that are verified:false, or verified:true but with no
    last_verified date, or whose last_verified is older than STALE_DAYS."""
    today = today or dt.date.today()
    stale = []
    for producer in pricebook.get("producers", []):
        verified = producer.get("verified", False)
        last_verified = producer.get("last_verified")
        if not verified:
            stale.append(producer)
        elif not last_verified:
            stale.append(producer)
        elif (today - _as_date(last_verified)).days > STALE_DAYS:
            stale.append(producer)
    return stale


def main():
    parser = argparse.ArgumentParser(description="Inspect prices.yaml reference data.")
    parser.add_argument(
        "--stale", action="store_true",
        help="List producers with verified:false or last_verified older than 180 days",
    )
    args = parser.parse_args()

    pricebook = load_pricebook()

    if args.stale:
        stale = stale_producers(pricebook)
        if not stale:
            print("No stale entries.")
            return
        for producer in stale:
            verified = producer.get("verified", False)
            last_verified = producer.get("last_verified") or "never"
            print(f"{producer['name']}: verified={verified}, last_verified={last_verified}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
