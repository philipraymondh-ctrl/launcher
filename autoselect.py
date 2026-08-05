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
import textnorm

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


# A price cell is short. 80 characters is roomy for "Prix habituel 21,50 €
# Prix soldé" and far too tight for a paragraph that merely mentions a
# number -- purovino's "In heel België vanaf 190 Euro" free-shipping line is
# exactly the prose this keeps out of the grid.
MAX_PRICE_CELL_CHARS = 80


def _price_nodes(soup, price_pattern):
    """Elements holding one price and nothing else.

    `_own_text` alone -- direct-child strings only -- misses every theme that
    wraps the currency marker in its own tag:

        <span class="amount">12.50<span class="currencySymbol">€</span></span>

    The digits and the marker then live in different elements, so no element's
    own text is currency-adjacent and the entire grid is invisible. That is
    WooCommerce's default markup and it is why vinovivo read as a shop with
    315 products and no prices.

    So: the innermost element whose *full* text carries a price. Innermost,
    because every ancestor up to <body> also contains that text and the widest
    of them would drag the whole page in as one product -- finding the right
    ancestor is _block_for's job, and it needs to start from the cell.
    """
    nodes = []
    for el in soup.find_all(True):
        text = el.get_text(" ", strip=True)
        if not price_pattern.search(text):
            continue
        # An element with a link inside it is a card, not a price cell.
        # Without this the "innermost" element can still be the whole tile,
        # and a producer named in a sidebar attaches to the wrong bottle.
        if el.find("a") is not None:
            continue
        if len(text) > MAX_PRICE_CELL_CHARS:
            continue
        if any(price_pattern.search(child.get_text(" ", strip=True) or "")
               for child in el.find_all(True)):
            continue
        nodes.append(el)
    return nodes


# Card layouts often carry the destination in an attribute and let script
# do the navigating -- leszinzinsduvin's grower list is 395
# `<div class="domaines__card" data-url="domaine-6-193-Ganevat...html">`
# and not one <a> among them. Reading only href finds nothing there.
LINK_ATTRS = ("href", "data-url", "data-href", "data-link")


def _link_target(element):
    for attr in LINK_ATTRS:
        value = (element.get(attr) or "").strip()
        if value and not NON_PRODUCT_HREF.match(value) and not NON_PRODUCT_PATH.search(value):
            return value
    return None


def _linked_elements(root):
    for element in root.find_all(True):
        target = _link_target(element)
        if target:
            yield element, target


def _product_link(element):
    # The element may *be* the link rather than contain one: a data-url
    # card carries the destination on itself, and looking only at
    # descendants made the whole card invisible to block detection.
    if _link_target(element):
        return element
    for candidate, _ in _linked_elements(element):
        return candidate
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


def _title_from_url(url):
    """The product slug as a last-resort name.

    A shop whose cards carry only an image and a price leaves nothing to
    read, but its product URL almost always spells the wine out --
    /vin-3120-jura_Blanc__Poulprix_2024_Ganevat.html. An untitled hit is
    useless in a digest, so this beats returning nothing.
    """
    slug = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"\.(?:html?|php|aspx)$", "", slug, flags=re.I)
    slug = re.sub(r"[-_+]+", " ", slug)
    return re.sub(r"\s{2,}", " ", slug).strip()


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


# A grower's page legitimately lists one or two bottles -- Labet's has two,
# Ganevat's currently none. Requiring three there returned nothing for a
# page that parses perfectly well; the three-block rule is for deciding
# whether an *unknown* page is a catalogue, and on a page reached through
# the producer index that question is already answered.
def find_products(html, base_url, price_pattern, parse_price, min_blocks=None):
    """Return [{text, title, price, url, variant_title}], or [] if the page
    has no repeated priced structure to read."""
    min_blocks = MIN_BLOCKS if min_blocks is None else min_blocks
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
    if len(best) < min_blocks:
        return []

    items, seen = [], set()
    for block in best:
        anchor = _product_link(block)
        if anchor is None:
            continue
        # Not anchor["href"]: since link detection learned to read data-url,
        # this element may be a <div> with no href at all, and indexing it
        # raised KeyError -- which the probe reported as "responded but
        # parse failed" on the very grower index it was meant to read.
        target = _link_target(anchor)
        if not target:
            continue
        url = urljoin(base_url, target)
        if url in seen:
            continue
        seen.add(url)
        text = block.get_text(" ", strip=True)
        # The block was chosen for holding a currency-adjacent number, so a
        # None here means the caller rejected the value itself -- a zero,
        # which is a cart total or a placeholder, not a bottle.
        price = parse_price(text)
        if price is None:
            continue
        items.append({
            "text": text,
            "title": _title_for(block, anchor) or _title_from_url(url),
            "price": price,
            "url": url,
            "variant_title": "",
            # Marked rather than dropped: the probe counts parsed products
            # to decide whether an adapter works, and a shop whose stock
            # happens to be sold out today must not read as broken.
            "in_stock": not (OUT_OF_STOCK.search(_strip_accents(text))
                             or markup_says_sold_out(block)),
        })
    return items


# A shop whose catalogue is a document. The link text says what it is in the
# shop's own language, and the filename usually repeats it.
PDF_LIST_WORDS = ("wijnlijst", "wijnkaart", "wine list", "winelist", "carte",
                  "tarif", "prijslijst", "catalogue", "lijst", "kaart", "liste")


def find_pdf_link(html, base_url):
    """The wine-list PDF on this page, or None.

    Discovered rather than recorded, every run: purewijnen's list is a Drupal
    attachment whose URL carries a file id and a mangled slug
    (`wijnlijst_winkel-lpt-desktop-78jpikp-desktop-78jpikp_257.pdf`), so a
    stored URL would 404 the moment they upload a new edition -- silently,
    which is the failure mode this project exists to avoid. The page that
    links it is stable; the file's name is not.
    """
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if ".pdf" not in href.lower():
            continue
        label = _strip_accents(
            f"{anchor.get_text(' ', strip=True)} {anchor.get('title') or ''} {href}")
        named = any(word in label for word in PDF_LIST_WORDS)
        candidates.append((named, urljoin(base_url, href)))
    if not candidates:
        return None
    # A shop with several PDFs (a list, a menu, terms) -- prefer one that says
    # it is a list, and otherwise take the first, which is document order.
    candidates.sort(key=lambda c: not c[0])
    return candidates[0][1]


# --- finding the catalogue --------------------------------------------------

# A landing page is usually a shop window: a few featured bottles, or none.
# winenot.fr's home page yields 6 products against a catalogue of far more,
# and leszinzinsduvin's yields nothing at all. Rather than hand-record a
# path per shop, the probe walks this list once and records what worked.
CATALOGUE_PATHS = [
    "", "boutique", "shop", "vins", "vins.php", "nos-vins", "les-vins",
    "catalogue", "produits", "collections/all", "vin", "cave", "tous-nos-vins",
    # Hand-rolled PHP shops keep their catalogue behind a script name, and
    # a "promos" page is where a deal-hunting scraper most wants to look.
    "vins.php", "promos.php", "boutique.php", "catalogue.php", "promotions",
    # Grower indexes: for a scraper watching named producers these are
    # often a better way in than the catalogue, and sometimes the only one.
    "domaines.php", "domaines", "producteurs", "vignerons", "nos-vignerons",
]


def looks_like_catalogue(items):
    return len(items) >= MIN_BLOCKS


# --- producer indexes ------------------------------------------------------

# Some shops have no browsable catalogue at all. leszinzinsduvin's /vins.php
# is a POST search form -- 132 filter options and no listing to fetch -- but
# it also publishes /domaines.php, an index of growers linking to
# /domaine-14-58-Rhone_DOMAINE_GRAMENON.html. Since this scraper watches a
# named list of producers rather than whole catalogues, following only the
# growers we care about is both the data we actually want and far politer
# than walking a thousand-wine catalogue to find sixteen of them.
# leszinzinsduvin alone stocks 16 of the producers we watch. Each is a
# request with the usual 3s+jitter politeness delay, so this is the most
# expensive path in the run -- but it is aimed at exactly the bottles we
# care about, where the alternative is a thousand-wine catalogue walk.
MAX_INDEX_LINKS = 20


def _identity_text(element):
    """The grower's name, not the blurb about them.

    An index card carries a heading and a paragraph of prose, and that
    prose name-drops: leszinzinsduvin's entry for Thomas Batardiere
    mentions Richard Leroy, and Alexandre Plassat's mentions Ganevat.
    Matching the whole card reported both as those producers' pages.
    """
    heading = element.find(["h1", "h2", "h3", "h4"])
    if heading:
        return heading.get_text(" ", strip=True)
    return element.get_text(" ", strip=True)


def find_producer_links(html, base_url, match_fn):
    """[(producer, url)] for index entries naming a producer we watch.

    Takes the matcher rather than the producer dict so the longest-alias
    -wins rule lives in one place: matching aliases here independently made
    "Bruyeere_houillon" report as Overnoy/Houillon, because "houillon" hits
    first and both estates share the surname.
    """
    soup = BeautifulSoup(html, "html.parser")
    found, seen = [], set()
    for element, target in _linked_elements(soup):
        url = urljoin(base_url, target)
        for canonical in match_fn(target + " " + _identity_text(element)):
            if (canonical, url) not in seen:
                seen.add((canonical, url))
                found.append((canonical, url))
    return found


# --- pagination -------------------------------------------------------------

# A listing that says it is sold out is not a find. Alerting on a bottle
# nobody can buy is the same noise as alerting on a coffret.
OUT_OF_STOCK = re.compile(
    r"\b(epuise|epuisee|rupture(?:\s+de\s+stock)?|sold\s*out|uitverkocht|"
    r"ausverkauft|non\s+disponible|indisponible)\b", re.I)


_strip_accents = textnorm.strip_accents


def is_out_of_stock(text, normalize_fn=None):
    return bool(OUT_OF_STOCK.search(_strip_accents(text)))


# Some shops say it in the markup and nowhere else. A WooCommerce card is
# `<li class="product ... outofstock">` whose visible text is identical to an
# in-stock card's except for the button ("Lire la suite" instead of "Ajouter
# au panier"), so reading text alone marks every sold-out bottle as buyable --
# and an alerted bottle is written to seen.json, which is what silences the
# restock alert for thirty days.
#
# Only the block's own class list, never a descendant's: themes ship hidden
# "sold out" badges inside every card, and believing one would quietly mark a
# whole catalogue sold out -- a failure that suppresses finds instead of
# adding noise, so nothing would ever report it.
OUT_OF_STOCK_CLASSES = {"outofstock", "out-of-stock", "out_of_stock",
                        "sold-out", "soldout", "epuise"}
OUT_OF_STOCK_SCHEMA = "schema.org/outofstock"


def markup_says_sold_out(block):
    """Whether the listing's own markup states it is out of stock.

    Only ever used to *add* an out-of-stock verdict, never to overturn one:
    silence from a theme is not a promise that a bottle is on the shelf.
    """
    classes = block.get("class") or []
    classes = [classes] if isinstance(classes, str) else classes
    if any(c.lower() in OUT_OF_STOCK_CLASSES for c in classes):
        return True
    # An explicit machine-readable statement, wherever it sits in the card.
    for el in block.find_all(True):
        for attr in ("href", "content", "itemtype"):
            if OUT_OF_STOCK_SCHEMA in (el.get(attr) or "").lower():
                return True
    return False


NEXT_WORDS = {"next", "suivant", "suivante", "volgende", "weiter", "›", "»", "→", ">"}
# Class tokens for a pager arrow that carries no text at all.
NEXT_CLASS_TOKENS = {"next", "suivant", "next-page", "pagination-next", "page-next"}


# Words a shop uses for the page that lists its wines, in the four languages
# these shops actually use. Matched against a link's text and its href.
CATALOGUE_WORDS = (
    "vins", "vin", "wines", "wine", "wijnen", "weine", "boutique", "shop",
    "cave", "caves", "catalogue", "catalog", "produits", "products",
    "collection", "collections", "selection", "assortiment", "winkel",
    "promos", "promotions", "nos-vins", "les-vins",
    # A shop whose catalogue is a document names the page for the list, not
    # for the goods: purewijnen's whole range is a PDF behind /nl/wijnkaart,
    # and with none of these words in the menu vocabulary the ten links
    # discovery did offer were all region pages carrying no price at all.
    "wijnkaart", "wijnlijst", "prijslijst", "kaart", "lijst", "carte",
    "tarif", "tarifs", "bestellen", "commander", "order",
    # French wine regions. winenot.fr and vinnouveau.fr split their
    # catalogues across these with no "all wines" page, so without them the
    # menu offers no catalogue at all -- and the probe settles for whatever
    # filter page happens to parse.
    "alsace", "beaujolais", "bordeaux", "bourgogne", "bugey", "champagne",
    "corse", "jura", "languedoc", "loire", "provence", "rhone", "roussillon",
    "savoie", "sud-ouest", "auvergne", "gascogne", "cotes", "vallee",
)
# Pages that are never a catalogue however they are worded. A cart link is
# the reason "0,00 EUR" once reached a digest as a permanent DEAL.
NOT_CATALOGUE_WORDS = (
    "panier", "cart", "winkelwagen", "compte", "account", "login", "connexion",
    "blog", "actualite", "actualites", "news", "journal", "contact", "cgv",
    "mentions", "legal", "livraison", "shipping", "faq", "about", "apropos",
    "a-propos", "newsletter", "checkout", "commande", "wishlist", "search",
    "recherche", "gift", "cadeau",
)
MAX_CATALOGUE_LINKS = 10
# French shops number their categories -- /12-alsace, /19-jura, /25-vins --
# which is the strongest available signal that a link is a category rather
# than a promo strip or a filter.
NUMBERED_CATEGORY = re.compile(r"/\d+-[a-z]")
# Real pages, but slices of the catalogue rather than the catalogue: a "new
# arrivals" strip, a promo list, a CMS page, a colour/type filter. Ranked
# last, never excluded -- for a shop with nothing better they are what there
# is.
SECOND_CHOICE = ("nouveaux-produits", "nouveautes", "promotions", "promos",
                 "/content/", "/s/")


def find_catalogue_links(html, base_url, exclude=()):
    """Candidate catalogue URLs taken from the page's own navigation.

    A fixed list of guessed paths cannot know that a shop calls its
    catalogue `/la-cave` or `/notre-selection`, but the shop's own menu says
    so in its links. Same-host only, cart/account/blog/legal pages excluded,
    Anything already read as a product on this page is excluded, and
    shallower paths come first: a category lives near the root
    (`/12-vins-francais`) while a bottle lives under one
    (`/accueil/4437-zulu-vin-de-france-rouge-magnum.html`), and both contain
    "vin". At 3s a request the bottles would crowd out the categories.
    """
    soup = BeautifulSoup(html, "html.parser")
    base_host = urlparse(base_url).netloc
    landing = {base_url.rstrip("/"), base_url.rstrip("/") + "/"}

    excluded = {u.rstrip("/") for u in exclude}
    found = []
    for anchor in soup.find_all("a", href=True):
        url = urljoin(base_url, anchor["href"])
        parsed = urlparse(url)
        if parsed.netloc != base_host or parsed.scheme not in ("http", "https"):
            continue
        if url in landing or url.rstrip("/") in landing:
            continue
        haystack = _strip_accents(f"{anchor.get_text(' ', strip=True)} {parsed.path}")
        haystack = re.sub(r"[^a-z0-9]+", " ", haystack)
        words = set(haystack.split())
        if words & set(NOT_CATALOGUE_WORDS):
            continue
        if not words & set(CATALOGUE_WORDS):
            continue
        if url.rstrip("/") in excluded:
            continue
        if url not in found:
            found.append(url)

    # A page named for the list itself. When a shop keeps its range in a
    # document, this is the only page that leads anywhere, and it must outrank
    # the region pages that merely look like catalogues.
    LIST_PAGE = ("wijnkaart", "wijnlijst", "prijslijst", "tarif", "carte",
                 "bestellen", "commander")

    def rank(url):
        path = urlparse(url).path
        return (
            # A slice of the catalogue is a last resort, but a list page is a
            # first one -- ahead of even a numbered category.
            not any(marker in path for marker in LIST_PAGE),
            # A slice of the catalogue is a last resort, not a first guess.
            any(marker in path for marker in SECOND_CHOICE),
            # A numbered category is the catalogue as the shop files it.
            not NUMBERED_CATEGORY.search(path),
            # A bottle's own page sits under a category; a category sits at
            # the root.
            len([p for p in path.strip("/").split("/") if p]),
            path.endswith(".html"),
        )

    # Stable: document order breaks ties, so a shop's own menu ordering still
    # decides between two equally promising categories.
    found.sort(key=rank)

    # Sample both kinds. winenot.fr lists 17 numbered categories whose pages
    # carry no products at all, and the routes that do parse are its filters
    # -- one of which is every colour and type at once. Ranked behind the
    # categories and cut by the cap, they were never tried, so the cap turned
    # a preference into an exclusion.
    preferred = [u for u in found if not any(m in urlparse(u).path
                                             for m in SECOND_CHOICE)]
    fallback = [u for u in found if u not in preferred]
    half = MAX_CATALOGUE_LINKS // 2
    picked = preferred[:max(half, MAX_CATALOGUE_LINKS - len(fallback))]
    picked += fallback[:MAX_CATALOGUE_LINKS - len(picked)]
    # A shop with only one kind still fills the list.
    picked += [u for u in found if u not in picked][:MAX_CATALOGUE_LINKS - len(picked)]
    return picked[:MAX_CATALOGUE_LINKS]


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

    # Last: the arrow with no words in it. WooCommerce's pager renders
    # <a class="next page-numbers"><span class="arrow"></span></a> -- no text,
    # no aria-label, no title, so both passes above see an empty label and
    # walk past the only link to page 2. Whole-token match on the class, so
    # "nextgen-gallery" is not a next page.
    for anchor in soup.find_all("a", href=True):
        classes = anchor.get("class") or []
        classes = [classes] if isinstance(classes, str) else classes
        if any(c.lower() in NEXT_CLASS_TOKENS for c in classes):
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
