#!/usr/bin/env python3
"""Find the product listings on a page nobody has written selectors for.

Nine of the shops serve ordinary HTML and match none of the generic
`div.product` / `h2.product-title` guesses in SHOPS, because their markup
is their own: leszinzinsduvin is hand-rolled PHP serving
`/vin-2669-alsace_Rouge_..._Pierre_Andrey.html`, and nothing about that
resembles WooCommerce. Writing nine bespoke selector sets is nine things
to maintain, and each one breaks silently the next time a shop restyles.

So the structure is derived instead of declared, from the one thing every
shop listing has in common: a repeated block containing a price and a link
to the product. Concretely --

  1. find every element whose own text carries a currency-adjacent price
     (the same rule as scraper.PRICE_PATTERN -- a bare 4-digit number is a
     vintage, not a price),
  2. climb from each until the ancestor also holds a product link, and stop
     before it swallows a second price, which would mean the whole grid,
  3. group those blocks by their shared parent: items in a listing are
     siblings,
  4. the parent holding the most of them is the catalogue.

This gives a shop adapter for free when it works, and nothing at all when
it doesn't -- `find_products` returns an empty list rather than guessing,
which the caller reports as a shop needing real selectors.
"""
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

# Fewer repeats than this is a "featured wine" box or a related-items
# strip, not a catalogue.
MIN_BLOCKS = 3
# How far to climb from a price before giving up on finding a link.
MAX_CLIMB = 6

NON_PRODUCT_HREF = re.compile(r"^(#|javascript:|mailto:|tel:)", re.I)
# Links every shop has that are never a product.
NON_PRODUCT_PATH = re.compile(
    r"/(cart|panier|basket|checkout|account|compte|login|connexion|register|"
    r"search|recherche|contact|cgv|mentions|privacy|blog|news|actualites)\b", re.I
)


def _own_text(element):
    """Text belonging to this element rather than its descendants, so a
    <body> is not reported as 'containing a price'."""
    return " ".join(t for t in element.find_all(string=True, recursive=False)).strip()


def _price_nodes(soup, price_pattern):
    return [el for el in soup.find_all(True) if price_pattern.search(_own_text(el))]


def _product_link(element):
    for anchor in element.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or NON_PRODUCT_HREF.match(href) or NON_PRODUCT_PATH.search(href):
            continue
        return anchor
    return None


def _block_for(node, price_pattern):
    """The widest ancestor that still describes exactly one product.

    Climbing only as far as the first ancestor holding a link is not
    enough. A table-based catalogue puts the link and the price in the same
    <td>, so that rule stops at the cell and every product's "parent" is
    its own <tr> -- each group has one member and the grid is never found.
    Climbing on while the ancestor holds one price stops at the <tr>
    instead, and all the rows then share the <table> as their parent.
    """
    current, widest = node, None
    for _ in range(MAX_CLIMB):
        if current.parent is None:
            break
        current = current.parent
        if len(price_pattern.findall(current.get_text(" ", strip=True))) > 1:
            break
        if _product_link(current) is not None:
            widest = current
    return widest


def _title_for(block, anchor):
    for candidate in (
        anchor.get("title"),
        anchor.get("aria-label"),
        block.find(["h1", "h2", "h3", "h4"]).get_text(" ", strip=True)
        if block.find(["h1", "h2", "h3", "h4"]) else None,
        anchor.get_text(" ", strip=True),
        (block.find("img") or {}).get("alt") if block.find("img") else None,
    ):
        if candidate and candidate.strip():
            return re.sub(r"\s{2,}", " ", candidate.strip())
    return ""


def find_products(html, base_url, price_pattern, parse_price):
    """Return [{text, title, price, url, variant_title}], or [] if the page
    has no repeated priced structure to read."""
    soup = BeautifulSoup(html, "html.parser")

    blocks = []
    for node in _price_nodes(soup, price_pattern):
        block = _block_for(node, price_pattern)
        if block is not None:
            blocks.append(block)

    # Siblings in a listing share a parent; the busiest parent is the grid.
    by_parent = {}
    for block in blocks:
        parent = block.parent
        if parent is None:
            continue
        by_parent.setdefault(id(parent), []).append(block)

    if not by_parent:
        return []
    best = max(by_parent.values(), key=len)
    if len(best) < MIN_BLOCKS:
        return []

    items, seen = [], set()
    for block in best:
        anchor = _product_link(block)
        if anchor is None:
            continue
        url = urljoin(base_url, anchor["href"].strip())
        if url in seen:
            continue
        seen.add(url)
        text = block.get_text(" ", strip=True)
        items.append({
            "text": text,
            "title": _title_for(block, anchor),
            "price": parse_price(text),
            "url": url,
            "variant_title": "",
        })
    return items


# --- finding the catalogue --------------------------------------------------

# A landing page is usually a shop window: a few featured bottles, or none.
# winenot.fr's home page yields 6 products against a catalogue of far more,
# and leszinzinsduvin's yields nothing at all. Rather than hand-record a
# path per shop, the probe walks this list once and records what worked.
CATALOGUE_PATHS = [
    "", "boutique", "shop", "vins", "vins.php", "nos-vins", "les-vins",
    "catalogue", "produits", "collections/all", "vin", "cave", "tous-nos-vins",
]


def looks_like_catalogue(items):
    return len(items) >= MIN_BLOCKS


# --- pagination -------------------------------------------------------------

NEXT_WORDS = {"next", "suivant", "suivante", "volgende", "weiter", "›", "»", "→", ">"}


def find_next_page(html, current_url):
    """The next catalogue page, or None.

    Catalogues are paged and reading only page one turns a real hit into a
    silent miss, which is the failure this whole project exists to avoid.
    """
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(["a", "link"], rel=True, href=True):
        rel = tag.get("rel")
        rel = [rel] if isinstance(rel, str) else (rel or [])
        if any(r.lower() == "next" for r in rel):
            return _distinct(urljoin(current_url, tag["href"].strip()), current_url)

    for anchor in soup.find_all("a", href=True):
        label = " ".join(filter(None, [
            anchor.get_text(" ", strip=True).lower(),
            (anchor.get("aria-label") or "").lower(),
            (anchor.get("title") or "").lower(),
        ]))
        if not label:
            continue
        if label in NEXT_WORDS or any(w in label.split() for w in ("next", "suivant", "suivante")):
            href = anchor["href"].strip()
            if href and not NON_PRODUCT_HREF.match(href):
                return _distinct(urljoin(current_url, href), current_url)
    return None


def _distinct(candidate, current):
    """A "next" link that points at the page we are on is a dead end, and
    following it would loop until the page budget ran out."""
    if candidate.rstrip("/") == current.rstrip("/"):
        return None
    if not urlparse(candidate).scheme.startswith("http"):
        return None
    return candidate
