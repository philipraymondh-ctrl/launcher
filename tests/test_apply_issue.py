import ast
import copy
import datetime as dt

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

### Price quality

- [x] This is a real figure I checked myself (stamps today's date)
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
    new_src, new_book, summary = apply_issue.handle_producer(fields, scraper_src, copy.deepcopy(book))

    assert '"Zzz Test Domaine": ["zzztestalias", "zzz test domaine"],' in new_src
    entry = next(p for p in new_book["producers"] if p["name"] == "Zzz Test Domaine")
    assert entry["region"] == "burgundy"
    assert entry["reference_750_eur"] == 120
    assert entry["verified"] is True
    assert "Zzz Test Domaine" in summary


def test_edited_scraper_is_still_valid_python(scraper_src, book):
    fields = apply_issue.parse_form(PRODUCER_BODY)
    new_src, _, _ = apply_issue.handle_producer(fields, scraper_src, copy.deepcopy(book))

    tree = ast.parse(new_src)  # raises SyntaxError if we mangled the file
    ns = {}
    producers = next(
        n for n in tree.body
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", None) == "PRODUCERS"
    )
    exec(compile(ast.Module([producers], []), "<t>", "exec"), ns)
    assert ns["PRODUCERS"]["Zzz Test Domaine"] == ["zzztestalias", "zzz test domaine"]


def test_verified_tick_without_a_price_is_rejected(scraper_src, book):
    # Ticking "verified" cannot mark an entry verified with no price behind
    # it -- that would claim a checked figure that does not exist.
    fields = apply_issue.parse_form(body_with(PRODUCER_BODY, "Reference price, EUR per 750ml", "_No response_"))
    with pytest.raises(InvalidSubmission, match="no reference price"):
        apply_issue.handle_producer(fields, scraper_src, copy.deepcopy(book))


def test_unticked_price_source_stays_unverified(scraper_src, book):
    fields = apply_issue.parse_form(PRODUCER_BODY.replace("- [x]", "- [ ]"))
    _, new_book, _ = apply_issue.handle_producer(fields, scraper_src, copy.deepcopy(book))

    entry = next(p for p in new_book["producers"] if p["name"] == "Zzz Test Domaine")
    assert entry["verified"] is False


def test_price_accepts_euro_sign_and_comma(scraper_src, book):
    fields = apply_issue.parse_form(body_with(PRODUCER_BODY, "Reference price, EUR per 750ml", "€ 89,50"))
    _, new_book, _ = apply_issue.handle_producer(fields, scraper_src, copy.deepcopy(book))

    entry = next(p for p in new_book["producers"] if p["name"] == "Zzz Test Domaine")
    assert entry["reference_750_eur"] == pytest.approx(89.5)


def test_existing_producer_is_updated_not_rejected(scraper_src, book):
    # Upsert: naming an existing producer edits it. This is what makes
    # "correct a price" the same one-step action as "add a producer".
    fields = apply_issue.parse_form(body_with(PRODUCER_BODY, "Producer name", "Ganevat"))
    new_src, new_book, summary = apply_issue.handle_producer(fields, scraper_src, copy.deepcopy(book))

    entry = next(p for p in new_book["producers"] if p["name"] == "Ganevat")
    assert entry["reference_750_eur"] == 120
    assert entry["verified"] is True
    assert entry["last_verified"] == dt.date.today().isoformat()
    assert summary.startswith("Updated producer")
    # Exactly one Ganevat line survives in PRODUCERS -- no duplicate.
    assert new_src.count('"Ganevat":') == 1


def test_unknown_region_rejected(scraper_src, book):
    fields = apply_issue.parse_form(body_with(PRODUCER_BODY, "Region", "atlantis"))
    with pytest.raises(InvalidSubmission, match="not one of"):
        apply_issue.handle_producer(fields, scraper_src, copy.deepcopy(book))


def test_non_numeric_price_rejected(scraper_src, book):
    fields = apply_issue.parse_form(body_with(PRODUCER_BODY, "Reference price, EUR per 750ml", "cheap"))
    with pytest.raises(InvalidSubmission, match="not a number"):
        apply_issue.handle_producer(fields, scraper_src, copy.deepcopy(book))


def test_missing_required_field_rejected(scraper_src, book):
    fields = apply_issue.parse_form(body_with(PRODUCER_BODY, "Aliases", "_No response_"))
    with pytest.raises(InvalidSubmission):
        apply_issue.handle_producer(fields, scraper_src, copy.deepcopy(book))


@pytest.mark.parametrize("hostile", [
    'Evil", "x": ["y"], "z',   # closes the string, injects a dict entry
    'Back\\slash',             # backslash escape
])
def test_quote_injection_in_producer_name_rejected(hostile, scraper_src, book):
    # The repo is public, so any stranger can submit an issue form. A name
    # that closes the string literal must never reach scraper.py.
    fields = apply_issue.parse_form(body_with(PRODUCER_BODY, "Producer name", hostile))
    with pytest.raises(InvalidSubmission, match="quotes or backslashes"):
        apply_issue.handle_producer(fields, scraper_src, copy.deepcopy(book))


def test_quote_injection_in_alias_rejected(scraper_src, book):
    fields = apply_issue.parse_form(body_with(PRODUCER_BODY, "Aliases", 'ok, bad"], "X": ["y'))
    with pytest.raises(InvalidSubmission, match="quotes or backslashes"):
        apply_issue.handle_producer(fields, scraper_src, copy.deepcopy(book))


# --- shops -------------------------------------------------------------------

def test_add_shop_is_never_verified(scraper_src, tmp_path):
    fields = apply_issue.parse_form(SHOP_BODY)

    new_src, summary = apply_issue.handle_shop(fields, scraper_src)

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

    new_src, _ = apply_issue.handle_shop(fields, scraper_src)

    assert '"name": "mixedcase"' in new_src


@pytest.mark.parametrize("bad_name", [
    "Has Spaces", "../../etc/passwd", "semi;colon", "a", "x" * 41, "-leading", "under_score",
])
def test_invalid_shop_names_rejected(bad_name, scraper_src):
    fields = apply_issue.parse_form(body_with(SHOP_BODY, "Short name", bad_name))
    with pytest.raises(InvalidSubmission, match="must be"):
        apply_issue.handle_shop(fields, scraper_src)


@pytest.mark.parametrize("bad_url", [
    "http://insecure.example.com", "ftp://x.example.com", "javascript:alert(1)", "example.com",
])
def test_non_https_shop_url_rejected(bad_url, scraper_src):
    fields = apply_issue.parse_form(body_with(SHOP_BODY, "Shop URL", bad_url))
    with pytest.raises(InvalidSubmission, match="https://"):
        apply_issue.handle_shop(fields, scraper_src)


def test_quote_injection_in_shop_url_rejected(scraper_src):
    fields = apply_issue.parse_form(
        body_with(SHOP_BODY, "Shop URL", 'https://x.example.com", "verified": True, "z": "')
    )
    with pytest.raises(InvalidSubmission, match="illegal characters"):
        apply_issue.handle_shop(fields, scraper_src)


def test_existing_shop_is_repointed_and_unverified(scraper_src):
    fields = apply_issue.parse_form(body_with(SHOP_BODY, "Short name", "whynat"))
    new_src, summary = apply_issue.handle_shop(fields, scraper_src)

    entry = shops_of(new_src)["whynat"]
    assert entry["url"] == "https://zzztestshop.example.com"
    # Re-pointing invalidates any verification.
    assert entry["verified"] is False
    assert len([s for s in shops_of(new_src)]) == len(shops_of(scraper_src))
    assert "re-probe" in summary


def shops_of(src):
    """Exec just the SHOPS assignment out of a scraper.py source string."""
    tree = ast.parse(src)
    node = next(
        n for n in tree.body
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", None) == "SHOPS"
    )
    ns = {}
    exec(compile(ast.Module([node], []), "<t>", "exec"), ns)
    return {s["name"]: s for s in ns["SHOPS"]}


def producers_of(src):
    tree = ast.parse(src)
    node = next(
        n for n in tree.body
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", None) == "PRODUCERS"
    )
    ns = {}
    exec(compile(ast.Module([node], []), "<t>", "exec"), ns)
    return ns["PRODUCERS"]


# --- upsert: partial updates leave other fields alone ------------------------

def test_update_price_only_keeps_aliases_and_region(scraper_src, book):
    body = "### Producer name\n\nGanevat\n\n### Reference price, EUR per 750ml\n\n95\n"
    before = next(p for p in book["producers"] if p["name"] == "Ganevat")

    new_src, new_book, _ = apply_issue.handle_producer(
        apply_issue.parse_form(body), scraper_src, copy.deepcopy(book)
    )

    after = next(p for p in new_book["producers"] if p["name"] == "Ganevat")
    assert after["reference_750_eur"] == 95
    assert after["region"] == before["region"]
    # Aliases untouched in scraper.py because the field was left blank.
    assert producers_of(new_src)["Ganevat"] == producers_of(scraper_src)["Ganevat"]


def test_new_producer_without_alias_is_rejected(scraper_src, book):
    body = "### Producer name\n\nBrand New Domaine\n\n### Region\n\njura\n"
    with pytest.raises(InvalidSubmission, match="needs at least one alias"):
        apply_issue.handle_producer(apply_issue.parse_form(body), scraper_src, copy.deepcopy(book))


def test_new_producer_without_region_is_rejected(scraper_src, book):
    body = "### Producer name\n\nBrand New Domaine\n\n### Aliases\n\nbrandnew\n"
    with pytest.raises(InvalidSubmission, match="needs a region"):
        apply_issue.handle_producer(apply_issue.parse_form(body), scraper_src, copy.deepcopy(book))


# --- remove --------------------------------------------------------------------

def test_remove_producer(scraper_src, book):
    body = ("### Producer name\n\nGanevat\n\n### Danger zone\n\n"
            "- [x] Remove this producer instead (uses the name above)\n")
    new_src, new_book, summary = apply_issue.handle_producer(
        apply_issue.parse_form(body), scraper_src, copy.deepcopy(book)
    )

    assert "Ganevat" not in producers_of(new_src)
    assert not any(p["name"] == "Ganevat" for p in new_book["producers"])
    assert summary.startswith("Removed producer")


def test_remove_unknown_producer_is_rejected(scraper_src, book):
    body = ("### Producer name\n\nNope\n\n### Danger zone\n\n"
            "- [x] Remove this producer instead\n")
    with pytest.raises(InvalidSubmission, match="nothing to remove"):
        apply_issue.handle_producer(apply_issue.parse_form(body), scraper_src, copy.deepcopy(book))


def test_remove_shop_drops_only_that_block(scraper_src):
    body = ("### Short name\n\nwhynat\n\n### Danger zone\n\n"
            "- [x] Remove this shop instead (uses the name above)\n")
    before = shops_of(scraper_src)

    new_src, summary = apply_issue.handle_shop(apply_issue.parse_form(body), scraper_src)

    after = shops_of(new_src)
    assert "whynat" not in after
    assert set(before) - set(after) == {"whynat"}
    assert summary.startswith("Removed shop")


def test_remove_unknown_shop_is_rejected(scraper_src):
    body = "### Short name\n\nnosuchshop\n\n### Danger zone\n\n- [x] Remove this shop instead\n"
    with pytest.raises(InvalidSubmission, match="nothing to remove"):
        apply_issue.handle_shop(apply_issue.parse_form(body), scraper_src)


# --- bulk -------------------------------------------------------------------------

def test_bulk_adds_several_producers(scraper_src, book):
    body = ("### Producer name\n\nignored\n\n### Or add several at once\n\n"
            "Zzz One | zzzone | jura | 40\n"
            "Zzz Two | zzztwo, zzz two | burgundy\n"
            "# a comment line\n"
            "\n")
    new_src, new_book, summary = apply_issue.handle_producer(
        apply_issue.parse_form(body), scraper_src, copy.deepcopy(book)
    )

    producers = producers_of(new_src)
    assert producers["Zzz One"] == ["zzzone"]
    assert producers["Zzz Two"] == ["zzztwo", "zzz two"]
    one = next(p for p in new_book["producers"] if p["name"] == "Zzz One")
    assert one["reference_750_eur"] == 40 and one["region"] == "jura"
    two = next(p for p in new_book["producers"] if p["name"] == "Zzz Two")
    assert two["reference_750_eur"] is None
    # Bulk never marks anything verified -- no figure was vouched for.
    assert one["verified"] is False and two["verified"] is False
    assert summary.count("- ") == 2


def test_bulk_line_without_aliases_is_rejected(scraper_src, book):
    body = "### Or add several at once\n\nJust A Name\n"
    with pytest.raises(InvalidSubmission, match="needs at least"):
        apply_issue.handle_producer(apply_issue.parse_form(body), scraper_src, copy.deepcopy(book))


def test_bulk_bad_price_is_rejected(scraper_src, book):
    body = "### Or add several at once\n\nZzz One | zzzone | jura | free\n"
    with pytest.raises(InvalidSubmission, match="not a number"):
        apply_issue.handle_producer(apply_issue.parse_form(body), scraper_src, copy.deepcopy(book))


def test_bulk_result_is_valid_python(scraper_src, book):
    body = ("### Or add several at once\n\n"
            "Zzz One | zzzone | jura | 40\nZzz Two | zzztwo | burgundy | 60\n")
    new_src, _, _ = apply_issue.handle_producer(
        apply_issue.parse_form(body), scraper_src, copy.deepcopy(book)
    )
    ast.parse(new_src)
