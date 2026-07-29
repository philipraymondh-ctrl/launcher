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
import os
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

import autoselect
import crawler
import evaluate
import market
import notify
import textnorm

DRY_RUN = os.environ.get("DRY_RUN") == "1"

# Catalogues are paged. Without walking the pages we only ever see the
# newest ~250 products, so a producer sitting deeper in the catalogue
# reads as "not in stock" -- a silent false negative that looks like a
# clean run. MAX_PAGES_PER_SHOP bounds the cost; the crawler's own
# MAX_REQUESTS_PER_RUN budget still applies on top.
SHOPIFY_PAGE_SIZE = 250
WOO_PAGE_SIZE = 100
MAX_PAGES_PER_SHOP = 20

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
        "name": "winenot",
        "platform": "html",
        "url": "https://winenot.fr",
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
        "platform": "woocommerce",
        "url": "https://www.vinifine.be",
        "verified": True,
    },
    {
        "name": "zuiverwijnen",
        "platform": "shopify",
        "url": "https://zuiverwijnen.nl",
        "verified": True,
    },
    {
        # Platform guess based on the site's Dutch WooCommerce-default URL
        # slug ("/product-categorie/"). Weak evidence, still unverified.
        "name": "vinopura",
        "platform": "woocommerce",
        "url": "https://vinopura.nl",
        "verified": True,
    },
    {
        "name": "volatilewines",
        "platform": "woocommerce",
        "url": "https://volatilewines.com",
        "verified": True,
    },
    {
        "name": "biowijnclub",
        "platform": "woocommerce",
        "url": "https://www.biowijnclub.nl",
        "verified": True,
    },
    {
        "name": "puurwijnshop",
        "platform": "shopify",
        "url": "https://www.puurwijn.shop",
        "verified": True,
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
    {
        "name": "lavinoterie",
        "platform": "html",
        "url": "https://lavinoterie.fr",
        "item_selector": "div.product",
        "title_selector": "h2.product-title",
        "price_selector": "span.price",
        "verified": False,
    },
]


class EmptyResponseError(Exception):
    """Raised when an HTML shop returns empty/near-empty markup, usually a JS-rendered storefront."""


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
PRICE_PATTERN = re.compile(
    r"(?:[€$£]\s?(\d{1,4}(?:[.,]\d{2})?))"
    r"|(?:(\d{1,4}(?:[.,]\d{2})?)\s?(?:€|EUR|USD|\$))",
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
    items = []
    for page in range(1, MAX_PAGES_PER_SHOP + 1):
        try:
            resp = crawler_client.get(url, params=params_for_page(page))
        except crawler.BudgetExceeded:
            print(
                f"[{shop['name']}] request budget exhausted after page {page - 1}; "
                f"catalogue TRUNCATED, later pages not checked"
            )
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
    return items


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


def fetch_html(shop, crawler_client):
    """Walk an HTML catalogue, following its own "next page" link.

    A shop may list nothing on its landing page, so `catalog_path` points
    at the catalogue when the two differ (leszinzinsduvin serves its wines
    from /vins.php).
    """
    start = urljoin(shop["url"] + "/", shop["catalog_path"]) if shop.get("catalog_path") else shop["url"]

    # Product URLs and page URLs are different namespaces -- checking a
    # "next page" link against the product set would never match, and a
    # self-referential pager would walk until the budget ran out.
    items, seen_urls, visited, page_url, how = [], set(), {start}, start, None
    first_html, first_url = "", start
    for page in range(1, MAX_PAGES_PER_SHOP + 1):
        try:
            resp = crawler_client.get(page_url)
        except crawler.BudgetExceeded:
            print(f"[{shop['name']}] request budget exhausted after page {page - 1}; "
                  f"catalogue TRUNCATED, later pages not checked")
            break
        resp.raise_for_status()
        if not resp.text.strip():
            if page == 1:
                raise EmptyResponseError(shop["name"])
            break

        if page == 1:
            first_html, first_url = resp.text, page_url
        page_items, how = _parse_html_page(shop, resp.text, page_url)
        fresh = [i for i in page_items if i["url"] not in seen_urls]
        if not fresh:
            break
        seen_urls.update(i["url"] for i in fresh)
        items.extend(fresh)

        next_url = autoselect.find_next_page(resp.text, page_url)
        if not next_url or next_url in visited:
            break
        visited.add(next_url)
        page_url = next_url
    else:
        print(f"[{shop['name']}] hit MAX_PAGES_PER_SHOP ({MAX_PAGES_PER_SHOP}); "
              f"catalogue may be TRUNCATED")

    if not items and first_html:
        items = _fetch_via_producer_index(shop, first_html, first_url, crawler_client)
        how = "index" if items else how

    if items and how == "auto":
        print(f"[{shop['name']}] no configured selector matched; "
              f"read {len(items)} product(s) by auto-detection")
    return items


FETCHERS = {
    "shopify": fetch_shopify,
    "woocommerce": fetch_woocommerce,
    "html": fetch_html,
}


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

    def __init__(self, hits=(), products_parsed=0, sold_out=(), near_tokens=()):
        super().__init__(hits)
        self.products_parsed = products_parsed
        self.sold_out = list(sold_out)
        self.near_tokens = set(near_tokens)


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
    hour = (now or dt.datetime.now(dt.timezone.utc)).hour
    offset = hour % len(shops)
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
                      sold_out=sold_out, near_tokens=near_tokens)


def main():
    crawler_client = crawler.Crawler()
    all_hits = []
    sold_out_shops = {}      # producer -> shops that had it, out of stock
    near_corpus = {}         # shop -> words close to a watched alias
    error_count = 0
    skipped_count = 0
    silent_shops = []
    verified_names = [s["name"] for s in SHOPS if s.get("verified", True)]
    # Rotated so a binding budget does not starve the same tail every hour.
    order = shop_order(SHOPS)

    for i, shop in enumerate(order):
        if not shop.get("verified", True):
            skipped_count += 1
            print(f"[{shop['name']}] skipped: unverified placeholder, needs shop-adapter confirmation")
            continue

        if crawler_client.request_count >= crawler_client.max_requests:
            remaining = [s["name"] for s in order[i:] if s.get("verified", True)]
            print(
                f"MAX_REQUESTS_PER_RUN ({crawler_client.max_requests}) reached; "
                f"not reached this run: {', '.join(remaining)}"
            )
            break

        try:
            hits = check_shop(shop, crawler_client)
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
            print(f"[{shop['name']}] empty response (likely JS-rendered storefront)")
        except crawler.Disallowed as e:
            error_count += 1
            print(f"[{shop['name']}] robots.txt disallows this path, skipped: {e}")
        except crawler.CircuitOpen as e:
            error_count += 1
            print(f"[{shop['name']}] circuit breaker open for this host, skipped: {e}")
        except crawler.BudgetExceeded:
            remaining = [s["name"] for s in order[i:] if s.get("verified", True)]
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

    notify.run_digest(evaluated, dry_run=DRY_RUN, notes={
        "Shops that returned nothing": silent_shops,
        "Matched but sold out everywhere": sold_out_only,
        "Watched but found nowhere": unseen,
        "Alias near-misses": misses,
    })


if __name__ == "__main__":
    main()
