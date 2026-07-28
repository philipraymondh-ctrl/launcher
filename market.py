#!/usr/bin/env python3
"""Reference prices derived from the shops we already crawl.

The hand-typed `reference_750_eur` in prices.yaml is one number per
producer, and one number cannot describe a producer who sells a negoce
cuvee at EUR 30 and a domaine vin jaune at EUR 250. Scored against a
single EUR 70 figure, the cheap line reads as a permanent DEAL and the
expensive line as permanently HIGH -- both meaningless, and no amount of
typing fixes it, because the work grows with producers x cuvees x
vintages.

So the reference is observed rather than declared. We already fetch ~13
catalogues an hour; when two shops list the same bottle, that is a market
price. Nothing here contacts a price aggregator -- the data is the crawl
we were already doing.

Making the comparison key fine enough separates the three things that
actually move a wine's price, with no per-producer configuration:

  negoce vs domaine  the label carries different names ("Anne et Jean
                     Francois Ganevat" vs "Ganevat"), so segment() puts
                     them in different buckets
  vintage            part of the key; 2012 is only ever compared to 2012
  cru / cuvee        the cru is in the cuvee text, so it is already part
                     of the cuvee key

What cannot be observed is left unobserved: a wine seen at exactly one
shop gets a weaker basis and a caveat, never an invented number.
"""
import json
import re
import statistics
import unicodedata
from datetime import date, timedelta
from pathlib import Path

import textnorm

OBSERVATIONS_PATH = Path(__file__).parent / "observations.json"

# One other shop listing the same bottle is already a comparison -- it is
# what "cheaper than elsewhere" means -- but it is one shop's opinion, so it
# only earns "medium". Two or more is a market, and earns "high". Demanding
# two *other* shops meant three shops had to stock the same wine, which
# across 13 catalogues almost never happens, and the rung never fired.
MIN_SHOPS = 1
SHOPS_FOR_HIGH = 2
# Cuvee names differ between shops ("Les Grands Teppes VV" vs "Grands
# Teppes Vieilles Vignes"), so identity is token overlap, not equality.
JACCARD_THRESHOLD = 0.6
# Beyond this a listing says more about last season than about today.
MAX_AGE_DAYS = 180
# For the producer-line fallback: enough listings that a median means
# something.
MIN_LINE_RECORDS = 3


# Deliberately the accent-only rule, not textnorm.match_key: VINTAGE_RE
# below tells a vintage from a price by the currency markers touching the
# number, and match_key removes them.
normalize = textnorm.strip_accents


# --- vintage ----------------------------------------------------------------

# Deliberately not the price pattern's problem: PRICE_PATTERN only matches a
# number with a currency marker touching it, and this only matches one with
# no currency marker adjacent, so the two can never claim the same digits.
VINTAGE_RE = re.compile(r"(?<![\d€$£.,])(19[5-9]\d|20[0-4]\d)(?![\d.,]*\s*(?:€|eur|usd|\$))", re.I)


def parse_vintage(text):
    """The vintage in a title, or None for NV/multi-vintage/unstated."""
    years = VINTAGE_RE.findall(normalize(text))
    if not years:
        return None
    # A title can carry two years ("2012, mis en 2020"); the wine is the
    # earlier one.
    return min(int(y) for y in years)


# --- cuvee identity ---------------------------------------------------------

# Words that say nothing about which wine this is.
STOPWORDS = {
    "de", "du", "des", "la", "le", "les", "l", "d", "et", "a", "au", "aux",
    "en", "the", "of", "and", "van", "der", "den", "il", "el",
    "vin", "vins", "wine", "wijn", "weine", "bouteille", "bottle", "fles",
    "cuvee", "cuvée", "domaine", "dom", "chateau", "maison", "clos",
    "magnum", "jeroboam", "demie", "demi", "half", "cl", "ml", "l",
    "coffret", "caisse", "carton", "case", "box", "assortiment",
    "bio", "nature", "naturel", "natural", "organic",
    "new", "nouveau", "arrivage", "stock", "vente", "sale", "promo",
}

TOKEN_RE = re.compile(r"[a-z]{2,}")


def cuvee_tokens(title, producer_name="", aliases=(), seg=""):
    """The significant words of a cuvee: the title with the producer's whole
    name phrase and the packaging noise taken out.

    Stripping the *segment* and not just the canonical alias matters. A
    negoce label repeats "Anne et Jean Francois" on every bottle, so those
    three words swamped short cuvee names -- "SUL Q" and "Poulprix" came out
    60% similar and were treated as the same wine.
    """
    norm = normalize(title)
    phrases = list(aliases) + (producer_name or "").replace("/", " ").split() + (seg or "").split()
    for phrase in sorted(phrases, key=len, reverse=True):
        norm = norm.replace(normalize(phrase), " ")
    return frozenset(t for t in TOKEN_RE.findall(norm) if t not in STOPWORDS)


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def same_cuvee(a, b, threshold=JACCARD_THRESHOLD):
    return jaccard(a, b) >= threshold


# --- producer segment (negoce vs domaine, without being told) ---------------

# Words that appear on everyone's label and so distinguish nobody. Dropping
# them collapses "Domaine Ganevat" and "Ganevat" into one segment while
# leaving "Anne et Jean Francois Ganevat" as its own -- which is exactly the
# negoce/domaine split, learned from the label rather than configured.
GENERIC_NAME_WORDS = {
    "domaine", "dom", "chateau", "ch", "maison", "clos", "vignoble",
    "vignobles", "earl", "scea", "gaec", "les", "le", "la", "du", "de",
    "des", "vins", "vin", "cave", "caves", "famille",
    # Colours and styles sit right before the producer name often enough
    # ("... , Blanc - Ganevat") to leak in and split a line in two.
    "blanc", "blanche", "rouge", "rose", "jaune", "brut", "sec", "doux",
    "white", "red", "orange", "petillant", "cremant",
}
NAME_WORD_RE = re.compile(r"[a-z]{2,}")
# The producer phrase runs back from the alias until something that cannot
# be part of a name: a digit, a comma, a pipe. A hyphen or apostrophe is
# *inside* names ("Jean-Francois"), so it is not a boundary -- treating it
# as one made "Jean-Francois Ganevat" and "Jean Francois Ganevat" two
# different producer lines.
SEG_BOUNDARY_RE = re.compile(r"[^a-z\s'’-]")
MAX_NAME_WORDS = 4


def segment(title, producer_name, aliases=()):
    """A key for which *line* of a producer this is.

    Negoce bottlings carry extra proper names on the label; domaine
    bottlings carry the bare estate name. Reading the name phrase off the
    title and dropping the generic words leaves those two as different
    strings without anyone having to say so.
    """
    norm = normalize(title)
    best = None
    for alias in aliases or [producer_name]:
        idx = norm.find(normalize(alias))
        if idx >= 0 and (best is None or idx < best[0]):
            best = (idx, normalize(alias))
    if best is None:
        return normalize(producer_name)

    idx, alias = best
    tail = SEG_BOUNDARY_RE.split(norm[:idx])[-1]
    before = NAME_WORD_RE.findall(tail)[-MAX_NAME_WORDS:]
    words = [w for w in before + NAME_WORD_RE.findall(alias)
             if w not in GENERIC_NAME_WORDS]
    return " ".join(words) or normalize(producer_name)


# --- observation records ----------------------------------------------------

def size_bucket(hit):
    """Coffrets are only comparable to other coffrets -- an unknown number
    of bottles has no per-bottle price."""
    return "coffret" if hit.get("bundle") else "bottle"


def to_750(price, size_ml, format_multipliers):
    """Per-750ml equivalent, so a magnum listing still informs the bottle
    reference instead of sitting in its own thin bucket."""
    multiplier = format_multipliers.get(size_ml, 1.0)
    if not multiplier:
        return None
    return price / multiplier


def observation(hit, format_multipliers, aliases_by_producer, today=None):
    """One record, or None if this listing can't inform anything."""
    price = hit.get("price")
    producer = hit.get("producer")
    if price is None or price <= 0 or not producer:
        return None

    aliases = aliases_by_producer.get(producer, [])
    title = hit.get("title", "")
    seg = segment(title, producer, aliases)
    bucket = size_bucket(hit)

    if bucket == "coffret":
        value = price
    else:
        # A title with no size word is a 750 -- the same assumption
        # evaluate.py already makes, and the reason parse_size returns
        # (750, "low") rather than nothing. Rejecting low confidence here
        # rejected every ordinary bottle and left the pool permanently
        # empty; the sizes that actually matter (magnum, clavelin) carry a
        # word in the title and parse at high confidence.
        value = to_750(price, hit.get("size_ml") or 750, format_multipliers)
        if value is None:
            return None

    return {
        "producer": producer,
        "seg": seg,
        "cuvee": sorted(cuvee_tokens(title, producer, aliases, seg)),
        "vintage": parse_vintage(f"{title} {hit.get('variant_title', '')}"),
        "bucket": bucket,
        "shop": hit.get("shop"),
        "price750": round(value, 2),
        "seen": (today or date.today()).isoformat(),
    }


def load_observations(path=None):
    path = Path(path or OBSERVATIONS_PATH)
    if not path.exists():
        return {"records": []}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        # A corrupt store must not take the run down: the worst case is a
        # rebuild from this run's own cross-shop data.
        return {"records": []}
    return data if isinstance(data, dict) and "records" in data else {"records": []}


def save_observations(store, path=None):
    path = Path(path or OBSERVATIONS_PATH)
    path.write_text(json.dumps(store, indent=1, sort_keys=True) + "\n")


def record_key(record):
    # -1 stands in for "no vintage" so the key stays sortable: NV bottlings
    # are common, and a None here made sorting the store raise.
    return (record["producer"] or "", record["seg"] or "", record["bucket"],
            record["vintage"] if record["vintage"] is not None else -1,
            record["shop"] or "", tuple(record["cuvee"]))


def merge(store, records, today=None):
    """Fold this run's listings in, newest price per (wine, shop) wins, and
    drop anything too old to mean anything."""
    today = today or date.today()
    cutoff = (today - timedelta(days=MAX_AGE_DAYS)).isoformat()
    merged = {}
    for record in store.get("records", []):
        if record.get("seen", "") >= cutoff:
            merged[record_key(record)] = record
    for record in records:
        merged[record_key(record)] = record
    return {"records": sorted(merged.values(), key=record_key), "updated": today.isoformat()}


# --- deriving a reference ---------------------------------------------------

def _median(records):
    return round(statistics.median(r["price750"] for r in records), 2)


def _distinct_shops(records):
    return {r["shop"] for r in records}


def reference_from_market(hit, store, format_multipliers, aliases_by_producer):
    """Return {price, basis, confidence, shops, n} or None.

    A ladder from strongest to weakest evidence. Each rung is a real
    comparison; when none of them holds we return None and the caller
    reports NOREF rather than inventing a figure.
    """
    producer = hit.get("producer")
    if not producer:
        return None

    aliases = aliases_by_producer.get(producer, [])
    title = hit.get("title", "")
    seg = segment(title, producer, aliases)
    tokens = cuvee_tokens(title, producer, aliases, seg)
    vintage = parse_vintage(f"{title} {hit.get('variant_title', '')}")
    bucket = size_bucket(hit)
    shop = hit.get("shop")

    # Only other shops count. Comparing a shop's price to itself says
    # nothing, and would let one shop define its own bargains.
    pool = [r for r in store.get("records", [])
            if r["producer"] == producer and r["bucket"] == bucket and r["shop"] != shop]
    if not pool:
        return None

    same_wine = [r for r in pool if same_cuvee(tokens, frozenset(r["cuvee"]))]

    # 1. The same wine, same vintage, elsewhere. The strongest answer there
    #    is: no format, tier or age adjustment stands between the two
    #    numbers, they are the same bottle.
    if vintage is not None:
        exact = [r for r in same_wine if r["vintage"] == vintage]
        shops = _distinct_shops(exact)
        if len(shops) >= MIN_SHOPS:
            return {"price": _median(exact),
                    "basis": "same wine at %d shop%s" % (len(shops), "" if len(shops) == 1 else "s"),
                    "confidence": "high" if len(shops) >= SHOPS_FOR_HIGH else "medium",
                    "shops": sorted(shops), "n": len(exact)}

    # 2. The same cuvee, other vintages. Age moves price, so this is a
    #    hint, not a verdict, however many shops agree.
    shops = _distinct_shops(same_wine)
    if len(shops) >= MIN_SHOPS:
        return {"price": _median(same_wine), "basis": "same cuvee, other vintages",
                "confidence": "medium" if len(shops) >= SHOPS_FOR_HIGH else "low",
                "shops": sorted(shops), "n": len(same_wine)}

    # 3. The rest of this producer's line. Keeps a negoce bottle away from
    #    a domaine reference, but says nothing about which cuvee it is.
    line = [r for r in pool if r["seg"] == seg]
    if len(line) >= MIN_LINE_RECORDS and len(_distinct_shops(line)) >= SHOPS_FOR_HIGH:
        return {"price": _median(line), "basis": "%s line median" % (seg or producer),
                "confidence": "low", "shops": sorted(_distinct_shops(line)), "n": len(line)}

    return None


def aliases_by_producer(producers):
    """{canonical name: [aliases]} -- passed in so this module never has to
    import scraper (which imports evaluate, which imports this)."""
    return {name: list(aliases) for name, aliases in (producers or {}).items()}
