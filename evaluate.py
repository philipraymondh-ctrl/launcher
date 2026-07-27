"""Price-band evaluation: bottle size, Burgundy cru tier, and comparison
against the reference figures in prices.yaml.

Never suppresses a hit. A missing reference, an unverified reference, or
low size/tier confidence gets a caveat marker, not a filter -- filtering on
bad reference data would hide real finds, which defeats the point of the
scraper.
"""
import re
import unicodedata
from pathlib import Path

import yaml

PRICES_PATH = Path(__file__).parent / "prices.yaml"


def normalize(text):
    # Duplicated from scraper.normalize(): tiny, and importing scraper here
    # would create a scraper <-> evaluate circular import (scraper.py calls
    # into evaluate.py, not the other way around).
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower()


def load_pricebook(path=None):
    with open(path or PRICES_PATH) as f:
        return yaml.safe_load(f)


# --- bottle size ----------------------------------------------------------

SIZE_PATTERNS = [
    (375, re.compile(r"\b(?:half(?:\s*bottle)?|demie?(?:-?\s*bouteille)?|37[.,]?5\s*cl|375\s*ml)\b", re.I)),
    (1500, re.compile(r"\b(?:magnum|mag\.?|1[.,]5\s*l|150\s*cl|1500\s*ml)\b", re.I)),
    (3000, re.compile(r"\b(?:double\s*magnum|jeroboam|3[.,]0?\s*l|300\s*cl|3000\s*ml)\b", re.I)),
    (750, re.compile(r"\b(?:75\s*cl|750\s*ml)\b", re.I)),
    # Jura Vin Jaune ships in a 620ml clavelin. Half the tracked
    # producers are Jura, so treating one as 750ml misprices it by ~20%.
    (620, re.compile(r"\b(?:clavelin|62\s*cl|620\s*ml|vin\s+jaune)\b", re.I)),
]


def parse_size(text):
    """Return (size_ml, confidence). Defaults to (750, "low") when nothing
    in the text matches a known format."""
    text = text or ""
    for size, pattern in SIZE_PATTERNS:
        if pattern.search(text):
            return size, "high"
    return 750, "low"


# A coffret/case is several bottles in a box, so its price is not
# comparable to a per-bottle reference at all. Real listings from
# levinnaturel and petitescaves ("COFFRET ANNIVERSAIRE GANEVAT", EUR 450)
# would otherwise be scored against a ~EUR 70 single-bottle reference and
# shouted about as HIGH. Detect them and caveat instead of pretending.
BUNDLE_RE = re.compile(
    r"\b(?:coffret|caisse|carton|case\s+of|gift\s*(?:box|set)|"
    r"(?:\d+)\s*(?:bouteilles|bottles)|assortiment|panach\w+)\b",
    re.I,
)


def is_bundle(text):
    return bool(BUNDLE_RE.search(normalize(text or "")))


# --- Burgundy tier ----------------------------------------------------------

GRAND_CRU_RE = re.compile(r"grand\s*cru", re.I)
PREMIER_CRU_RE = re.compile(r"(premier\s*cru|\b1er\b)", re.I)
BOURGOGNE_RE = re.compile(r"\bbourgogne\b", re.I)
VILLAGE_APPELLATIONS = [
    "chambolle-musigny", "chambolle musigny", "gevrey-chambertin", "gevrey chambertin",
    "vosne-romanee", "vosne romanee", "nuits-saint-georges", "nuits saint georges",
    "chassagne-montrachet", "chassagne montrachet", "puligny-montrachet", "puligny montrachet",
    "morey-saint-denis", "morey saint denis", "volnay", "pommard", "meursault", "beaune",
    "marsannay", "fixin", "santenay", "auxey-duresses", "auxey duresses",
]


def detect_tier(text):
    """Return (tier, confidence) for a Burgundy cuvee string. tier is one of
    grand_cru/premier_cru/village/bourgogne, or None if undetected (with
    confidence "low")."""
    norm = normalize(text)
    if GRAND_CRU_RE.search(norm):
        return "grand_cru", "high"
    if PREMIER_CRU_RE.search(norm):
        return "premier_cru", "high"
    if BOURGOGNE_RE.search(norm):
        return "bourgogne", "high"
    if any(v in norm for v in VILLAGE_APPELLATIONS):
        return "village", "high"
    return None, "low"


# --- reference lookup -------------------------------------------------------

def find_producer_entry(pricebook, producer_name):
    for entry in pricebook.get("producers", []):
        if entry.get("name") == producer_name:
            return entry
    return None


def find_cuvee_override(producer_entry, text):
    norm = normalize(text)
    for cuvee in (producer_entry or {}).get("cuvees") or []:
        if any(normalize(m) in norm for m in cuvee.get("match", [])):
            return cuvee
    return None


def derive_cuvee(title, producer_name):
    """Best-effort display label: the title with the producer's own name
    words stripped out. Cosmetic only -- doesn't affect classification."""
    label = title or ""
    for word in (producer_name or "").replace("/", " ").split():
        label = re.sub(re.escape(word), "", label, flags=re.IGNORECASE)
    label = re.sub(r"\s{2,}", " ", label).strip(" -,")
    return label or title or ""


# --- evaluation --------------------------------------------------------------

def evaluate_hit(hit, pricebook):
    """Return a new dict: hit plus size_ml, size_confidence, tier,
    tier_confidence, reference_price, expected_price, ratio, classification,
    reference_verified, caveat, cuvee.

    Always returns a fully classified hit -- never None, never dropped.
    """
    defaults = pricebook.get("defaults", {})
    format_multipliers = {int(k): v for k, v in (defaults.get("format_multipliers") or {}).items()}
    tier_multipliers = defaults.get("burgundy_tier_multipliers") or {}
    deal_threshold = defaults.get("deal_threshold", 0.85)
    fair_ceiling = defaults.get("fair_ceiling", 1.25)

    size_text = f"{hit.get('title', '')} {hit.get('variant_title', '')}"
    bundle = is_bundle(size_text)
    size_ml, size_confidence = parse_size(size_text)
    if bundle:
        # Unknown bottle count, so no format multiplier is defensible.
        # Report it as a coffret and always caveat it.
        size_confidence = "low"
        format_multiplier = 1.0
    else:
        format_multiplier = format_multipliers.get(size_ml, 1.0)

    result = dict(hit)
    result["size_ml"] = size_ml
    result["size_confidence"] = size_confidence
    result["bundle"] = bundle
    result["size_label"] = "coffret" if bundle else f"{size_ml}ml"
    result["cuvee"] = derive_cuvee(hit.get("title", ""), hit.get("producer", ""))

    producer_entry = find_producer_entry(pricebook, hit.get("producer"))
    if producer_entry is None:
        result.update(
            tier=None, tier_confidence="n/a", reference_price=None,
            expected_price=None, ratio=None, classification="NOREF",
            reference_verified=False, caveat=True,
        )
        return result

    reference_verified = bool(producer_entry.get("verified", False))

    tier = None
    tier_confidence = "n/a"
    tier_multiplier = 1.0
    if producer_entry.get("region") == "burgundy":
        tier, tier_confidence = detect_tier(hit.get("title", ""))
        if tier and tier in tier_multipliers:
            tier_multiplier = tier_multipliers[tier]
        else:
            tier = None  # unknown tier -> no multiplier applied

    cuvee_override = find_cuvee_override(producer_entry, hit.get("title", ""))
    reference_price = (cuvee_override or {}).get("reference_750_eur", producer_entry.get("reference_750_eur"))

    if reference_price is None:
        result.update(
            tier=tier, tier_confidence=tier_confidence, reference_price=None,
            expected_price=None, ratio=None, classification="NOREF",
            reference_verified=reference_verified, caveat=True,
        )
        return result

    expected_price = reference_price * tier_multiplier * format_multiplier
    observed_price = hit.get("price")

    if observed_price is None or not expected_price:
        classification = "NOREF"
        ratio = None
    else:
        ratio = observed_price / expected_price
        if ratio <= deal_threshold:
            classification = "DEAL"
        elif ratio > fair_ceiling:
            classification = "HIGH"
        else:
            classification = "FAIR"

    low_confidence = size_confidence == "low" or tier_confidence == "low"
    caveat = (not reference_verified) or low_confidence or bundle

    result.update(
        tier=tier,
        tier_confidence=tier_confidence,
        reference_price=reference_price,
        expected_price=expected_price,
        ratio=ratio,
        classification=classification,
        reference_verified=reference_verified,
        caveat=caveat,
    )
    return result


def evaluate_hits(hits, pricebook=None):
    pricebook = pricebook if pricebook is not None else load_pricebook()
    return [evaluate_hit(hit, pricebook) for hit in hits]
