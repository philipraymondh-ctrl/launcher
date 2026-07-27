import ast
import copy

import pytest
import yaml

import apply_issue
from apply_issue import InvalidSubmission


PRODUCER_BODY = """### Producer name

Zzz Test Domaine

### Aliases

zzztestalias, zzz test domaine

### Region

burgundy

### Reference price, EUR per 750ml

120

### Price source

- [x] This figure is a real one I checked myself (leave unticked for a guess)
"""

SHOP_BODY = """### Short name

zzztestshop

### Shop URL

https://zzztestshop.example.com
"""


@pytest.fixture(autouse=True)
def never_touch_the_real_repo(tmp_path, monkeypatch):
    """add_shop() writes a fixture file as a side effect. Redirect ROOT for
    every test so a case that unexpectedly succeeds can't litter the real
    tests/fixtures/ -- which is exactly what happened once."""
    monkeypatch.setattr(apply_issue, "ROOT", tmp_path)
    (tmp_path / "tests" / "fixtures").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def scraper_src():
    return apply_issue.SCRAPER_PATH.read_text()


@pytest.fixture
def book():
    with open(apply_issue.PRICES_PATH) as f:
        return yaml.safe_load(f)


def body_with(base, heading, value):
    """Replace one field's value in an issue-form body."""
    lines = base.split("\n")
    out, replacing = [], False
    for line in lines:
        if line.startswith("### "):
            replacing = line[4:].strip().lower() == heading.lower()
            out.append(line)
            if replacing:
                out.append("")
                out.append(value)
        elif not replacing:
            out.append(line)
    return "\n".join(out)


# --- form parsing ---------------------------------------------------------

def test_parse_form_extracts_fields():
    fields = apply_issue.parse_form(PRODUCER_BODY)
    assert fields["producer name"] == "Zzz Test Domaine"
    assert fields["aliases"] == "zzztestalias, zzz test domaine"
    assert fields["region"] == "burgundy"


def test_parse_form_treats_no_response_as_blank():
    fields = apply_issue.parse_form("### Reference price\n\n_No response_\n")
    assert fields["reference price"] == ""


def test_parse_form_handles_crlf():
    fields = apply_issue.parse_form("### Producer name\r\n\r\nX\r\n")
    assert fields["producer name"] == "X"


# --- producers -------------------------------------------------------------

def test_add_producer_updates_both_files(scraper_src, book):
    fields = apply_issue.parse_form(PRODUCER_BODY)
    new_src, new_book, summary = apply_issue.add_producer(fields, scraper_src, copy.deepcopy(book))

    assert '"Zzz Test Domaine": ["zzztestalias", "zzz test domaine"],' in new_src
    entry = next(p for p in new_book["producers"] if p["name"] == "Zzz Test Domaine")
    assert entry["region"] == "burgundy"
    assert entry["reference_750_eur"] == 120
    assert entry["verified"] is True
    assert "Zzz Test Domaine" in summary


def test_edited_scraper_is_still_valid_python(scraper_src, book):
    fields = apply_issue.parse_form(PRODUCER_BODY)
    new_src, _, _ = apply_issue.add_producer(fields, scraper_src, copy.deepcopy(book))

    tree = ast.parse(new_src)  # raises SyntaxError if we mangled the file
    ns = {}
    producers = next(
        n for n in tree.body
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", None) == "PRODUCERS"
    )
    exec(compile(ast.Module([producers], []), "<t>", "exec"), ns)
    assert ns["PRODUCERS"]["Zzz Test Domaine"] == ["zzztestalias", "zzz test domaine"]


def test_blank_reference_gives_unverified_entry(scraper_src, book):
    fields = apply_issue.parse_form(body_with(PRODUCER_BODY, "Reference price, EUR per 750ml", "_No response_"))
    _, new_book, summary = apply_issue.add_producer(fields, scraper_src, copy.deepcopy(book))

    entry = next(p for p in new_book["producers"] if p["name"] == "Zzz Test Domaine")
    assert entry["reference_750_eur"] is None
    # Ticking "verified" cannot make an entry verified with no price behind it.
    assert entry["verified"] is False
    assert "NOREF" in summary


def test_unticked_price_source_stays_unverified(scraper_src, book):
    fields = apply_issue.parse_form(PRODUCER_BODY.replace("- [x]", "- [ ]"))
    _, new_book, _ = apply_issue.add_producer(fields, scraper_src, copy.deepcopy(book))

    entry = next(p for p in new_book["producers"] if p["name"] == "Zzz Test Domaine")
    assert entry["verified"] is False


def test_price_accepts_euro_sign_and_comma(scraper_src, book):
    fields = apply_issue.parse_form(body_with(PRODUCER_BODY, "Reference price, EUR per 750ml", "€ 89,50"))
    _, new_book, _ = apply_issue.add_producer(fields, scraper_src, copy.deepcopy(book))

    entry = next(p for p in new_book["producers"] if p["name"] == "Zzz Test Domaine")
    assert entry["reference_750_eur"] == pytest.approx(89.5)


def test_duplicate_producer_rejected(scraper_src, book):
    fields = apply_issue.parse_form(body_with(PRODUCER_BODY, "Producer name", "Ganevat"))
    with pytest.raises(InvalidSubmission, match="already in"):
        apply_issue.add_producer(fields, scraper_src, copy.deepcopy(book))


def test_unknown_region_rejected(scraper_src, book):
    fields = apply_issue.parse_form(body_with(PRODUCER_BODY, "Region", "atlantis"))
    with pytest.raises(InvalidSubmission, match="not one of"):
        apply_issue.add_producer(fields, scraper_src, copy.deepcopy(book))


def test_non_numeric_price_rejected(scraper_src, book):
    fields = apply_issue.parse_form(body_with(PRODUCER_BODY, "Reference price, EUR per 750ml", "cheap"))
    with pytest.raises(InvalidSubmission, match="not a number"):
        apply_issue.add_producer(fields, scraper_src, copy.deepcopy(book))


def test_missing_required_field_rejected(scraper_src, book):
    fields = apply_issue.parse_form(body_with(PRODUCER_BODY, "Aliases", "_No response_"))
    with pytest.raises(InvalidSubmission):
        apply_issue.add_producer(fields, scraper_src, copy.deepcopy(book))


@pytest.mark.parametrize("hostile", [
    'Evil", "x": ["y"], "z',   # closes the string, injects a dict entry
    'Back\\slash',             # backslash escape
])
def test_quote_injection_in_producer_name_rejected(hostile, scraper_src, book):
    # The repo is public, so any stranger can submit an issue form. A name
    # that closes the string literal must never reach scraper.py.
    fields = apply_issue.parse_form(body_with(PRODUCER_BODY, "Producer name", hostile))
    with pytest.raises(InvalidSubmission, match="quotes or backslashes"):
        apply_issue.add_producer(fields, scraper_src, copy.deepcopy(book))


def test_quote_injection_in_alias_rejected(scraper_src, book):
    fields = apply_issue.parse_form(body_with(PRODUCER_BODY, "Aliases", 'ok, bad"], "X": ["y'))
    with pytest.raises(InvalidSubmission, match="quotes or backslashes"):
        apply_issue.add_producer(fields, scraper_src, copy.deepcopy(book))


# --- shops -------------------------------------------------------------------

def test_add_shop_is_never_verified(scraper_src, tmp_path):
    fields = apply_issue.parse_form(SHOP_BODY)

    new_src, summary = apply_issue.add_shop(fields, scraper_src)

    assert '"name": "zzztestshop"' in new_src
    tree = ast.parse(new_src)
    shops = next(
        n for n in tree.body
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", None) == "SHOPS"
    )
    ns = {}
    exec(compile(ast.Module([shops], []), "<t>", "exec"), ns)
    entry = next(s for s in ns["SHOPS"] if s["name"] == "zzztestshop")
    assert entry["verified"] is False
    assert entry["url"] == "https://zzztestshop.example.com"
    assert (tmp_path / "tests" / "fixtures" / "zzztestshop.html").exists()
    assert "verified: false" in summary


def test_shop_name_is_normalised_to_lowercase(scraper_src):
    # Convenience, not a loophole: case is folded, but the character-set
    # rules below still apply to the folded name.
    fields = apply_issue.parse_form(body_with(SHOP_BODY, "Short name", "MixedCase"))

    new_src, _ = apply_issue.add_shop(fields, scraper_src)

    assert '"name": "mixedcase"' in new_src


@pytest.mark.parametrize("bad_name", [
    "Has Spaces", "../../etc/passwd", "semi;colon", "a", "x" * 41, "-leading", "under_score",
])
def test_invalid_shop_names_rejected(bad_name, scraper_src):
    fields = apply_issue.parse_form(body_with(SHOP_BODY, "Short name", bad_name))
    with pytest.raises(InvalidSubmission, match="must be"):
        apply_issue.add_shop(fields, scraper_src)


@pytest.mark.parametrize("bad_url", [
    "http://insecure.example.com", "ftp://x.example.com", "javascript:alert(1)", "example.com",
])
def test_non_https_shop_url_rejected(bad_url, scraper_src):
    fields = apply_issue.parse_form(body_with(SHOP_BODY, "Shop URL", bad_url))
    with pytest.raises(InvalidSubmission, match="https://"):
        apply_issue.add_shop(fields, scraper_src)


def test_quote_injection_in_shop_url_rejected(scraper_src):
    fields = apply_issue.parse_form(
        body_with(SHOP_BODY, "Shop URL", 'https://x.example.com", "verified": True, "z": "')
    )
    with pytest.raises(InvalidSubmission, match="illegal characters"):
        apply_issue.add_shop(fields, scraper_src)


def test_duplicate_shop_rejected(scraper_src):
    fields = apply_issue.parse_form(body_with(SHOP_BODY, "Short name", "whynat"))
    with pytest.raises(InvalidSubmission, match="already in"):
        apply_issue.add_shop(fields, scraper_src)
