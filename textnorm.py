"""How text is compared, in one place.

Two functions, deliberately not one.

`strip_accents` is what `scraper`, `market`, `evaluate` and `apply_issue`
each carried as their own private copy: NFKD, drop the combining marks,
lowercase. Punctuation survives, which matters because `market`'s vintage
regex and `scraper`'s price pattern both decide what a number *is* from the
currency markers touching it. Four identical copies were fine only while
nobody edited them; the first divergent edit would have made a producer
added through the issue form derive an alias that matches nothing, and the
feedback loop on that is a fortnight of quiet runs.

`match_key` is for the other job: deciding whether a *name* matches. Shops
write one estate as "Bruyère-Houillon", "Bruyere Houillon", "Renaud
Bruyère–Houillon" (en dash) and "Allanté & Boulanger" for "Allante et
Boulanger", so a hand-written alias per punctuation style is a maintenance
cost per producer that buys nothing. It must never be used on text a price
or vintage regex will read afterwards -- it removes exactly the markers
those regexes rely on.
"""
import re
import unicodedata

_NON_ALNUM = re.compile(r"[^0-9a-z]+")
# Both the ASCII ampersand and the full-width one shops occasionally paste.
_AMPERSANDS = ("&", "＆")


def strip_accents(text):
    """Accent-folded and lowercased, punctuation untouched."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower()


def match_key(text):
    """A name reduced to lowercase words separated by single spaces.

    Every separator a shop might choose -- hyphen, en dash, slash, comma,
    apostrophe, non-breaking space -- becomes a space, and an ampersand
    becomes the word it stands for, so one alias covers all of them.
    """
    folded = strip_accents(text)
    for amp in _AMPERSANDS:
        folded = folded.replace(amp, " et ")
    return _NON_ALNUM.sub(" ", folded).strip()
