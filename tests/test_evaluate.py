import pytest

import evaluate


@pytest.fixture
def pricebook():
    return {
        "defaults": {
            "format_multipliers": {375: 0.55, 750: 1.0, 1500: 2.30, 3000: 5.0},
            "burgundy_tier_multipliers": {
                "bourgogne": 0.6, "village": 1.0, "premier_cru": 2.0, "grand_cru": 4.5,
            },
            "deal_threshold": 0.85,
            "fair_ceiling": 1.25,
        },
        "producers": [
            {
                "name": "Labet",
                "region": "jura",
                "reference_750_eur": 55,
                "cuvees": [{"match": ["Fleurs", "Fleur de Marne"], "reference_750_eur": 75}],
                "last_verified": None,
                "verified": False,
            },
            {
                "name": "Roumier",
                "region": "burgundy",
                "reference_750_eur": 200,
                "cuvees": [],
                "last_verified": "2026-01-01",
                "verified": True,
            },
        ],
    }


# --- size parsing -----------------------------------------------------------

@pytest.mark.parametrize(
    "text,expected_ml",
    [
        ("Domaine Labet Magnum 2020", 1500),
        ("Domaine Labet mag 2020", 1500),
        ("Domaine Labet 1,5L 2020", 1500),
        ("Domaine Labet 150cl 2020", 1500),
        ("Domaine Labet half bottle 2020", 375),
        ("Domaine Labet demi 2020", 375),
        ("Domaine Labet 37,5cl 2020", 375),
    ],
)
def test_size_parsing_fixtures(text, expected_ml):
    size_ml, confidence = evaluate.parse_size(text)
    assert size_ml == expected_ml
    assert confidence == "high"


def test_size_defaults_to_750_with_low_confidence_when_unmatched():
    size_ml, confidence = evaluate.parse_size("Domaine Labet Chardonnay 2020")
    assert size_ml == 750
    assert confidence == "low"


# --- tier detection ----------------------------------------------------------

def test_tier_detects_premier_cru():
    tier, confidence = evaluate.detect_tier("Chassagne-Montrachet 1er Cru Les Vergers")
    assert tier == "premier_cru"
    assert confidence == "high"


def test_tier_detects_bourgogne():
    tier, confidence = evaluate.detect_tier("Bourgogne Blanc")
    assert tier == "bourgogne"
    assert confidence == "high"


def test_tier_detects_grand_cru():
    tier, confidence = evaluate.detect_tier("Musigny Grand Cru")
    assert tier == "grand_cru"
    assert confidence == "high"


def test_tier_unknown_when_no_keyword_present():
    tier, confidence = evaluate.detect_tier("Some Unlabelled Cuvee")
    assert tier is None
    assert confidence == "low"


# --- classification ----------------------------------------------------------

def test_deal_classification(pricebook):
    hit = {"producer": "Labet", "title": "Domaine Labet Cotes du Jura Chardonnay 2020", "price": 40, "shop": "s", "url": "u"}
    result = evaluate.evaluate_hit(hit, pricebook)
    assert result["classification"] == "DEAL"
    assert result["reference_price"] == 55
    assert result["expected_price"] == pytest.approx(55)
    assert result["caveat"] is True  # producer unverified


def test_fair_classification(pricebook):
    hit = {"producer": "Labet", "title": "Domaine Labet Cotes du Jura Chardonnay 2020", "price": 55, "shop": "s", "url": "u"}
    result = evaluate.evaluate_hit(hit, pricebook)
    assert result["classification"] == "FAIR"


def test_high_classification(pricebook):
    hit = {"producer": "Labet", "title": "Domaine Labet Cotes du Jura Chardonnay 2020", "price": 90, "shop": "s", "url": "u"}
    result = evaluate.evaluate_hit(hit, pricebook)
    assert result["classification"] == "HIGH"


def test_cuvee_override_reference_price_used(pricebook):
    hit = {"producer": "Labet", "title": "Domaine Labet Fleur de Marne 2020", "price": 75, "shop": "s", "url": "u"}
    result = evaluate.evaluate_hit(hit, pricebook)
    assert result["reference_price"] == 75
    assert result["classification"] == "FAIR"


def test_burgundy_tier_multiplier_applied(pricebook):
    # Size stated explicitly (750ml) so size_confidence is "high" -- this
    # isolates the "no caveat" happy path: verified producer, confident
    # tier, confident size.
    hit = {"producer": "Roumier", "title": "Musigny Grand Cru 2018 750ml", "price": 900, "shop": "s", "url": "u"}
    result = evaluate.evaluate_hit(hit, pricebook)
    assert result["tier"] == "grand_cru"
    assert result["expected_price"] == pytest.approx(200 * 4.5)
    assert result["classification"] == "FAIR"
    assert result["caveat"] is False  # producer verified, tier confident, size confident


def test_default_size_confidence_low_still_flags_caveat(pricebook):
    # No explicit size text -- this is the common case (most listings don't
    # spell out "750ml"), and it should still surface a caveat per the
    # "never suppress, just flag" rule, even for an otherwise-verified,
    # confidently-tiered producer.
    hit = {"producer": "Roumier", "title": "Musigny Grand Cru 2018", "price": 900, "shop": "s", "url": "u"}
    result = evaluate.evaluate_hit(hit, pricebook)
    assert result["size_confidence"] == "low"
    assert result["classification"] != "NOREF"
    assert result["caveat"] is True


def test_magnum_applies_format_multiplier(pricebook):
    hit = {"producer": "Labet", "title": "Domaine Labet Chardonnay Magnum 2020", "price": 100, "shop": "s", "url": "u"}
    result = evaluate.evaluate_hit(hit, pricebook)
    assert result["size_ml"] == 1500
    assert result["expected_price"] == pytest.approx(55 * 2.30)


# --- never suppress -----------------------------------------------------------

def test_unknown_producer_is_noref_but_still_returned(pricebook):
    hit = {"producer": "Some Unknown Producer", "title": "Whatever 2020", "price": 40, "shop": "s", "url": "u"}
    result = evaluate.evaluate_hit(hit, pricebook)
    assert result["classification"] == "NOREF"
    assert result["caveat"] is True
    assert result["producer"] == "Some Unknown Producer"  # hit data preserved, not dropped


def test_missing_observed_price_is_noref_but_still_returned(pricebook):
    hit = {"producer": "Labet", "title": "Domaine Labet Chardonnay 2020", "price": None, "shop": "s", "url": "u"}
    result = evaluate.evaluate_hit(hit, pricebook)
    assert result["classification"] == "NOREF"


def test_low_confidence_still_classifies_with_caveat(pricebook):
    # Burgundy producer, no tier keyword in the title -> tier undetected,
    # low confidence -- must still classify (not suppress) and flag caveat.
    hit = {"producer": "Roumier", "title": "Unlabelled Cuvee 2018", "price": 150, "shop": "s", "url": "u"}
    result = evaluate.evaluate_hit(hit, pricebook)
    assert result["tier"] is None
    assert result["tier_confidence"] == "low"
    assert result["classification"] != "NOREF"  # reference exists, still classified
    assert result["caveat"] is True


def test_evaluate_hits_never_drops_a_hit(pricebook):
    hits = [
        {"producer": "Labet", "title": "Domaine Labet Chardonnay 2020", "price": 40, "shop": "s", "url": "u1"},
        {"producer": "Unknown", "title": "Whatever 2020", "price": None, "shop": "s", "url": "u2"},
    ]
    results = evaluate.evaluate_hits(hits, pricebook)
    assert len(results) == 2


# --- price parser (vintage vs. real price) -- shared with scraper.parse_price

def test_price_parser_still_ignores_vintage_in_evaluate_context():
    import scraper
    assert scraper.parse_price("Chardonnay 2020 210,00 EUR") == pytest.approx(210.0)
