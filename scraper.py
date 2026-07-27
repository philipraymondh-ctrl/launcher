#!/usr/bin/env python3
"""Hourly scraper: checks configured wine shops for named producers, prices
each hit against prices.yaml, and sends a digest email on anything alert-worthy.

Run modes:
  python scraper.py            normal run, sends a digest email via Gmail SMTP
  DRY_RUN=1 python scraper.py  skips SMTP, prints the would-be digest to stdout

Env vars consumed by the crawl layer (see crawler.py): CONTACT_EMAIL,
MAX_REQUESTS_PER_RUN, FRESH.
"""
import os
import re
import unicodedata

from bs4 import BeautifulSoup

import crawler
import evaluate
import notify

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
# Every entry needs "verified": True before main() will actually query it.
# A shop with "verified": False is skipped at run time with a log line --
# this is the case for a fixture built from a guess (platform assumed, or
# selectors invented) rather than a real saved response. Flip it to True
# only after shop-adapter has confirmed real markup/JSON and replaced the
# fixture with an actual saved response. Never flip it by hand without that.
# ---------------------------------------------------------------------------
SHOPS = [
    # Worked examples (fixtures in tests/fixtures/) showing each adapter
    # shape. Placeholder domains -- not real shops, not meant to run live.
    {
        "name": "example-shopify-shop",
        "platform": "shopify",
        "url": "https://example-shopify-shop.example.com",
        "verified": False,
    },
    {
        "name": "example-woo-shop",
        "platform": "woocommerce",
        "url": "https://example-woo-shop.example.com",
        "verified": False,
    },
    {
        "name": "example-html-shop",
        "platform": "html",
        "url": "https://example-html-shop.example.com/catalog",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },

    # --- Real shops, all UNVERIFIED placeholders -----------------------
    # Added from web research, not a live fetch (this dev environment has
    # no general internet egress). Platform/selectors below are guesses;
    # each has a fixture in tests/fixtures/ documenting what's known vs.
    # invented. shop-adapter must confirm each one against the real site
    # before it's safe to flip "verified" to True.
    {
        "name": "leszinzinsduvin",
        "platform": "html",
        "url": "https://www.leszinzinsduvin.com",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
    {
        "name": "winenot",
        "platform": "html",
        "url": "https://winenot.fr",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
    {
        "name": "vinnouveau",
        "platform": "html",
        "url": "https://vinnouveau.fr",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
    {
        # Platform guess is more confident here: indexed URLs follow
        # Shopify conventions (/en/collections, /en/products/<handle>,
        # /en/cart, /password). Still unverified -- see fixture notes.
        "name": "levinnaturel",
        "platform": "shopify",
        "url": "https://levinnaturel.com",
        "verified": False,
    },
    {
        "name": "lespeauxdevins",
        "platform": "html",
        "url": "https://lespeauxdevins.com",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
    {
        "name": "lacavedespapilles",
        "platform": "html",
        "url": "https://www.lacavedespapilles.com",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
    {
        "name": "vinnaturel",
        "platform": "html",
        "url": "https://www.vinnaturel.fr",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
    {
        "name": "whynat",
        "platform": "html",
        "url": "https://www.whynat.fr",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
    {
        "name": "vinibee",
        "platform": "html",
        "url": "https://www.vinibee.com",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
    {
        "name": "vinscheznous",
        "platform": "html",
        "url": "https://www.vinscheznous.com",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
    {
        "name": "petitescaves",
        "platform": "html",
        "url": "https://www.petitescaves.com",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
    {
        "name": "cavepurjus",
        "platform": "html",
        "url": "https://www.cavepurjus.com",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
    {
        "name": "bbn",
        "platform": "html",
        "url": "https://biobiodynamienature.com",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
    {
        "name": "purewijnen",
        "platform": "html",
        "url": "https://www.purewijnen.be",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
    {
        "name": "amberbottleshop",
        "platform": "html",
        "url": "https://amberbottleshop.com",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
    {
        "name": "naturavin",
        "platform": "html",
        "url": "https://www.naturavin.be",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
    {
        "name": "vinnaturelbe",
        "platform": "html",
        "url": "https://vin-naturel.be",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
    {
        "name": "vinovivo",
        "platform": "html",
        "url": "https://vinovivo.be",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
    {
        "name": "vinifine",
        "platform": "html",
        "url": "https://www.vinifine.be",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
    {
        "name": "zuiverwijnen",
        "platform": "html",
        "url": "https://zuiverwijnen.nl",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
    {
        # Platform guess based on the site's Dutch WooCommerce-default URL
        # slug ("/product-categorie/"). Weak evidence, still unverified.
        "name": "vinopura",
        "platform": "woocommerce",
        "url": "https://vinopura.nl",
        "verified": False,
    },
    {
        "name": "volatilewines",
        "platform": "html",
        "url": "https://volatilewines.com",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
    {
        "name": "biowijnclub",
        "platform": "html",
        "url": "https://www.biowijnclub.nl",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
    {
        "name": "puurwijnshop",
        "platform": "html",
        "url": "https://www.puurwijn.shop",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
    {
        "name": "purovino",
        "platform": "html",
        "url": "https://www.purovino.be",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
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


def fetch_shopify(shop, crawler_client):
    resp = crawler_client.get(f"{shop['url'].rstrip('/')}/products.json", params={"limit": 250})
    resp.raise_for_status()
    data = resp.json()
    items = []
    for product in data.get("products", []):
        title = product.get("title", "")
        text = f"{title} {product.get('vendor', '')} {product.get('body_html', '')}"
        price = None
        variants = product.get("variants") or []
        variant_title = ""
        if variants:
            if variants[0].get("price"):
                price = float(variants[0]["price"])
            variant_title = variants[0].get("title", "") or ""
        url = f"{shop['url'].rstrip('/')}/products/{product.get('handle', '')}"
        items.append({
            "text": text, "title": title, "price": price, "url": url,
            "variant_title": variant_title,
        })
    return items


def fetch_woocommerce(shop, crawler_client):
    resp = crawler_client.get(
        f"{shop['url'].rstrip('/')}/wp-json/wc/store/v1/products", params={"per_page": 100}
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
            "variant_title": "",
        })
    return items


def fetch_html(shop, crawler_client):
    resp = crawler_client.get(shop["url"])
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
            "variant_title": "",
        })
    return items


FETCHERS = {
    "shopify": fetch_shopify,
    "woocommerce": fetch_woocommerce,
    "html": fetch_html,
}


def check_shop(shop, crawler_client):
    items = FETCHERS[shop["platform"]](shop, crawler_client)
    hits = []
    for item in items:
        for producer in match_producers(item["text"]):
            hits.append({
                "shop": shop["name"],
                "producer": producer,
                "title": item["title"],
                "price": item["price"],
                "url": item["url"],
                "variant_title": item.get("variant_title", ""),
            })
    return hits


def main():
    crawler_client = crawler.Crawler()
    all_hits = []
    error_count = 0
    skipped_count = 0
    verified_names = [s["name"] for s in SHOPS if s.get("verified", True)]

    for i, shop in enumerate(SHOPS):
        if not shop.get("verified", True):
            skipped_count += 1
            print(f"[{shop['name']}] skipped: unverified placeholder, needs shop-adapter confirmation")
            continue

        if crawler_client.request_count >= crawler_client.max_requests:
            remaining = [s["name"] for s in SHOPS[i:] if s.get("verified", True)]
            print(
                f"MAX_REQUESTS_PER_RUN ({crawler_client.max_requests}) reached; "
                f"not reached this run: {', '.join(remaining)}"
            )
            break

        try:
            hits = check_shop(shop, crawler_client)
            all_hits.extend(hits)
            print(f"[{shop['name']}] ok, {len(hits)} hit(s)")
        except EmptyResponseError:
            error_count += 1
            print(f"[{shop['name']}] empty response (likely JS-rendered storefront)")
        except crawler.Disallowed as e:
            error_count += 1
            print(f"[{shop['name']}] robots.txt disallows this path, skipped: {e}")
        except crawler.CircuitOpen as e:
            error_count += 1
            print(f"[{shop['name']}] circuit breaker open for this host, skipped: {e}")
        except crawler.BudgetExceeded:
            remaining = [s["name"] for s in SHOPS[i:] if s.get("verified", True)]
            print(
                f"MAX_REQUESTS_PER_RUN ({crawler_client.max_requests}) reached mid-run; "
                f"not reached this run: {', '.join(remaining)}"
            )
            break
        except crawler.UpstreamError as e:
            error_count += 1
            print(f"[{shop['name']}] unreachable: {e}")
        except Exception as e:
            error_count += 1
            print(f"[{shop['name']}] parse error: {e}")

    print(f"{len(all_hits)} raw producer match(es) this run.")
    if error_count:
        print(f"{error_count} shop(s) had errors this run.")
    if skipped_count:
        print(f"{skipped_count} shop(s) skipped as unverified placeholders.")
    if not verified_names:
        print("No verified shops configured -- nothing to evaluate or notify on.")

    evaluated = evaluate.evaluate_hits(all_hits)
    notify.run_digest(evaluated, dry_run=DRY_RUN)


if __name__ == "__main__":
    main()
