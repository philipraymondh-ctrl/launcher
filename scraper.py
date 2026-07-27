#!/usr/bin/env python3
"""Hourly scraper: checks configured wine shops for named producers, emails on a hit.

Run modes:
  python scraper.py            normal run, sends email via Gmail SMTP on a hit
  DRY_RUN=1 python scraper.py  skips SMTP, prints the would-be email body to stdout
"""
import os
import re
import smtplib
import unicodedata
from email.mime.text import MIMEText
from pathlib import Path

import requests
from bs4 import BeautifulSoup

TIMEOUT = 15
DRY_RUN = os.environ.get("DRY_RUN") == "1"

# ---------------------------------------------------------------------------
# Producers to watch for. Each canonical name maps to alias substrings that
# are matched accent- and case-insensitively (see normalize()). A domaine
# known by more than one name (e.g. Overnoy is run by Houillon) lists both.
# ---------------------------------------------------------------------------
PRODUCERS = {
    "Overnoy/Houillon": ["overnoy", "houillon"],
    "Ganevat": ["ganevat"],
    "Labet": ["labet"],
    "Domaine des Miroirs/Kagami": ["miroirs", "kagami"],
    "Domaine Calice": ["calice"],
    "Clemence Gerbet": ["clemence gerbet", "gerbet"],
    "Thomas Popy": ["thomas popy", "popy"],
    "Roumier": ["roumier"],
}

# ---------------------------------------------------------------------------
# Shops to check. platform is one of "shopify", "woocommerce", "html".
# Shopify shops are queried via /products.json, WooCommerce via the Store
# API, and only shops that support neither fall back to "html" with CSS
# selectors. See CLAUDE.md and the shop-adapter agent before adding a shop.
#
# The three entries below are worked examples (with fixtures in
# tests/fixtures/) showing each adapter shape. Replace with real shops.
# ---------------------------------------------------------------------------
SHOPS = [
    {
        "name": "example-shopify-shop",
        "platform": "shopify",
        "url": "https://example-shopify-shop.example.com",
    },
    {
        "name": "example-woo-shop",
        "platform": "woocommerce",
        "url": "https://example-woo-shop.example.com",
    },
    {
        "name": "example-html-shop",
        "platform": "html",
        "url": "https://example-html-shop.example.com/catalog",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
    },
]


class EmptyResponseError(Exception):
    """Raised when an HTML shop returns empty/near-empty markup, usually a JS-rendered storefront."""


def normalize(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower()


def match_producers(text):
    """Return the canonical producer names whose aliases appear in text."""
    norm = normalize(text)
    matches = []
    for canonical, aliases in PRODUCERS.items():
        if any(normalize(alias) in norm for alias in aliases):
            matches.append(canonical)
    return matches


# Matches a number only when a currency marker is directly adjacent, so a
# bare 4-digit vintage year (e.g. "2018") is never mistaken for a price.
PRICE_PATTERN = re.compile(
    r"(?:[€$£]\s?(\d{1,4}(?:[.,]\d{2})?))"
    r"|(?:(\d{1,4}(?:[.,]\d{2})?)\s?(?:€|EUR|USD|\$))",
    re.IGNORECASE,
)


def parse_price(text):
    match = PRICE_PATTERN.search(text or "")
    if not match:
        return None
    raw = match.group(1) or match.group(2)
    return float(raw.replace(",", "."))


def fetch_shopify(shop):
    resp = requests.get(
        f"{shop['url'].rstrip('/')}/products.json",
        params={"limit": 250},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    items = []
    for product in data.get("products", []):
        title = product.get("title", "")
        text = f"{title} {product.get('vendor', '')} {product.get('body_html', '')}"
        price = None
        variants = product.get("variants") or []
        if variants and variants[0].get("price"):
            price = float(variants[0]["price"])
        url = f"{shop['url'].rstrip('/')}/products/{product.get('handle', '')}"
        items.append({"text": text, "title": title, "price": price, "url": url})
    return items


def fetch_woocommerce(shop):
    resp = requests.get(
        f"{shop['url'].rstrip('/')}/wp-json/wc/store/v1/products",
        params={"per_page": 100},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    items = []
    for product in data:
        name = product.get("name", "")
        text = f"{name} {product.get('short_description', '')} {product.get('description', '')}"
        price = None
        prices = product.get("prices") or {}
        raw_price = prices.get("price")
        if raw_price:
            minor_unit = int(prices.get("currency_minor_unit", 2))
            price = int(raw_price) / (10 ** minor_unit)
        items.append({
            "text": text,
            "title": name,
            "price": price,
            "url": product.get("permalink", shop["url"]),
        })
    return items


def fetch_html(shop):
    resp = requests.get(shop["url"], timeout=TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    if not resp.text.strip():
        raise EmptyResponseError(shop["name"])
    soup = BeautifulSoup(resp.text, "html.parser")
    items = []
    for node in soup.select(shop["item_selector"]):
        title_node = node.select_one(shop["title_selector"])
        price_node = node.select_one(shop["price_selector"])
        title = title_node.get_text(strip=True) if title_node else ""
        price = parse_price(price_node.get_text(strip=True) if price_node else "")
        link_node = node.select_one("a[href]")
        url = link_node["href"] if link_node else shop["url"]
        items.append({
            "text": f"{title} {node.get_text(' ', strip=True)}",
            "title": title,
            "price": price,
            "url": url,
        })
    return items


FETCHERS = {
    "shopify": fetch_shopify,
    "woocommerce": fetch_woocommerce,
    "html": fetch_html,
}


def check_shop(shop):
    items = FETCHERS[shop["platform"]](shop)
    hits = []
    for item in items:
        for producer in match_producers(item["text"]):
            hits.append({
                "shop": shop["name"],
                "producer": producer,
                "title": item["title"],
                "price": item["price"],
                "url": item["url"],
            })
    return hits


def build_email_body(all_hits):
    lines = ["Wine tracker found the following matches:", ""]
    for hit in all_hits:
        price = f"{hit['price']:.2f}" if hit["price"] is not None else "?"
        lines.append(f"- [{hit['shop']}] {hit['producer']}: {hit['title']} ({price}) {hit['url']}")
    return "\n".join(lines)


def send_email(body):
    sender = os.environ["GMAIL_SENDER"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ["NOTIFY_EMAIL"]
    msg = MIMEText(body)
    msg["Subject"] = "Wine tracker: producer match found"
    msg["From"] = sender
    msg["To"] = recipient
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.sendmail(sender, [recipient], msg.as_string())


def main():
    all_hits = []
    error_count = 0
    for shop in SHOPS:
        try:
            hits = check_shop(shop)
            all_hits.extend(hits)
            print(f"[{shop['name']}] ok, {len(hits)} hit(s)")
        except EmptyResponseError:
            error_count += 1
            print(f"[{shop['name']}] empty response (likely JS-rendered storefront)")
        except requests.RequestException as e:
            error_count += 1
            print(f"[{shop['name']}] unreachable: {e}")
        except Exception as e:
            error_count += 1
            print(f"[{shop['name']}] parse error: {e}")

    if not all_hits:
        print("No producer matches this run.")
        if error_count:
            print(f"{error_count} shop(s) had errors this run.")
        return

    body = build_email_body(all_hits)
    if DRY_RUN:
        print("DRY_RUN=1 set, skipping SMTP send. Email body would be:\n")
        print(body)
        return

    send_email(body)
    print(f"Sent alert email with {len(all_hits)} hit(s).")


if __name__ == "__main__":
    main()
