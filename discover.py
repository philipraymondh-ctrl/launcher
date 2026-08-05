#!/usr/bin/env python3
"""Find shops worth adding, and answer the three questions that decide it.

Adding a shop by hand means someone guessing from a search result page, and
the last ten guesses produced nothing: every one failed on a question nobody
had checked. This asks them in order, cheaply, against the real site:

  1. Is it a shop, or a restaurant with a wine list? A restaurant's menu has
     prices and no way to buy, so a price count alone cannot tell them apart.
  2. Does it ship to Denmark? Unanswerable from a search snippet, and the
     reason a shop is worth adding or not.
  3. Does it stock anyone we watch? Cheap to check once the catalogue is in
     hand, and the only thing that makes the other two matter.

What it is not: an Instagram scraper. Hashtag pages are behind a login wall,
the Graph API's hashtag search returns only the last 24 hours and does not
name the account that posted, and automated collection is against Meta's
terms -- the same reason nothing here fetches Wine-Searcher.

Candidates come from a search API (Google Programmable Search, key in the
environment, never committed and never printed), from `--from-page` -- an
importer's stockist list is the single richest source, because these growers
are allocated through one importer per country -- or from `--url` for a
candidate you already have.

Every fetch goes through crawler.Crawler, so robots.txt, the rate limit, the
circuit breaker and the run budget apply exactly as they do to a real scrape.
"""
import argparse
import json
import os
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

import autoselect
import crawler
import scraper
import textnorm

OUTPUT_DIR = Path(os.environ.get("DISCOVER_OUTPUT_DIR",
                                 Path(__file__).parent / "discover_output"))

# How many pages one candidate may cost: the landing page, a catalogue, and a
# shipping page. A discovery pass over thirty candidates must not cost more
# than a crawl of the shops we already have.
MAX_PAGES_PER_CANDIDATE = 3

# --- question 1: is it a shop -------------------------------------------------
#
# The decisive signal is already in this codebase: autoselect.find_products
# returns nothing unless a page carries a repeated priced structure whose
# blocks each hold a product *link*. A restaurant menu has the prices and not
# the links. The markers below only break ties.

CART_PATHS = ("/cart", "/panier", "/kurv", "/checkout", "/warenkorb",
              "/winkelwagen", "/carrello", "/cesta", "/basket", "/commande",
              "/bestellen", "/kassa", "/caisse")
# Booking a table is the clearest thing a restaurant does and a shop does not.
RESTAURANT_PATHS = ("/reservation", "/reserver", "/booking", "/reservieren",
                    "/prenota", "/bord", "/table")
RESTAURANT_HOSTS = ("thefork.", "lafourchette.", "opentable.", "resy.",
                    "quandoo.", "bookatable.", "sevenrooms.")
RESTAURANT_WORDS = ("menu du jour", "carte du jour", "plat du jour", "prix fixe",
                    "reserver une table", "book a table", "tasting menu",
                    "aabningstider", "horaires d'ouverture")
# Enough repeated priced product blocks to be a catalogue rather than a
# "featured bottle" strip. Same threshold autoselect uses for the same reason.
MIN_PRODUCTS = autoselect.MIN_BLOCKS


def _text_of(html):
    return textnorm.strip_accents(
        " ".join(BeautifulSoup(html, "html.parser").stripped_strings))


def _paths_on(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for anchor in soup.find_all("a", href=True):
        target = urljoin(base_url, anchor["href"].strip())
        out.append(target)
    return out


def looks_like_shop(html, base_url):
    """(verdict, reasons). A restaurant with a wine list is the thing to reject.

    The products check does most of the work: a menu lists prices but does not
    link each dish to its own page, so find_products returns nothing on one and
    a grid on the other.
    """
    reasons = []
    products = autoselect.find_products(
        html, base_url, scraper.PRICE_PATTERN, scraper.parse_price)
    if len(products) >= MIN_PRODUCTS:
        reasons.append(f"{len(products)} priced product blocks")

    links = _paths_on(html, base_url)
    lowered = [l.lower() for l in links]
    if any(any(p in l for p in CART_PATHS) for l in lowered):
        reasons.append("has a cart")

    against = []
    if any(any(p in urlparse(l).path for p in RESTAURANT_PATHS) for l in lowered):
        against.append("links a table booking")
    if any(any(h in urlparse(l).netloc for h in RESTAURANT_HOSTS) for l in lowered):
        against.append("links a restaurant booking service")
    text = _text_of(html)
    hit_words = [w for w in RESTAURANT_WORDS if w in text]
    if hit_words:
        against.append(f"restaurant wording ({', '.join(hit_words[:2])})")

    # A wine bar that also sells bottles online is a shop for our purposes, so
    # the booking signals only decide it when nothing says shop.
    is_shop = bool(reasons)
    return is_shop, {"for": reasons, "against": against}


# --- question 2: does it ship to Denmark --------------------------------------

SHIPPING_WORDS = ("livraison", "expedition", "shipping", "delivery", "versand",
                  "verzending", "levering", "fragt", "spedizione", "envio",
                  "lieferung", "bezorging")
DENMARK = ("danmark", "denmark", "danemark", "danimarca", "dinamarca",
           "daenemark")
# A country list that offers Denmark is the same promise as prose saying so.
DENMARK_OPTION = re.compile(r'value\s*=\s*["\'](?:DK|208)["\']', re.I)


def find_shipping_page(html, base_url):
    """The delivery/shipping page, or None. Same host only."""
    host = urlparse(base_url).netloc
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        target = urljoin(base_url, anchor["href"].strip())
        if urlparse(target).netloc != host:
            continue
        label = textnorm.strip_accents(
            f"{anchor.get_text(' ', strip=True)} {target}")
        if any(word in label for word in SHIPPING_WORDS):
            return target
    return None


def ships_to_denmark(html):
    """True / False / None -- and None is a real answer.

    A page that never mentions shipping at all says nothing about Denmark, and
    recording that as "no" would drop a shop for a page we failed to find.
    """
    if html is None:
        return None
    text = _text_of(html)
    if any(marker in text for marker in DENMARK):
        return True
    if DENMARK_OPTION.search(html):
        return True
    if any(word in text for word in SHIPPING_WORDS):
        # It talks about shipping and does not name Denmark.
        return False
    return None


# --- question 3: does it stock anyone we watch --------------------------------

def producers_on(html, base_url):
    """Watched producers named in this page's product listings.

    Deliberately the *listings* rather than the whole page: a blog post about
    Overnoy is not a bottle of Overnoy, and the page text of a shop that sells
    neither still mentions half the Jura in its tag cloud.
    """
    products = autoselect.find_products(
        html, base_url, scraper.PRICE_PATTERN, scraper.parse_price)
    found = set()
    for item in products:
        found.update(scraper.match_producers(item["text"]))
    return sorted(found)


# --- putting a candidate through all three -----------------------------------

def assess(url, crawler_client, catalogue_hint=None):
    """Everything known about one candidate, for at most three requests."""
    result = {"url": url, "host": urlparse(url).netloc, "status": "ok",
              "is_shop": False, "why": {}, "ships_to_denmark": None,
              "producers": [], "pages_read": 0}
    try:
        landing = crawler_client.get(url)
        landing.raise_for_status()
        result["pages_read"] += 1
    except Exception as e:
        result["status"] = f"{type(e).__name__}: {_redact(str(e))}"
        return result

    html = landing.text
    result["is_shop"], result["why"] = looks_like_shop(html, url)
    result["producers"] = producers_on(html, url)

    # The landing page is rarely the catalogue, and both remaining questions
    # are better answered on the catalogue itself.
    catalogue = catalogue_hint or next(
        iter(autoselect.find_catalogue_links(html, url)), None)
    if catalogue and result["pages_read"] < MAX_PAGES_PER_CANDIDATE:
        try:
            page = crawler_client.get(catalogue)
            page.raise_for_status()
            result["pages_read"] += 1
            result["catalogue"] = catalogue
            shop_here, why = looks_like_shop(page.text, catalogue)
            if shop_here:
                result["is_shop"] = True
                result["why"] = why
            result["producers"] = sorted(
                set(result["producers"]) | set(producers_on(page.text, catalogue)))
        except Exception as e:
            result["catalogue_error"] = f"{type(e).__name__}: {_redact(str(e))}"

    shipping = find_shipping_page(html, url)
    if shipping and result["pages_read"] < MAX_PAGES_PER_CANDIDATE:
        try:
            page = crawler_client.get(shipping)
            page.raise_for_status()
            result["pages_read"] += 1
            result["shipping_page"] = shipping
            result["ships_to_denmark"] = ships_to_denmark(page.text)
        except Exception as e:
            result["shipping_error"] = f"{type(e).__name__}: {_redact(str(e))}"
    if result["ships_to_denmark"] is None:
        result["ships_to_denmark"] = ships_to_denmark(html)
    return result


def rank(results):
    """Best first: stocks someone we watch, ships to Denmark, is a shop."""
    def key(r):
        return (
            -len(r["producers"]),
            {True: 0, None: 1, False: 2}[r["ships_to_denmark"]],
            not r["is_shop"],
            r["host"],
        )
    return sorted(results, key=key)


# --- where candidates come from ----------------------------------------------

SEARCH_ENDPOINT = "https://www.googleapis.com/customsearch/v1"


def _redact(text):
    """Never let a search key reach a log.

    The key travels as a query parameter, and this repo's runs are public.
    """
    return re.sub(r"([?&](?:key|cx)=)[^&\s]+", r"\1[redacted]", text or "")


def search_candidates(query, crawler_client, limit=10):
    """Candidate hosts from Google Programmable Search, or [] with a reason.

    A search API is the only sanctioned discovery channel here: reading a
    search engine's HTML results page is against its terms, and Instagram's
    hashtag pages need a login. No key means no search, not a fallback to
    scraping one.
    """
    key, cx = os.environ.get("SEARCH_API_KEY"), os.environ.get("SEARCH_ENGINE_ID")
    if not key or not cx:
        print("No SEARCH_API_KEY/SEARCH_ENGINE_ID set; skipping web search. "
              "Pass --url or --from-page instead.")
        return []
    url = f"{SEARCH_ENDPOINT}?key={key}&cx={cx}&num={min(limit, 10)}&q={query}"
    try:
        resp = crawler_client.get(url)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        print(f"search failed for {query!r}: {type(e).__name__}: {_redact(str(e))}")
        return []
    return [item["link"] for item in payload.get("items", [])][:limit]


def links_from_page(url, crawler_client):
    """Outbound links from a page -- an importer's stockist list.

    These growers are allocated through one importer per country, and the
    importer's own stockist page names the shops that actually receive
    bottles. That is a better candidate list than any search query.
    """
    resp = crawler_client.get(url)
    resp.raise_for_status()
    host = urlparse(url).netloc
    seen, out = set(), []
    for target in _paths_on(resp.text, url):
        parsed = urlparse(target)
        if parsed.scheme not in ("http", "https") or parsed.netloc == host:
            continue
        root = f"{parsed.scheme}://{parsed.netloc}"
        if root not in seen:
            seen.add(root)
            out.append(root)
    return out


def suggested_entry(result):
    """The SHOPS entry to paste, for a candidate worth probing.

    Always unverified: probe.py --apply is what turns a candidate into a shop,
    against a real response, in one run.
    """
    host = result["host"].replace("www.", "")
    name = re.sub(r"[^a-z0-9]", "", host.split(".")[0].lower())
    return {
        "name": name,
        "platform": "html",
        "url": f"https://{result['host']}",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    }


def report(results):
    rows = [("HOST", "SHOP?", "SHIPS DK", "PRODUCERS", "WHY")]
    for r in results:
        rows.append((
            r["host"][:34],
            "yes" if r["is_shop"] else "no",
            {True: "yes", False: "no", None: "unknown"}[r["ships_to_denmark"]],
            ", ".join(r["producers"])[:34] or "-",
            "; ".join(r["why"].get("for") or r["why"].get("against") or
                      [r["status"]])[:46],
        ))
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    lines = [" | ".join(v.ljust(w) for v, w in zip(rows[0], widths))]
    lines.append("-" * len(lines[0]))
    lines += [" | ".join(v.ljust(w) for v, w in zip(row, widths)) for row in rows[1:]]
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", action="append", default=[],
                        help="A candidate shop to assess (repeatable)")
    parser.add_argument("--from-page", action="append", default=[],
                        help="Assess every outbound host linked from this page "
                             "(an importer's stockist list)")
    parser.add_argument("--producer", action="append", default=[],
                        help="Search the web for shops selling this producer "
                             "(needs SEARCH_API_KEY/SEARCH_ENGINE_ID)")
    parser.add_argument("--limit", type=int, default=20,
                        help="Most candidates to assess")
    args = parser.parse_args(argv)

    client = crawler.Crawler()
    candidates = list(args.url)
    for page in args.from_page:
        try:
            candidates += links_from_page(page, client)
        except Exception as e:
            print(f"could not read {page}: {type(e).__name__}: {e}")
    for producer in args.producer:
        query = f'"{producer}" vin naturel boutique en ligne livraison Danemark'
        candidates += search_candidates(query, client)

    seen, ordered = set(), []
    for url in candidates:
        host = urlparse(url).netloc
        if host and host not in seen:
            seen.add(host)
            ordered.append(url)
    ordered = ordered[:args.limit]
    if not ordered:
        print("No candidates. Give --url, --from-page, or set a search key.")
        return 1

    print(f"Assessing {len(ordered)} candidate(s), "
          f"at most {MAX_PAGES_PER_CANDIDATE} request(s) each.")
    results = rank([assess(url, client) for url in ordered])

    for line in report(results):
        print(line)

    worth_probing = [r for r in results
                     if r["is_shop"] and r["ships_to_denmark"] is not False]
    if worth_probing:
        print("\nSHOPS entries worth probing (verified stays False until "
              "probe.py --apply says otherwise):")
        for r in worth_probing:
            print("    " + json.dumps(suggested_entry(r)) + ",")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "candidates.json").write_text(json.dumps(results, indent=2))
    print(f"\nFull report: {OUTPUT_DIR / 'candidates.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
