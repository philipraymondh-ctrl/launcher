"""Reading a wine list that is a document rather than a catalogue.

purewijnen publishes its whole range as a PDF -- 41 pages, ~700 wines,
`Wijnlijst Augustus 2026 (Deze lijst vervangt alle vorige.)` -- and has no
price anywhere in its HTML. Three pages of that site were captured and
checked before this existed, and every one held zero currency markers, so
this is not a shortcut around a parser that could have read the site: it is
the only thing there is to read.

The document's shape, from the real extracted text:

    Naam Streek Beschrijving Jaar Prijs        <- column header
    Domaine des Marnes
    Blanches, Trousseau
    Côtes du Jura Trousseau 2023  36.00        <- entry ends at the price
    Domaine du Calice, Loïc Arbois Savagnin 2022  SOLD

So an entry is "every line since the last one, up to and including the line
that carries a price". Producer, cuvée, region, grapes and vintage arrive as
ragged lines above it; which line is which varies, and it does not matter --
`match_producers` reads the whole block, exactly as it reads a card's text.

Two things this file must get right, because both are rules elsewhere in the
codebase and a document is where they are easiest to break:

1. **A number without a currency marker.** There is no `€` anywhere in the
   price column, so `scraper.PRICE_PATTERN` reads nothing here. This module
   therefore accepts a *bare* number, and pays for that licence with three
   restrictions: it must have a two-digit decimal part (which excludes a bare
   vintage -- `2023` cannot match), it must be the end of the line, and it
   must not be preceded by a digit, dot or slash. That last one is what keeps
   the shop's own phone number, `056/41.18.48`, from being read as EUR 18.48
   in an entry whose "wine" is the letterhead.
2. **`SOLD` is stock, not absence.** Both watched producers in the current
   list are marked SOLD. Dropping those rows would throw away the most
   valuable thing this shop can tell us: nothing about a sold-out listing is
   persisted, so when one comes back it reads as new and alerts.
"""
import io
import re

import textnorm

def extract_text(blob):
    """(pages, None) or (None, why it could not be read).

    pypdf is the whole dependency: no OCR, no layout analysis. A scanned list
    would come back as empty pages, which reads as "this shop has no wines"
    and must therefore be reported rather than parsed -- see fetch_pdf.
    """
    try:
        import pypdf
    except ImportError:                                   # pragma: no cover
        return None, "pypdf is not installed"
    try:
        reader = pypdf.PdfReader(io.BytesIO(blob))
        return [(page.extract_text() or "") for page in reader.pages], None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


# A price at the end of its line: two decimal places, optionally followed by
# the list's own footnote marker. `(?<![\d.,/])` is the phone-number guard.
PRICE_AT_END = re.compile(r"(?<![\d.,/])(\d{1,4})[.,](\d{2})\s*\*?\s*$")

# The same shape mid-line, for the entries that put the price before a
# trailing note. Deliberately not used to *find* the entry's end -- only to
# read a price out of a line already known to end one.
SOLD_MARKERS = ("sold", "uitverkocht", "op aanvraag", "binnenkort")

# Wine prices in this list run from about EUR 7 to EUR 250. The bound is not
# there to be clever: it is the last line of defence for a bare number, and a
# document contains page numbers, years, postcodes and street numbers.
MIN_PRICE = 4.0
MAX_PRICE = 3000.0

# Lines that are the document talking about itself, or our own capture header.
FURNITURE = re.compile(
    r"^(#|naam\s+streek|wijnlijst|deze lijst|www\.|\[redacted\]|pagina\b)", re.I)

# A page number is furniture; a bare year is part of an entry. Telling them
# apart matters because a multi-vintage entry puts its years on their own
# lines and its price on the next -- treating "2023" as furniture reset the
# buffer and left the price with no wine attached to it.
PAGE_NUMBER = re.compile(r"^\d{1,3}\s*$")

# An entry needs a name in it somewhere. Two letters is enough to reject the
# letterhead's postcode line without rejecting a terse cuvée.
MIN_ENTRY_LETTERS = 6

# Producer, cuvée, region/grapes and a wrap of the grape list. Bounding this
# bounds how far a leftover line can travel: PDF extraction interleaves the
# columns, so an unterminated block is how one wine's cuvée ends up in the
# next wine's text -- and a stray producer name there is a misattribution.
MAX_ENTRY_LINES = 4


def _price_on(line):
    match = PRICE_AT_END.search(line)
    if not match:
        return None
    value = float(f"{match.group(1)}.{match.group(2)}")
    return value if MIN_PRICE <= value <= MAX_PRICE else None


def _sold_on(line):
    lowered = textnorm.strip_accents(line).strip().lower()
    return any(lowered.endswith(marker) for marker in SOLD_MARKERS)


def _slug(text, limit=60):
    slug = re.sub(r"[^a-z0-9]+", "-", textnorm.strip_accents(text)).strip("-")
    return slug[:limit] or "entry"


def _title_of(lines):
    """The entry's name: its first two substantial lines, which is where the
    producer and the cuvée sit. The whole block still goes to the matcher --
    this is only what a human reads in the digest."""
    parts = [ln.strip() for ln in lines if len(ln.strip()) > 1]
    return re.sub(r"\s{2,}", " ", " ".join(parts[:2]))[:120]


def list_date(text):
    """The list's own date line, so a digest can say which edition it read.

    "Deze lijst vervangt alle vorige" -- this list replaces all previous ones
    -- is the shop telling us the document is the state of their range."""
    match = re.search(r"^\s*(wijnlijst[^\n(]*)", text, re.I | re.M)
    return match.group(1).strip() if match else ""


def parse_wine_list(text, source_url):
    """[{text, title, price, url, variant_title, in_stock}] from a PDF's text.

    Every item's URL is the document plus a fragment naming the wine, because
    `notify.item_key` hashes shop + url + variant: one URL for 700 wines would
    collapse them all into a single remembered item.
    """
    items, buffer, seen = [], [], set()
    for raw in text.splitlines():
        line = raw.rstrip()
        # Not a buffer reset: an entry whose cuvée field is empty renders as a
        # whitespace-only line *inside* it, and clearing here cost Renaud
        # Bruyère-Houillon's Arbois Trousseau its producer. A priced or SOLD
        # line already clears the buffer, so entries cannot bleed into each
        # other for want of this.
        if not line.strip():
            continue
        if FURNITURE.match(line.strip()):
            buffer = []
            continue
        if PAGE_NUMBER.match(line.strip()):
            continue

        price = _price_on(line)
        sold = _sold_on(line)
        if price is None and not sold:
            buffer.append(line)
            # A block that never terminates means the parse has lost its
            # footing (a page of prose, a restyled document). Forget it rather
            # than attaching thirty lines to the next price.
            if len(buffer) > MAX_ENTRY_LINES:
                buffer.pop(0)
            continue

        block = buffer + [line]
        buffer = []
        body = re.sub(r"\s{2,}", " ", " ".join(ln.strip() for ln in block)).strip()
        letters = sum(ch.isalpha() for ch in body)
        if letters < MIN_ENTRY_LETTERS:
            continue

        title = _title_of(block)
        url = f"{source_url}#{_slug(title)}"
        if url in seen:
            # The same wine at two vintages differs further along the block,
            # so key on the whole body before discarding a repeat.
            url = f"{url}-{_slug(body[-40:], 20)}"
        if url in seen:
            continue
        seen.add(url)
        items.append({
            "text": body,
            "title": title,
            "price": price,
            "url": url,
            "variant_title": "",
            # SOLD is the price column's other value. Marked, never dropped:
            # nothing about a sold-out listing is persisted, which is what
            # makes a restock read as new and alert.
            "in_stock": not sold,
        })
    return items
