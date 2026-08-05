"""Reading a shop whose catalogue is a document.

purewijnen has no price anywhere in its HTML -- three captured pages, zero
currency markers -- and publishes ~700 wines as a 41-page PDF, dated and
"replacing all previous". The fixture is a trimmed excerpt of the real
extracted text, contact details aside.

The danger here is the opposite of the HTML path's. There is no currency
marker anywhere in the price column, so this parser accepts a bare number --
and a document is full of numbers that are not prices.
"""
from pathlib import Path

import pytest

import autoselect
import crawler
import pdflist
import scraper

FIXTURES = Path(__file__).parent / "fixtures"
LIST_TEXT = (FIXTURES / "purewijnen-wijnlijst-excerpt.txt").read_text()
PDF_URL = "https://www.purewijnen.be/nl/system/files/attachments/wijnlijst_257.pdf"


def items():
    return pdflist.parse_wine_list(LIST_TEXT, PDF_URL)


# --- the bare-number licence, and what pays for it ---------------------------

def test_a_price_column_with_no_currency_marker_is_still_read():
    """scraper.PRICE_PATTERN reads nothing in this document -- there is no
    marker to be adjacent to."""
    assert scraper.PRICE_PATTERN.search("Granada Vigiriega 2011  26.90") is None
    assert pdflist._price_on("Granada Vigiriega 2011  26.90") == pytest.approx(26.90)


def test_both_decimal_separators_are_prices():
    assert pdflist._price_on("Brussel Gueuze-Lambic 2023  9,50") == pytest.approx(9.50)
    assert pdflist._price_on("VDF Gamay 2021  21.00") == pytest.approx(21.00)


def test_a_vintage_is_never_the_price():
    """The whole reason a decimal part is required: a year cannot have one."""
    assert pdflist._price_on("Arbois Savagnin 2023") is None
    assert pdflist._price_on("2023 ") is None
    assert pdflist._price_on("Cotes du Jura Trousseau 2023  ") is None


def test_the_shops_own_phone_number_is_not_a_price():
    """"052/12.34.56" ends in something shaped exactly like 34.56, on a line
    whose "wine" would be the letterhead. The left boundary is what stops it."""
    assert pdflist._price_on("052/12.34.56 ") is None
    assert pdflist._price_on("0470/11.22.33 ") is None


def test_a_postcode_or_street_number_is_not_a_price():
    assert pdflist._price_on(" Voorbeeldstraat 2 8560 Moorsele  ") is None


def test_the_letterhead_does_not_become_a_wine():
    for item in items():
        assert "example.be" not in item["text"]
        assert "Moorsele" not in item["title"]


def test_the_column_header_is_not_a_wine():
    assert not any("Naam Streek" in i["title"] for i in items())


# --- the entries themselves ---------------------------------------------------

def test_entries_are_read_with_prices_titles_and_urls():
    parsed = items()
    assert len(parsed) >= 8
    first = parsed[0]
    assert first["price"] == pytest.approx(26.90)
    assert "Baranco Oscuro" in first["title"]
    assert first["url"].startswith(PDF_URL + "#")


def test_a_price_on_its_own_line_still_closes_its_entry():
    """A multi-vintage entry puts the years on one line and the price on the
    next, with a footnote marker after it."""
    parsed = items()
    tissot = [i for i in parsed if "Bruy" in i["title"] and "Tissot" in i["text"]]
    assert tissot, "the multi-vintage entry was not read"
    assert tissot[0]["price"] == pytest.approx(59.00)


def test_every_entry_gets_its_own_url():
    """notify.item_key hashes shop + url + variant, so one URL for 700 wines
    would collapse the lot into a single remembered item -- and then one alert
    would silence the whole shop for thirty days."""
    parsed = items()
    assert len({i["url"] for i in parsed}) == len(parsed)


# --- SOLD is stock, not absence ----------------------------------------------

def test_sold_entries_are_kept_and_marked():
    """Both watched producers in the real list are SOLD. Dropping those rows
    throws away the most valuable thing this shop can say: nothing about a
    sold-out listing is persisted, so a restock reads as new and alerts."""
    sold = [i for i in items() if i["in_stock"] is False]
    assert len(sold) >= 6
    assert all(i["price"] is None for i in sold)


def test_the_watched_producers_in_the_list_are_matched():
    found = set()
    for item in items():
        found.update(scraper.match_producers(item["text"]))
    assert {"Domaine Calice", "Bruyere Houillon"} <= found


def test_a_sold_out_match_never_reaches_the_hits(monkeypatch):
    """The rule that makes a restock the most valuable alert this sends."""
    shop = {"name": "pdfshop", "platform": "html", "url": "https://x.test",
            "catalog_path": "list", "verified": True}
    monkeypatch.setattr(scraper, "FETCHERS", dict(
        scraper.FETCHERS, html=lambda *_: scraper.ParsedItems(items())))
    result = scraper.check_shop(shop, object())
    assert all(h["producer"] not in ("Domaine Calice", "Bruyere Houillon")
               for h in result), "a SOLD bottle reached hits.json"
    assert {h["producer"] for h in result.sold_out} == {"Domaine Calice",
                                                        "Bruyere Houillon"}


def test_the_list_names_its_own_edition():
    assert pdflist.list_date(LIST_TEXT).startswith("Wijnlijst")


# --- finding the document at all ----------------------------------------------

WIJNKAART_PAGE = """
<html><body><ul>
  <li><a href="/nl/algemene-voorwaarden.pdf">Algemene voorwaarden</a></li>
  <li><a href="/nl/system/files/attachments/wijnlijst_winkel-lpt-78jpikp_257.pdf">
      Wijnlijst</a></li>
  <li><a href="/nl/contact">Contact</a></li>
</ul></body></html>
"""


def test_the_pdf_link_is_discovered_not_recorded():
    """The URL carries a Drupal file id and a mangled slug, so it changes with
    every new edition. A recorded path would 404 the day the list is updated --
    which, for a list updated often, is most days, and silently."""
    found = autoselect.find_pdf_link(WIJNKAART_PAGE, "https://www.purewijnen.be/nl/wijnkaart")
    assert found.endswith("wijnlijst_winkel-lpt-78jpikp_257.pdf")


def test_the_terms_pdf_is_not_mistaken_for_the_wine_list():
    assert "voorwaarden" not in autoselect.find_pdf_link(
        WIJNKAART_PAGE, "https://www.purewijnen.be/nl/wijnkaart")


def test_a_page_with_no_pdf_yields_none():
    assert autoselect.find_pdf_link("<html><body><p>niets</p></body></html>",
                                    "https://x.test") is None


# --- the fetcher --------------------------------------------------------------

class PdfCrawler:
    """Serves the wijnkaart page, then the document as bytes."""

    def __init__(self, pdf_bytes=b"", pages=None, page_html=WIJNKAART_PAGE):
        self.pdf_bytes = pdf_bytes
        self.pages = pages
        self.page_html = page_html
        self.requested = []

    def get(self, url, params=None):
        self.requested.append(url)
        if url.endswith(".pdf"):
            return crawler.FetchResult(200, "", content=self.pdf_bytes)
        return crawler.FetchResult(200, self.page_html)


# The PDF route is a fallback inside fetch_html, not a platform of its own:
# a separate platform would mean four coupled edit sites in probe.py
# (candidate_endpoints, FETCHERS, EMPTY_PAGE, trim_payload), and missing the
# third makes the probe reject the shop it just read.
SHOP = {"name": "purewijnen", "platform": "html",
        "url": "https://www.purewijnen.be", "catalog_path": "nl/wijnkaart",
        "item_selector": "div.product", "title_selector": "h2.product-title",
        "price_selector": "span.price", "verified": True}


def test_the_fetcher_costs_two_requests(monkeypatch):
    monkeypatch.setattr(pdflist, "extract_text", lambda blob: ([LIST_TEXT], None))
    client = PdfCrawler()
    parsed = scraper.fetch_html(SHOP, client)
    assert len(client.requested) == 2
    assert client.requested[0].endswith("/nl/wijnkaart")
    assert client.requested[1].endswith(".pdf")
    assert len(parsed) >= 8


def test_a_list_that_moved_is_reported_not_guessed(monkeypatch):
    client = PdfCrawler(page_html="<html><body><p>geen lijst</p></body></html>")
    parsed = scraper.fetch_html(SHOP, client)
    assert parsed == []
    assert not any(u.endswith(".pdf") for u in client.requested)


def test_an_unreadable_document_is_a_failure_not_an_empty_shop(monkeypatch):
    """A scanned list extracts to empty pages. "No entries" from a document is
    indistinguishable from "this shop stocks nothing" unless it is raised."""
    monkeypatch.setattr(pdflist, "extract_text", lambda blob: (["", "", ""], None))
    with pytest.raises(scraper.UnreadableDocumentError):
        scraper.fetch_html(SHOP, PdfCrawler())


def test_a_corrupt_document_is_a_failure(monkeypatch):
    monkeypatch.setattr(pdflist, "extract_text",
                        lambda blob: (None, "PdfStreamError: broken"))
    with pytest.raises(scraper.UnreadableDocumentError):
        scraper.fetch_html(SHOP, PdfCrawler())


# --- the bytes path the document needs ----------------------------------------

class FakeResp:
    def __init__(self, status_code=200, text="", headers=None, content=b""):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.content = content


def test_a_pdf_survives_the_cache_as_bytes(monkeypatch, tmp_path):
    """resp.text of a PDF is mojibake, and a cache that stored it would hand
    the parser something unreadable on every hit for six hours."""
    blob = b"%PDF-1.4 fake bytes \x00\x01\x02"

    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/robots.txt"):
            return FakeResp(200, "")
        return FakeResp(200, "mojibake", {"Content-Type": "application/pdf"}, blob)

    monkeypatch.setattr(crawler.requests, "get", fake_get)
    monkeypatch.setattr(crawler.time, "sleep", lambda *_: None)
    client = crawler.Crawler(cache_dir=tmp_path, contact="https://example.com/bot")

    fresh = client.get("https://x.test/list.pdf")
    assert fresh.is_binary and fresh.content == blob

    cached = client.get("https://x.test/list.pdf")
    assert cached.from_cache and cached.content == blob
