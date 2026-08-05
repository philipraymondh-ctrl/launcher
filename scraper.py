#!/usr/bin/env python3
"""Hourly scraper: checks configured wine shops for named producers, prices
each hit against prices.yaml, and sends a digest email on anything alert-worthy.

Run modes:
  python scraper.py            normal run, sends a digest email via Gmail SMTP
  DRY_RUN=1 python scraper.py  skips SMTP, prints the would-be digest to stdout

Env vars consumed by the crawl layer (see crawler.py): CONTACT_URL,
MAX_REQUESTS_PER_RUN, FRESH.
"""
import datetime as dt
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

import autoselect
import crawler
import evaluate
import market
import notify
import pdflist
import textnorm

DRY_RUN = os.environ.get("DRY_RUN") == "1"
# Set by the workflow when a human dispatched the run rather than the hourly
# schedule. A scheduled run with no news is right to stay quiet; a run
# somebody pressed a button for has to answer, or the button looks broken.
FORCE_REPORT = os.environ.get("FORCE_REPORT") == "1"

# Politeness is the cost of this crawl: 3s plus jitter per host, per request.
# Reading all 23 shops to the end of their catalogues is 311 requests, ~28
# minutes cold. A job killed at the runner's ceiling loses everything -- no
# hits.json, no email, a red run and no explanation -- so the run stops itself
# first and says which shops it did not reach, exactly as it does when the
# request budget binds. 0 disables it.
MAX_RUN_SECONDS = float(os.environ.get("MAX_RUN_SECONDS", "2700"))

# One row per live shop: what we read, what was buyable, and who we matched.
# "Does this match the shop's real selection?" is the question that decides
# whether a shop is worth keeping, and until this table it could only be
# answered by reading a run log line by line.
COVERAGE_PATH = Path(os.environ.get(
    "COVERAGE_OUTPUT_PATH", Path(__file__).parent / "coverage.json"))
COVERAGE_COLUMNS = ["SHOP", "PLATFORM", "STATUS", "PAGES", "PRODUCTS",
                    "IN STOCK", "SOLD OUT", "HITS", "PRODUCERS"]


def coverage_row(shop, result=None, status="ok"):
    """One shop's line in the table. `result` is None when the fetch failed,
    which still gets a row -- a shop missing from the table entirely is how
    "we never looked" hides."""
    products = result.products_parsed if result is not None else 0
    sold_out = result.out_of_stock if result is not None else 0
    hits = list(result) if result is not None else []
    producers = sorted({h["producer"] for h in hits}
                       | {h["producer"] for h in (result.sold_out if result else [])})
    if result is not None and result.truncated:
        status = "TRUNCATED"
    # "480 products" cannot be compared with a shop's real selection; "20/118
    # pages" can, and says plainly that the other 98 went unread.
    read = getattr(result, "pages_read", 0) if result is not None else 0
    total = getattr(result, "pages_total", None) if result is not None else None
    pages = f"{read}/{total}" if total else (str(read) if read else "-")
    return {
        "shop": shop["name"],
        "platform": shop["platform"],
        "status": status,
        "pages": pages,
        "products": products,
        "in_stock": products - sold_out,
        "sold_out": sold_out,
        "hits": len(hits),
        "producers": producers,
    }


def coverage_table(rows):
    """The table as lines of text, aligned, for the log and the email."""
    if not rows:
        return []
    cells = [[r["shop"], r["platform"], r["status"], r.get("pages", "-"),
              str(r["products"]), str(r["in_stock"]), str(r["sold_out"]),
              str(r["hits"]), ", ".join(r["producers"]) or "-"] for r in rows]
    widths = [max(len(c[i]) for c in [COVERAGE_COLUMNS] + cells)
              for i in range(len(COVERAGE_COLUMNS))]
    # Numbers right, names left: a column of counts is read by comparing them.
    align = [False, False, False, True, True, True, True, True, False]

    def line(values):
        return " | ".join(
            v.rjust(w) if right else v.ljust(w)
            for v, w, right in zip(values, widths, align)
        ).rstrip()

    out = [line(COVERAGE_COLUMNS), "-" * len(line(COVERAGE_COLUMNS))]
    out += [line(c) for c in cells]
    totals = (f"{len(rows)} live shop(s), {sum(r['products'] for r in rows)} "
              f"product(s) read, {sum(r['hits'] for r in rows)} hit(s)")
    truncated = [r["shop"] for r in rows if r["status"] == "TRUNCATED"]
    if truncated:
        totals += (f". TRUNCATED at {', '.join(truncated)} -- their catalogue is "
                   f"bigger than what was read")
    out += ["", totals]
    return out

# Catalogues are paged. Without walking the pages we only ever see the
# newest ~250 products, so a producer sitting deeper in the catalogue
# reads as "not in stock" -- a silent false negative that looks like a
# clean run. MAX_PAGES_PER_SHOP bounds the cost; the crawler's own
# MAX_REQUESTS_PER_RUN budget still applies on top.
SHOPIFY_PAGE_SIZE = 250
WOO_PAGE_SIZE = 100
# A safety net, not the operating limit. What a catalogue actually costs is
# derived from the size it states on its own page one (autoselect.catalogue_size,
# free -- that page is already in hand), so vinnouveau's 118 pages are walked as
# 118 rather than clipped to a round number that reported 480 of its 2827 wines
# as though 480 were the catalogue. This only stops a runaway pager.
MAX_PAGES_PER_SHOP = 150

# ---------------------------------------------------------------------------
# Producers to watch for. Each canonical name maps to alias substrings that
# are matched accent- and case-insensitively (see normalize()). A domaine
# known by more than one name (e.g. Overnoy is run by Houillon) lists both.
# ---------------------------------------------------------------------------
PRODUCERS = {
    # Never a bare "overnoy" or "houillon". Both surnames are shared by
    # several unrelated Jura and Savoie estates -- Domaine Overnoy,
    # Overnoy-Crinquand, Overnoy Jean-Louis et Guillaume, Corentin
    # Houillon, Charlotte et Aurelien Houillon, Fimbel-Houillon -- and the
    # bare names reported all of their bottles as Pupillin's. This is the
    # Pierre Overnoy / Emmanuel Houillon estate and nothing else.
    "Overnoy/Houillon": [
        "overnoy-houillon", "overnoy houillon", "houillon-overnoy",
        "pierre overnoy", "overnoy pierre", "emmanuel houillon", "houillon emmanuel",
    ],
    "Ganevat": ["ganevat"],
    "Labet": ["labet"],
    "Domaine des Miroirs/Kagami": ["miroirs", "kagami"],
    "Domaine Calice": ["domaine du calice", "du calice", "loic calice", "calice"],
    "Thomas Popy": ["thomas popy", "popy"],
    "Roumier": ["roumier"],
    "Alice Fahrenkrug": ["alice fahrenkrug"],
    # Not a bare "brochet": Emmanuel Brochet is a different
    # Champagne grower, and mareehaute stocks him -- the bare
    # surname reported his bottles as this producer.
    "Jules Brochet": ["jules brochet"],
    "Bruyere Houillon": ["bruyere houillon", "bruyere-houillon"],
    "Allante et Boulanger": ["allante et boulanger", "allante boulanger", "allante"],
    "Domaine des Murmures": ["domaine des murmures", "murmures"],
    "Tom Gauditiabois": ["tom gauditiabois", "gauditiabois"],
    "Richard Leroy": ["richard leroy", "leroy richard"],
    "Lattard": ["lattard"],
    "Romain Lawson": ["romain lawson"],
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

    # --- Real shops ----------------------------------------------------
    # verified: True means probe.py --apply fetched and parsed this shop's
    # real catalogue, saved it as the fixture, and set the platform from
    # what was actually observed. Those shops are fetched on every run.
    #
    # verified: False means the opposite -- the entry came from research,
    # its platform/selectors are guesses, and main() skips it entirely.
    # Re-run Probe Shops with apply to promote one; see its fixture and the
    # probe report for why it hasn't been promoted yet.
    {
        # Hand-rolled PHP: no products.json, no Store API. The wines live at
        # /vins.php (product pages are /vin-<id>-<region>_<colour>__<cuvee>_
        # <vintage>_<producer>.html), so the landing page alone parses to
        # nothing. Selectors below are the generic placeholders and are
        # expected to miss -- autoselect reads the listing instead.
        "name": "leszinzinsduvin",
        "platform": "html",
        "url": "https://www.leszinzinsduvin.com",
        "catalog_path": "domaines.php",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": True,
    },
    {
        # Its region categories (/12-alsace, /19-jura, ...) carry no products:
        # 456KB of HTML with 21 prices and five product links, so the grid is
        # rendered client-side. The routes that do parse are its colour and
        # type filters, and six of them cover the catalogue. Probe run
        # 30946131912 established that -- see probe_pages/capture.winenot-fr.*.
        "name": "winenot",
        "platform": "html",
        "url": "https://winenot.fr",
        "catalog_paths": [
            "s/2/rouge", "s/1/blanc", "s/5/rose",
            "s/3/vin-effervescent", "s/4/vin-moelleux", "s/34/vin-mute",
        ],
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": True,
    },
    {
        # Marée Haute, Saint-Pierre d'Oléron. Indexed URLs are
        # /fr-eu/pages/..., /en/collections/tous-nos-vins -- locale prefix
        # plus /collections/, which is Shopify's own shape. Still a guess
        # until the probe fetches it.
        "name": "mareehaute",
        "platform": "shopify",
        "url": "https://www.mareehaute.vin",
        "verified": True,
    },
    {
        "name": "vinnouveau",
        "platform": "html",
        "url": "https://vinnouveau.fr",
        "catalog_path": "12-vins-francais",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": True,
    },
    {
        # Platform guess is more confident here: indexed URLs follow
        # Shopify conventions (/en/collections, /en/products/<handle>,
        # /en/cart, /password). Still unverified -- see fixture notes.
        "name": "levinnaturel",
        "platform": "shopify",
        "url": "https://levinnaturel.com",
        "verified": True,
    },
    {
        "name": "lespeauxdevins",
        "platform": "shopify",
        "url": "https://lespeauxdevins.com",
        "verified": True,
    },
    {
        "name": "lacavedespapilles",
        "platform": "shopify",
        "url": "https://www.lacavedespapilles.com",
        "verified": True,
    },
    {
        "name": "vinnaturel",
        "platform": "html",
        "url": "https://www.vinnaturel.fr",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": True,
    },
    {
        "name": "whynat",
        "platform": "shopify",
        "url": "https://www.whynat.fr",
        "verified": True,
    },
    {
        "name": "vinibee",
        "platform": "woocommerce",
        "url": "https://www.vinibee.com",
        "verified": True,
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
        "platform": "shopify",
        "url": "https://www.petitescaves.com",
        "verified": True,
    },
    {
        "name": "cavepurjus",
        "platform": "html",
        "url": "https://www.cavepurjus.com",
        "catalog_path": "tous-nos-vins",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": True,
    },
    {
        "name": "purewijnen",
        "platform": "html",
        "url": "https://www.purewijnen.be",
        "catalog_path": "nl/wijnkaart",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": True,
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
        "catalog_paths": ["shop", "http://vinovivo.be/shop", "portfolio_page/pack-rosato", "portfolio_page/olivier-boulin-jura-bourgogne"],
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": True,
    },
    {
        "name": "zuiverwijnen",
        "platform": "shopify",
        "url": "https://zuiverwijnen.nl",
        "verified": True,
    },
    {
        "name": "puurwijnshop",
        "platform": "shopify",
        "url": "https://www.puurwijn.shop",
        "verified": True,
    },
    {
        "name": "lavinoterie",
        "platform": "shopify",
        "url": "https://lavinoterie.fr",
        "verified": True,
    },
    {
        "name": "pangee",
        "platform": "html",
        "url": "https://la-pangee.com/fr",
        "catalog_paths": ["25-vins", "https://la-pangee.com/nouveaux-produits", "nouveaux-produits", "28-beaujolais"],
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": True,
    },
    # Private sales of artisan-grower wines, Issy-les-Moulineaux. Its own
    # URLs (/content/5-notre-concept) are PrestaShop-shaped, but the
    # platform is the probe's to determine, not mine.
    {
        "name": "demainlesvins",
        "platform": "html",
        "url": "https://www.demainlesvins.com",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
    # Nuits-Saint-Georges, 4000+ references, Burgundy-led -- the first shop
    # on this list where Roumier is a plausible find rather than a hope.
    {
        "name": "cavescarriere",
        "platform": "html",
        "url": "https://caves-carriere.fr",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
    # Paris 14e, natural wine since 2006, ~1000 references. Its own domain is
    # a shopfront with nothing to read -- the webshop is hosted on Hiboutik, a
    # French point-of-sale SaaS, at mifuguemiraisin.hiboutik.com/shop. Probing
    # the main domain found nothing for exactly that reason.
    {
        "name": "mifuguemiraisin",
        "platform": "html",
        "url": "https://mifuguemiraisin.hiboutik.com",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
]


class EmptyResponseError(Exception):
    """Raised when an HTML shop returns empty/near-empty markup, usually a JS-rendered storefront."""


class UnreadableDocumentError(Exception):
    """A shop's catalogue document arrived but could not be read.

    A scanned wine list extracts to empty pages, and "no entries" from a
    document is indistinguishable from "this shop stocks nothing" unless it is
    raised as the failure it is.
    """


# Accent-folding lives in textnorm so the four modules that compare text
# cannot drift apart; see textnorm.py for why matching needs a second rule.
normalize = textnorm.strip_accents
match_key = textnorm.match_key


def match_producers(text):
    """Return the canonical producer names whose aliases appear in text.

    When one match's alias sits inside another's, only the longer one
    counts. Two producers legitimately share a surname -- "houillon"
    (Overnoy/Houillon) is a substring of "bruyere houillon" -- and without
    this every Bruyere-Houillon bottle would be reported twice, once under
    the wrong estate.
    """
    return list(matched_aliases(text))


def matched_aliases(text):
    """{producer: the alias that matched} -- the same rule, but keeping the
    alias instead of discarding it.

    A digest row that names the alias carries its own diagnosis: three
    estates were reported under the wrong producer this month, each caught
    only by someone recognising the name and opening the shop.

    Both sides go through `match_key`, so the separator a shop happens to
    choose is not part of the comparison: one alias `bruyere houillon`
    covers "Bruyère-Houillon", "Bruyere Houillon" and "Renaud
    Bruyère–Houillon", and `allante et boulanger` covers "Allanté &
    Boulanger". The longest-alias rule still decides between estates that
    share a surname, so widening the separators does not widen who matches.
    """
    norm = match_key(text)
    matched = {}
    for canonical, aliases in PRODUCERS.items():
        hits = [match_key(a) for a in aliases if match_key(a) in norm]
        if hits:
            matched[canonical] = max(hits, key=len)

    return {
        canonical: alias for canonical, alias in matched.items()
        if not any(other is not alias and alias in other for other in matched.values())
    }


# --- alias near-misses --------------------------------------------------------
#
# "Watched but found nowhere" is true and unhelpful: it cannot say whether
# the alias is wrong or the wine is simply not sold anywhere we look. A
# token one edit away from a watched alias is the cheapest available
# evidence for the first case -- and it is free, because the text was
# already in memory.
#
# Short words are one edit from everything, and French wine vocabulary is
# dense at six letters: "pierre", "calice", "kagami" each sit one edit from
# words the trade uses in earnest ("pierres", "malice", "kagame"). Seven is
# where the hint stops guessing.
NEAR_MISS_MIN_LEN = 7
NEAR_MISS_MAX_REPORTED = 5


def alias_tokens(producers=None):
    """The long, distinctive words of every watched alias."""
    tokens = set()
    for aliases in (producers if producers is not None else PRODUCERS).values():
        for alias in aliases:
            tokens |= {t for t in match_key(alias).split() if len(t) >= NEAR_MISS_MIN_LEN}
    return tokens


def alias_length_index(producers=None):
    """{first letter: lengths of alias tokens starting with it}.

    Built once per shop, not once per listing: a shop can parse thousands of
    products and this depends only on the roster.
    """
    by_first = {}
    for token in alias_tokens(producers):
        by_first.setdefault(token[0], set()).add(len(token))
    return by_first


def near_miss_candidates(text, by_first=None):
    """The words in `text` worth keeping for a later near-miss check.

    Filtered at collection rather than after the run: a token can only be
    one edit from an alias token if it starts with the same letter and its
    length is within one. That turns a shop's whole catalogue into a handful
    of words.
    """
    by_first = alias_length_index() if by_first is None else by_first
    if not by_first:
        return set()
    keep = set()
    for word in match_key(text).split():
        # One below the target floor: a single deletion from a seven-letter
        # alias token leaves six letters, and that is exactly the kind of
        # typo worth catching.
        if len(word) < NEAR_MISS_MIN_LEN - 1:
            continue
        for length in by_first.get(word[0], ()):
            if abs(length - len(word)) <= 1:
                keep.add(word)
                break
    return keep


def _one_edit_apart(a, b):
    """True when a single insertion, deletion or substitution turns a into b."""
    if a == b:
        return False
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    shorter, longer = (a, b) if len(a) < len(b) else (b, a)
    for i in range(len(longer)):
        if longer[:i] + longer[i + 1:] == shorter:
            return True
    return False


def _is_plural_of(word, target):
    return word == target + "s" or target == word + "s"


def near_misses(unseen, corpus, producers=None):
    """Suspicions, not findings: `Producer: 'spelling' at shop`.

    Only for producers that matched nothing at all -- for anyone who did
    match, a near-miss is just another wine on the same page.

    Three filters keep it from firing on ordinary words, all three learned
    from live runs that offered `Overnoy/Houillon: 'pierres'`,
    `'pierra'`, `'pierro'` and `Domaine Calice: 'malice'` -- because
    "pierre overnoy" carries a first name and "calice" is a French word
    before it is an estate. The corpus answers both ends of that:

    - a **candidate** at more than one shop is vocabulary, not a
      misspelling. A typo of a rare grower lives at the shop that made it.
    - a **target** the trade itself uses at more than one shop is not worth
      hunting typos of, however distinctive it looks in a producer's name.
    - a plural is not a typo.
    """
    catalogue = producers if producers is not None else PRODUCERS
    shops_per_word = {}
    for shop, words in corpus.items():
        for word in words:
            shops_per_word.setdefault(word, set()).add(shop)

    def common(word):
        return len(shops_per_word.get(word, ())) > 1

    reported = []
    for producer in unseen:
        targets = {t for alias in catalogue.get(producer, [])
                   for t in match_key(alias).split()
                   if len(t) >= NEAR_MISS_MIN_LEN and not common(t)}
        for shop in sorted(corpus):
            for word in sorted(corpus[shop]):
                if common(word):
                    continue
                if any(_one_edit_apart(word, t) and not _is_plural_of(word, t)
                       for t in targets):
                    reported.append(f"{producer}: '{word}' at {shop}")
                    break
    return reported[:NEAR_MISS_MAX_REPORTED]


# Matches a number only when a currency marker is directly adjacent, so a
# bare 4-digit vintage year (e.g. "2018") is never mistaken for a price.
#
# The number-then-marker branch carries three guards, all of them scars:
#
#  - `(?<![\d.,])` so the tail of a longer number is not read as a price of
#    its own. Without it, rejecting "2022 €" would just re-match "022 €".
#  - `(?!(?:19|20)\d{2}(?![.,]\d))` so a vintage immediately followed by the
#    *next* number's currency symbol is not read as that number's price.
#    "Ganevat Poulprix 2022 €45,00" parsed as 2022.0, and only French shops
#    (which write "45,00 €", number first) kept it from ever firing. A
#    price-shaped vintage with decimals -- "2018,50 €" -- is still a price,
#    which is why the guard looks past a decimal part.
#  - a lookahead rather than a consuming group for the marker, so the symbol
#    stays available to the symbol-first branch. Consuming it meant that
#    skipping "2022 €" also swallowed the € belonging to "45,00", and the
#    real price went unread.
#
# A genuine EUR 2018 bottle written "2018 €" is lost to this. That is the
# trade this codebase makes everywhere: no number beats a confident wrong one.
PRICE_PATTERN = re.compile(
    r"(?:[€$£]\s?(\d{1,4}(?:[.,]\d{2})?))"
    r"|(?:(?<![\d.,])(?!(?:19|20)\d{2}(?![.,]\d))"
    r"(\d{1,4}(?:[.,]\d{2})?)\s?(?=€|EUR|USD|\$))",
    re.IGNORECASE,
)


def positive_price(value):
    """A zero is not a price.

    Cart widgets ("Voir mon panier -- 0,00 EUR"), gift cards and "price on
    request" placeholders all carry a currency-adjacent zero, and zero sits
    below every reference there will ever be, so such a row scores DEAL for
    ever. One live dry run put exactly that in the digest.
    """
    return value if value and value > 0 else None


def parse_price(text):
    match = PRICE_PATTERN.search(text or "")
    if not match:
        return None
    raw = match.group(1) or match.group(2)
    return positive_price(float(raw.replace(",", ".")))


def _paged(shop, crawler_client, url, params_for_page, page_size, extract):
    """Walk a paged catalogue until it's exhausted, a short page arrives, or
    a limit is hit. Returns whatever was collected -- a partial catalogue is
    still real data, but truncation is logged loudly because it can turn a
    genuine hit into a silent miss."""
    items, truncated, fetched = [], False, 0
    for page in range(1, MAX_PAGES_PER_SHOP + 1):
        try:
            resp = crawler_client.get(url, params=params_for_page(page))
            fetched += 1
        except crawler.BudgetExceeded:
            print(
                f"[{shop['name']}] request budget exhausted after page {page - 1}; "
                f"catalogue TRUNCATED, later pages not checked"
            )
            truncated = True
            break
        resp.raise_for_status()
        records = extract(resp.json())
        if not records:
            break
        items.extend(records)
        if len(records) < page_size:
            break
    else:
        print(
            f"[{shop['name']}] hit MAX_PAGES_PER_SHOP ({MAX_PAGES_PER_SHOP}); "
            f"catalogue TRUNCATED, later pages not checked"
        )
        truncated = True
    return ParsedItems(items, truncated=truncated, pages_read=fetched)


def in_stock(explicit, text):
    """Whether a listing can actually be bought.

    False only when the shop says so. An unknown stock state is not a
    reason to hide a wine -- the platform APIs are authoritative when they
    answer, and the text is a backstop for shops that only say "epuise" in
    the title.
    """
    if explicit is False:
        return False
    return not autoselect.is_out_of_stock(text)


def fetch_shopify(shop, crawler_client):
    base = shop["url"].rstrip("/")

    def parse_page(payload):
        products = payload.get("products", [])
        parsed = []
        for product in products:
            title = product.get("title", "")
            text = f"{title} {product.get('vendor', '')} {product.get('body_html', '')}"
            price = None
            variants = product.get("variants") or []
            variant_title = ""
            if variants:
                if variants[0].get("price"):
                    price = positive_price(float(variants[0]["price"]))
                variant_title = variants[0].get("title", "") or ""
            # Shopify reports availability per variant; one buyable format
            # is enough. Absent the field entirely, assume nothing.
            stated = (any(v.get("available") for v in variants)
                      if any("available" in v for v in variants) else None)
            parsed.append({
                "text": text,
                "title": title,
                "price": price,
                "url": f"{base}/products/{product.get('handle', '')}",
                "variant_title": variant_title,
                "in_stock": in_stock(stated, f"{title} {variant_title}"),
            })
        return parsed

    return _paged(
        shop, crawler_client,
        f"{base}/products.json",
        lambda page: {"limit": SHOPIFY_PAGE_SIZE, "page": page},
        SHOPIFY_PAGE_SIZE,
        parse_page,
    )


def fetch_woocommerce(shop, crawler_client):
    base = shop["url"].rstrip("/")

    def parse_page(payload):
        parsed = []
        for product in payload:
            name = product.get("name", "")
            text = f"{name} {product.get('short_description', '')} {product.get('description', '')}"
            price = None
            prices = product.get("prices") or {}
            raw_price = prices.get("price")
            if raw_price:
                minor_unit = int(prices.get("currency_minor_unit", 2))
                price = positive_price(int(raw_price) / (10 ** minor_unit))
            parsed.append({
                "text": text,
                "title": name,
                "price": price,
                "url": product.get("permalink", shop["url"]),
                "variant_title": "",
                "in_stock": in_stock(product.get("is_in_stock"), name),
            })
        return parsed

    return _paged(
        shop, crawler_client,
        f"{base}/wp-json/wc/store/v1/products",
        lambda page: {"per_page": WOO_PAGE_SIZE, "page": page},
        WOO_PAGE_SIZE,
        parse_page,
    )


def _parse_html_page(shop, html, page_url, min_blocks=None):
    """Configured selectors first, auto-detection when they find nothing.

    The selectors in SHOPS are generic guesses for every shop that has not
    been probed, so on a real site they usually match zero elements.
    Falling back to autoselect turns that from "shop needs hand-written
    selectors" into a working adapter, and a shop whose markup later
    changes degrades to auto-detection instead of silently reporting an
    empty catalogue.
    """
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for node in soup.select(shop["item_selector"]):
        title_node = node.select_one(shop["title_selector"])
        price_node = node.select_one(shop["price_selector"])
        title = title_node.get_text(strip=True) if title_node else ""
        price = parse_price(price_node.get_text(strip=True) if price_node else "")
        link_node = node.select_one("a[href]")
        url = urljoin(page_url, link_node["href"]) if link_node else page_url
        items.append({
            "text": f"{title} {node.get_text(' ', strip=True)}",
            "title": title,
            "price": price,
            "url": url,
            "variant_title": "",
        })
    if items:
        return items, "selectors"

    return autoselect.find_products(
        html, page_url, PRICE_PATTERN, parse_price, min_blocks=min_blocks), "auto"


def _fetch_via_producer_index(shop, index_html, index_url, crawler_client):
    """Last resort for a shop with no crawlable catalogue.

    leszinzinsduvin only exposes its wines through a POST search form, but
    it lists its growers, and we watch growers. Follow the index entries
    matching PRODUCERS and read those pages -- a dozen requests instead of
    a full catalogue walk, aimed at exactly the bottles we care about.
    """
    # The page is already in hand -- re-fetching it wasted a request, and
    # under the probe's replay crawler it fetched a *different* page than
    # the one just parsed, so the index was never actually read.
    targets = autoselect.find_producer_links(index_html, index_url, match_producers)
    if not targets:
        return []

    items, empty = [], 0
    for producer, url in targets[:autoselect.MAX_INDEX_LINKS]:
        try:
            page = crawler_client.get(url)
            page.raise_for_status()
        except (crawler.BudgetExceeded, crawler.UpstreamError):
            break
        # One bottle on a grower's page is still that grower's bottle: the
        # "is this a catalogue" test does not apply once we got here from
        # the index.
        page_items, _ = _parse_html_page(shop, page.text, url, min_blocks=1)
        if not page_items:
            empty += 1
        for item in page_items:
            # The grower's own page may not repeat their name on every row.
            item["text"] = f"{producer} {item['text']}"
        items.extend(page_items)
    followed = len(targets[:autoselect.MAX_INDEX_LINKS])
    in_stock = sum(1 for i in items if i.get("in_stock") is not False)
    print(f"[{shop['name']}] no crawlable catalogue; followed {followed} producer "
          f"page(s) from the index, {empty} listing nothing; read {len(items)} "
          f"product(s), {in_stock} in stock")
    return items


def catalogue_starts(shop, now=None):
    """Where to begin reading this shop's catalogue, and in what order.

    `catalog_paths` (a list) exists because winenot.fr and vinnouveau.fr keep
    their wines under region categories -- /12-alsace, /19-jura, /21-loire --
    with no "all wines" page, so one path can only ever read one region. That
    is how winenot came to be configured to read its sparkling-wine filter
    and nothing else. Each entry may be a path or an absolute URL; urljoin
    passes an absolute URL through untouched.

    The list is rotated by the hour for the same reason `shop_order` rotates
    SHOPS. One page budget is shared across every category, so a fixed order
    spends all of it on the first few: winenot read 233 products over 20
    pages and never once reached its rosé, sparkling, moelleux or muté
    categories -- not at this budget, and not at any budget, because the
    order never changed. Rotating costs nothing and makes the rest reachable.
    """
    base = shop["url"].rstrip("/") + "/"
    paths = shop.get("catalog_paths") or (
        [shop["catalog_path"]] if shop.get("catalog_path") else [])
    if not paths:
        return [shop["url"]]

    # The probe records a path and its absolute form as two entries ("shop"
    # and "http://vinovivo.be/shop"), which is one catalogue and two requests.
    starts, seen = [], set()
    for path in paths:
        url = urljoin(base, path)
        key = url.replace("http://", "https://").rstrip("/")
        if key not in seen:
            seen.add(key)
            starts.append(url)

    # The first entry is the catalogue the probe measured as best, and it is
    # read every run: page 1 is where new arrivals land, and that is the news
    # this scraper exists for. Only the rest rotate -- the probe also records
    # pages that merely parsed (pangee's "28-beaujolais", vinovivo's
    # portfolio pages), and rotating those into first place would spend the
    # shared page budget on a slice of the shop instead of the whole of it.
    if len(starts) > 2:
        at = now or dt.datetime.now(dt.timezone.utc)
        rest = starts[1:]
        offset = int(at.timestamp() // 3600) % len(rest)
        starts = starts[:1] + rest[offset:] + rest[:offset]
    return starts


def _walk_pages(shop, crawler_client, start, pages_left, seen_urls):
    """Follow one catalogue's own "next page" links.

    Returns (pages fetched, truncated, items, first page's html). `seen_urls`
    is shared across catalogues so a bottle listed in two categories is read
    once.
    """
    items, visited, page_url, how = [], {start}, start, None
    first_html, truncated, fetched = "", False, 0
    stated_pages = None

    page = 0
    while page < pages_left:
        page += 1
        try:
            resp = crawler_client.get(page_url)
            fetched += 1
            resp.raise_for_status()
        except crawler.BudgetExceeded:
            print(f"[{shop['name']}] request budget exhausted after page {page - 1}; "
                  f"catalogue TRUNCATED, later pages not checked")
            truncated = True
            break
        except crawler.UpstreamError as e:
            # Page 1 failing is the shop failing, and must surface as such.
            # A later page failing is how several platforms say "that was the
            # last one" -- WordPress 404s a paged URL past the end. Raising
            # here threw away every page already read and reported a shop
            # that answered every request as unreachable.
            if page == 1:
                raise
            print(f"[{shop['name']}] page {page} returned {e}; treating page "
                  f"{page - 1} as the last one")
            break
        if not resp.text.strip():
            if page == 1 and start == shop["url"]:
                raise EmptyResponseError(shop["name"])
            break

        if page == 1:
            first_html = resp.text
            # What this catalogue says it holds. Costs nothing: the page is
            # already here. A shop that overstates its pager cannot make the
            # walk longer than the net above.
            size = autoselect.catalogue_size(resp.text)
            if size and size[2]:
                stated_pages = min(size[2], pages_left)
                if size[0]:
                    print(f"[{shop['name']}] {start} states {size[0]} product(s) "
                          f"over {size[2]} page(s)")
        page_items, how = _parse_html_page(shop, resp.text, page_url)
        fresh = [i for i in page_items if i["url"] not in seen_urls]
        if not fresh:
            break
        seen_urls.update(i["url"] for i in fresh)
        items.extend(fresh)

        if stated_pages is not None and page >= stated_pages:
            break

        next_url = autoselect.find_next_page(resp.text, page_url)
        if not next_url or next_url in visited:
            break
        visited.add(next_url)
        page_url = next_url
    else:
        # Only reachable when the pager kept offering pages past the limit.
        print(f"[{shop['name']}] stopped at {pages_left} page(s) of {start}; "
              f"catalogue TRUNCATED")
        truncated = True

    return fetched, truncated, items, first_html, how, stated_pages


def fetch_html(shop, crawler_client):
    """Walk an HTML catalogue -- or several, when a shop splits its wines
    across region categories -- following each one's "next page" link.

    A shop may list nothing on its landing page, so `catalog_path` /
    `catalog_paths` point at the catalogue when the two differ
    (leszinzinsduvin serves its wines from /vins.php).
    """
    starts = catalogue_starts(shop)
    items, seen_urls, how = [], set(), None
    first_html, first_url, truncated = "", starts[0], False
    pages_total = None
    # A budget per catalogue rather than one shared across all of them. Sharing
    # it meant winenot spent every page on its first categories and never once
    # read its rose, sparkling, moelleux or mute; each category states its own
    # size, so each is walked to the end of itself.
    pages_read = 0

    for start in starts:
        fetched, page_truncated, page_items, page_html, page_how, stated = _walk_pages(
            shop, crawler_client, start, MAX_PAGES_PER_SHOP, seen_urls)
        pages_read += fetched
        if stated:
            pages_total = (pages_total or 0) + stated
        truncated = truncated or page_truncated
        items.extend(page_items)
        how = how or page_how
        if page_html and not first_html:
            first_html, first_url = page_html, start

    if not items and first_html:
        items = _fetch_via_pdf_list(shop, first_html, first_url, crawler_client)
        how = "pdf" if items else how

    if not items and first_html:
        items = _fetch_via_producer_index(shop, first_html, first_url, crawler_client)
        how = "index" if items else how

    if items and how == "auto":
        print(f"[{shop['name']}] no configured selector matched; "
              f"read {len(items)} product(s) by auto-detection")
    # A fraction is only shown when it means what it looks like. pages_read
    # counts every page across every catalogue this shop has, while a stated
    # total can only come from the catalogues that state one -- so vinovivo
    # reported "34/32" (32 pages of /shop plus two portfolio pages that state
    # nothing) and pangee "27/31" while being complete, because its categories
    # share bottles and the URL dedupe ends a walk early. Both read as a
    # shortfall that is not there.
    return ParsedItems(items, truncated=truncated, pages_read=pages_read,
                       pages_total=pages_total if len(starts) == 1 else None)


def _fetch_via_pdf_list(shop, page_html, page_url, crawler_client):
    """Last resort for a shop whose catalogue is a document.

    purewijnen has no price anywhere in its HTML -- three captured pages, zero
    currency markers -- and publishes its whole range as a 41-page PDF linked
    from /nl/wijnkaart. One extra request reads 800+ wines.

    The link is rediscovered every run rather than recorded. Its URL is a
    Drupal attachment carrying a file id and a mangled slug, so it changes with
    each new edition; a stored path would 404 the day the list is updated --
    which, for a list updated often, is most days, and silently.
    """
    pdf_url = autoselect.find_pdf_link(page_html, page_url)
    if not pdf_url:
        return []

    document = crawler_client.get(pdf_url)
    document.raise_for_status()
    pages, why = pdflist.extract_text(document.content)
    if pages is None:
        raise UnreadableDocumentError(f"{pdf_url}: {why}")

    text = "\n".join(pages)
    items = pdflist.parse_wine_list(text, pdf_url)
    # A scanned list extracts to empty pages, which would read as "this shop
    # stocks nothing" -- indistinguishable from a shop that really is empty.
    if pages and not items:
        raise UnreadableDocumentError(
            f"{pdf_url}: {len(pages)} page(s) held no readable entries")

    edition = pdflist.list_date(text)
    sold = sum(1 for i in items if i["in_stock"] is False)
    print(f"[{shop['name']}] catalogue is a document: {len(pages)} page(s)"
          f"{' of ' + edition if edition else ''}, {len(items)} entr(ies), "
          f"{sold} marked sold out")
    return items


FETCHERS = {
    "shopify": fetch_shopify,
    "woocommerce": fetch_woocommerce,
    "html": fetch_html,
}


class ParsedItems(list):
    """The listings a fetcher read, plus whether the catalogue ran out or we
    did.

    A `list` subclass for the same reason `ShopResult` is one: every caller
    and every test treats this as the list of items, and "we stopped before
    the shop did" is a fact about the fetch rather than a member of the
    payload. It is also the one number that decides whether a shop's row in
    the coverage table can be compared to its real selection at all.
    """

    def __init__(self, items=(), truncated=False, pages_read=0, pages_total=None):
        super().__init__(items)
        self.truncated = truncated
        # How much of the catalogue this was. "480 products" read as the whole
        # of vinnouveau for weeks while the shop's own page said 2827 over 118
        # pages -- the count alone cannot tell a complete read from a slice.
        self.pages_read = pages_read
        self.pages_total = pages_total


class ShopResult(list):
    """The hits, plus what the run learned while finding them.

    A list subclass rather than a new type on purpose: fifteen callers
    assert on this value directly (`== []`, `len(...)`, iteration), and the
    count is the drift signal, not the payload. Every verified shop has a
    real fixture that parses to more than zero products, so zero from a
    live fetch means the adapter has stopped reading that shop -- which
    until now printed exactly the same line as a shop with nothing in
    stock.

    `sold_out` carries the producers this shop had on the page but cannot
    sell today. They are not hits and never become hits -- they exist so
    "watched but found nowhere" can stop meaning "found nowhere in stock",
    which is a different and much less alarming fact.

    `near_tokens` is the raw material for the alias near-miss check: the
    words on this shop's pages that are close enough to a watched alias to
    be a misspelling of it. Filtered here rather than kept wholesale,
    because a shop like mareehaute parses 3481 products.
    """

    def __init__(self, hits=(), products_parsed=0, sold_out=(), near_tokens=(),
                 truncated=False, out_of_stock=0, pages_read=0, pages_total=None):
        super().__init__(hits)
        self.products_parsed = products_parsed
        self.sold_out = list(sold_out)
        self.near_tokens = set(near_tokens)
        self.truncated = truncated
        # Two different questions. `sold_out` is the watched producers whose
        # bottles are gone, which is what the digest note names.
        # `out_of_stock` is how much of the whole catalogue is unbuyable,
        # which is what the coverage table needs -- reading the first number
        # into that column reported 30 of mareehaute's 3496 as sold out when
        # the true figure was 2139.
        self.out_of_stock = out_of_stock
        self.pages_read = pages_read
        self.pages_total = pages_total


def shop_order(shops, now=None):
    """SHOPS rotated by the hour.

    main() spends a global request budget walking SHOPS in list order, so
    the moment that budget binds it is always the same shops that go
    unfetched -- a systematic blind spot rather than a random one. An
    hourly offset needs no stored counter and gives every shop an early
    slot over a day. `now` is injectable so tests are not time-dependent.
    """
    if len(shops) < 2:
        return list(shops)
    # Hours since the epoch, not the hour of the day: `.hour` only ever takes
    # 24 values, so with 29 shops the offsets 24-28 never occurred and the
    # five shops sitting there could never lead a run -- a systematic blind
    # spot of exactly the kind this function exists to remove. The test that
    # claimed otherwise ran against a three-shop canned list.
    at = now or dt.datetime.now(dt.timezone.utc)
    offset = int(at.timestamp() // 3600) % len(shops)
    return list(shops[offset:]) + list(shops[:offset])


def check_shop(shop, crawler_client):
    items = FETCHERS[shop["platform"]](shop, crawler_client)
    hits = []
    sold_out = []
    near_tokens = set()
    by_first = alias_length_index()
    skipped = 0
    for item in items:
        near_tokens |= near_miss_candidates(item["text"], by_first)
        matches = matched_aliases(item["text"])
        # A bottle nobody can buy is not a find. But it is still evidence
        # that this shop stocks the producer at all, which is the difference
        # between "your alias is broken" and "the wine is gone" -- so it is
        # matched first and set aside, not skipped before matching.
        out_of_stock = item.get("in_stock") is False
        if out_of_stock:
            skipped += 1
        for producer, alias in matches.items():
            row = {
                "shop": shop["name"],
                "producer": producer,
                "title": item["title"],
                "price": item["price"],
                "url": item["url"],
                "variant_title": item.get("variant_title", ""),
                "matched_alias": alias,
            }
            (sold_out if out_of_stock else hits).append(row)
    if skipped:
        print(f"[{shop['name']}] skipped {skipped} sold-out listing(s)")
    return ShopResult(hits, products_parsed=len(items),
                      sold_out=sold_out, near_tokens=near_tokens,
                      truncated=getattr(items, "truncated", False),
                      out_of_stock=skipped,
                      pages_read=getattr(items, "pages_read", 0),
                      pages_total=getattr(items, "pages_total", None))


def main():
    crawler_client = crawler.Crawler()
    all_hits = []
    coverage = []
    sold_out_shops = {}      # producer -> shops that had it, out of stock
    near_corpus = {}         # shop -> words close to a watched alias
    error_count = 0
    skipped_count = 0
    silent_shops = []
    blocked_shops = []       # answered 200 with a bot challenge, not content
    unreached = []           # verified shops the run never got to
    verified_names = [s["name"] for s in SHOPS if s.get("verified", True)]
    # Rotated so a binding budget does not starve the same tail every hour.
    order = shop_order(SHOPS)

    started = time.monotonic()
    for i, shop in enumerate(order):
        if MAX_RUN_SECONDS > 0 and time.monotonic() - started >= MAX_RUN_SECONDS:
            unreached = [s for s in order[i:] if s.get("verified", True)]
            remaining = [s["name"] for s in unreached]
            print(
                f"Out of time after {MAX_RUN_SECONDS:.0f}s; stopping cleanly so the "
                f"run still reports. Shops not reached this run: {', '.join(remaining)}"
            )
            break

        if not shop.get("verified", True):
            skipped_count += 1
            print(f"[{shop['name']}] skipped: unverified placeholder, needs shop-adapter confirmation")
            continue

        if crawler_client.request_count >= crawler_client.max_requests:
            unreached = [s for s in order[i:] if s.get("verified", True)]
            remaining = [s["name"] for s in unreached]
            print(
                f"MAX_REQUESTS_PER_RUN ({crawler_client.max_requests}) reached; "
                f"not reached this run: {', '.join(remaining)}"
            )
            break

        try:
            hits = check_shop(shop, crawler_client)
            coverage.append(coverage_row(shop, hits))
            all_hits.extend(hits)
            for row in hits.sold_out:
                sold_out_shops.setdefault(row["producer"], set()).add(row["shop"])
            if hits.near_tokens:
                near_corpus[shop["name"]] = hits.near_tokens
            parsed = hits.products_parsed
            if parsed == 0:
                # Its fixture parses to more than zero, so the adapter has
                # stopped reading this shop. Until now this printed the same
                # line as a shop with nothing we watch in stock.
                silent_shops.append(shop["name"])
            print(f"[{shop['name']}] ok, {len(hits)} hit(s) from {parsed} product(s)")
        except EmptyResponseError:
            error_count += 1
            coverage.append(coverage_row(shop, status="empty response"))
            print(f"[{shop['name']}] empty response (likely JS-rendered storefront)")
        except crawler.Disallowed as e:
            error_count += 1
            coverage.append(coverage_row(shop, status="robots.txt"))
            print(f"[{shop['name']}] robots.txt disallows this path, skipped: {e}")
        except crawler.Challenged as e:
            error_count += 1
            coverage.append(coverage_row(shop, status="blocked"))
            blocked_shops.append(shop["name"])
            print(f"[{shop['name']}] blocked by a bot challenge, not read: {e}")
        except crawler.CircuitOpen as e:
            error_count += 1
            coverage.append(coverage_row(shop, status="circuit open"))
            print(f"[{shop['name']}] circuit breaker open for this host, skipped: {e}")
        except crawler.BudgetExceeded:
            unreached = [s for s in order[i:] if s.get("verified", True)]
            remaining = [s["name"] for s in unreached]
            print(
                f"MAX_REQUESTS_PER_RUN ({crawler_client.max_requests}) reached mid-run; "
                f"not reached this run: {', '.join(remaining)}"
            )
            break
        except crawler.UpstreamError as e:
            error_count += 1
            coverage.append(coverage_row(shop, status="unreachable"))
            print(f"[{shop['name']}] unreachable: {e}")
        except Exception as e:
            error_count += 1
            coverage.append(coverage_row(shop, status="parse error"))
            print(f"[{shop['name']}] parse error: {e}")

    # A shop the run never got to needs a row of its own. Without one it
    # simply vanishes from the table -- and the table is the one place that
    # answers "did we look at all", so a missing row is the exact shape of
    # the failure it was built to expose.
    for shop in unreached:
        coverage.append(coverage_row(shop, status="not reached"))

    table = coverage_table(sorted(coverage, key=lambda r: -r["products"]))
    print()
    for line in table:
        print(line)
    print()
    if not DRY_RUN:
        COVERAGE_PATH.write_text(json.dumps(coverage, indent=2, sort_keys=True))

    print(f"{len(all_hits)} raw producer match(es) this run.")
    if silent_shops:
        print(f"DRIFT: {len(silent_shops)} verified shop(s) parsed no products at "
              f"all: {', '.join(silent_shops)}. Their fixtures parse fine, so the "
              f"adapter has stopped reading them.")
    # Three states, not two. A producer stocked at three shops and sold out
    # at all three used to be reported identically to one whose alias is
    # broken -- and the second is the only one worth acting on.
    found = {h["producer"] for h in all_hits}
    sold_out_only = [
        f"{p} [{', '.join(sorted(sold_out_shops[p]))}]"
        for p in PRODUCERS if p not in found and p in sold_out_shops
    ]
    unseen = [p for p in PRODUCERS if p not in found and p not in sold_out_shops]
    # "Found nowhere" means an alias that matches nothing, and it can only
    # mean that when every shop was actually read. With shops unreached the
    # same list also holds producers whose shop the run never opened, which
    # is how a budget cut-off turned into an accusation against the aliases.
    unseen_title = ("Watched but found nowhere" if not unreached else
                    f"Watched but found nowhere in the "
                    f"{len(coverage) - len(unreached)} shop(s) read")
    if sold_out_only:
        print(f"Matched but sold out everywhere ({len(sold_out_only)}): "
              f"{', '.join(sold_out_only)}")
    if unseen:
        print(f"Watched but found nowhere ({len(unseen)}): {', '.join(unseen)}")
    misses = near_misses(unseen, near_corpus)
    if misses:
        print(f"Alias near-misses ({len(misses)}): {'; '.join(misses)}")
    if error_count:
        print(f"{error_count} shop(s) had errors this run.")
    if skipped_count:
        print(f"{skipped_count} shop(s) skipped as unverified placeholders.")
    if not verified_names:
        print("No verified shops configured -- nothing to evaluate or notify on.")

    # Reference prices are read off the crawl, not typed in. Record this
    # run's listings *before* evaluating so a wine seen at three shops this
    # hour is already comparable this hour -- the store is a memory across
    # runs, not a prerequisite for one.
    pricebook = evaluate.load_pricebook()
    format_multipliers = {
        int(k): v for k, v in
        ((pricebook.get("defaults") or {}).get("format_multipliers") or {}).items()
    }
    aliases = market.aliases_by_producer(PRODUCERS)

    store = market.load_observations()
    sized = [evaluate.evaluate_hit(hit, pricebook) for hit in all_hits]
    fresh = [o for o in (market.observation(h, format_multipliers, aliases) for h in sized) if o]
    store = market.merge(store, fresh)
    print(f"{len(fresh)} listing(s) recorded; {len(store['records'])} in the reference pool.")

    evaluated = evaluate.evaluate_hits(all_hits, pricebook, store, aliases)

    # A dry run must leave no trace, exactly as it leaves seen.json alone.
    if not DRY_RUN:
        market.save_observations(store)

    notify.run_digest(evaluated, dry_run=DRY_RUN, force=FORCE_REPORT,
                      tables={"Shop coverage": table}, notes={
        "Shops that returned nothing": silent_shops,
        "Blocked by a bot challenge": blocked_shops,
        "Matched but sold out everywhere": sold_out_only,
        unseen_title: unseen,
        "Shops not reached this run": [s["name"] for s in unreached],
        "Alias near-misses": misses,
    })


if __name__ == "__main__":
    main()
