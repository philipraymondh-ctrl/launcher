#!/usr/bin/env python3
"""Probe every unverified shop in SHOPS against its real endpoints.

This is the tool that unblocks shop verification. It has to run somewhere
with real outbound network (a GitHub Actions runner via
.github/workflows/probe.yml), because the dev sandbox has no general
internet egress.

For each shop it tries, in the order CLAUDE.md mandates:
  1. Shopify   {url}/products.json
  2. WooCommerce {url}/wp-json/wc/store/v1/products
  3. HTML      {url}
stopping at the first endpoint that both responds AND parses into products
via the real fetcher in scraper.py.

It saves the raw response body for every successful probe to
probe_output/, so those bodies can become real fixtures, and writes
probe_output/report.json summarising what was detected.

By default it changes nothing: it reports, and leaves SHOPS alone.

With `--apply` it also commits what it just observed -- saves the real
response as the shop's fixture (trimmed, keeping every producer match),
corrects the platform, and sets `verified: true`. That is allowed because
the fetch, the parse and the flag all happen in the *same run* against a
real response, which is exactly the condition
decisions/standing-decisions.md sets. It is never inferred from a previous
run, and a shop that failed to probe is always left unverified.

All requests go through crawler.Crawler, so robots.txt, the per-host rate
limit, backoff, the circuit breaker and the run budget all apply exactly
as they do in a real scrape.
"""
import argparse
import json
import os
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

import apply_issue
import autoselect
import crawler
import pdflist
import scraper

OUTPUT_DIR = Path(os.environ.get("PROBE_OUTPUT_DIR", Path(__file__).parent / "probe_output"))


# An exhausted-catalogue payload per platform, so a paginating fetcher
# terminates after the single page the probe actually fetched instead of
# replaying page 1 forever.
EMPTY_PAGE = {"shopify": '{"products": []}', "woocommerce": "[]", "html": ""}

# Enough of a failing body to tell a bot-block page from an API change.
BODY_SNIPPET = 400
# Below this many products the page that won is still worth flagging as thin:
# it parsed, so the shop is not dark, but it is unlikely to be the whole
# catalogue.
GOOD_CATALOGUE_AT = 24
# A page has to hold at least this much to count as one of several
# catalogues worth recording.
MIN_CATALOGUE_PAGE = 8
# How many of them to record. Nine regions at twenty pages each would spend
# a whole run's budget on one shop.
MAX_CATALOGUE_PATHS = 6


class CannedCrawler:
    """Replays one already-fetched response into a real fetcher, so the
    probe validates the actual parse path without a second network call.

    The first call replays the body already fetched. What happens next
    depends on the adapter: a paginating fetcher should stop, so page two
    reads as empty; but an adapter that follows links -- the grower-index
    route -- has to be allowed to actually follow them, or a shop reachable
    only that way can never be verified. So later calls go to the real
    crawler when one is supplied, and read empty otherwise.
    """

    def __init__(self, response, platform, live=None, follow_budget=0):
        self._response = response
        self._empty = crawler.FetchResult(200, EMPTY_PAGE.get(platform, ""))
        self._served = False
        self._live = live
        self._follow_budget = follow_budget
        self.request_count = 0
        self.max_requests = 1 + follow_budget

    def get(self, url, params=None):
        if not self._served:
            self._served = True
            return self._response
        if self._live is not None and self._follow_budget > 0:
            self._follow_budget -= 1
            self.request_count += 1
            return self._live.get(url, params=params)
        return self._empty


DIAGNOSTIC_DIR = Path(__file__).parent / "probe_pages"
DIAGNOSTIC_CAP = 300_000


def _page_slug(url):
    parts = urlparse(url)
    path = parts.path.strip("/") or "index"
    # The query belongs in the slug for the same reason the host does:
    # /webshop and /webshop?format=json are two different answers to two
    # different questions, and slugging only the path filed both under
    # "webshop" -- the second silently overwriting the first.
    if parts.query:
        path = f"{path}-{parts.query}"
    return re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")[:64]


def looks_like_json(body):
    return body.lstrip()[:1] in ("{", "[")


def pdf_text(blob):
    """The text of a PDF, page by page, or a reason it could not be read."""
    return pdflist.extract_text(blob)


# A shop's own document often carries the owner's mailbox and mobile number
# in its header. They are published by the shop, but copying them into a
# public repo is gratuitous -- the parser needs the wine rows, not the
# letterhead -- and the same rule that keeps our own contact off the wire
# applies to somebody else's.
CONTACT_PATTERNS = (
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    re.compile(r"\b0\d{2,3}[/.\s-]\d{2}[.\s-]?\d{2}[.\s-]?\d{2}\b"),
)


def redact_contacts(text):
    for pattern in CONTACT_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text


def save_diagnostic_text(name, url, text, byte_count, page_count):
    """A PDF's extracted text, committed so a parser can be written from it."""
    DIAGNOSTIC_DIR.mkdir(parents=True, exist_ok=True)
    host = urlparse(url).netloc.replace(".", "-")
    path = DIAGNOSTIC_DIR / f"{name}.{host}.{_page_slug(url)}.txt"
    path.write_text(
        f"# {url}\n# {byte_count} bytes of PDF, {page_count} page(s), extracted "
        f"with pypdf.\n# Diagnostic only: delete once this shop parses.\n\n"
        + redact_contacts(text[:DIAGNOSTIC_CAP])
    )
    return path


def describe_price_lines(text):
    """What a wine-list PDF's text looks like to a price parser.

    The question a capture has to answer before any parsing is written: are
    the prices currency-marked, or a bare column of numbers? The rule this
    project enforces everywhere else -- a number is only a price when a
    currency marker touches it -- decides whether this shop is readable at
    all, and it can only be checked against the real text.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    marked = [ln for ln in lines if scraper.PRICE_PATTERN.search(ln)]
    decimals = [ln for ln in lines if re.search(r"\d+[.,]\d{2}(?!\d)", ln)]
    return (f"{len(lines)} non-empty line(s), {len(marked)} with a "
            f"currency-adjacent price, {len(decimals)} with a decimal number")


# Scripts are most of a page's bytes and none of its structure -- except when
# they are the only place the structure lives. A Squarespace commerce page
# renders its prices from Static.SQUARESPACE_CONTEXT, and many themes emit
# JSON-LD Product blocks while their visible markup says nothing. Stripping
# every script destroyed exactly the evidence needed to write the parser:
# purovino's capture recorded "4 currency-adjacent prices" in its header and
# then contained not one currency marker.
DATA_SCRIPT_MARKERS = ("SQUARESPACE_CONTEXT", "__NEXT_DATA__", "__NUXT__",
                       "window.ShopifyAnalytics", "dataLayer.push")
DATA_SCRIPT_CAP = 20_000


def _is_data_script(tag):
    if (tag.get("type") or "").lower() in ("application/ld+json", "application/json"):
        return True
    body = tag.string or ""
    return any(marker in body for marker in DATA_SCRIPT_MARKERS)


def save_diagnostic_page(name, url, body):
    """Commit a readable copy of a page that responded but parsed to nothing.

    The full body goes to the run artifact, but artifacts live behind blob
    storage that the dev sandbox has no route to, so the only way this
    evidence reaches the person writing the parser is through git. Scripts
    and styles are stripped -- they are most of the bytes and none of the
    structure -- except the ones that carry data rather than behaviour.
    """
    DIAGNOSTIC_DIR.mkdir(parents=True, exist_ok=True)
    host = urlparse(url).netloc.replace(".", "-")
    stem = f"{name}.{host}.{_page_slug(url)}"

    # A JSON body put through an HTML parser comes out as one text node with
    # its own punctuation re-escaped: unreadable, and unusable as a fixture.
    # Save it as it arrived.
    if looks_like_json(body):
        (DIAGNOSTIC_DIR / f"{stem}.json").write_text(body[:DIAGNOSTIC_CAP])
        return

    soup = BeautifulSoup(body, "html.parser")
    for tag in soup(["style", "noscript", "svg"]):
        tag.decompose()
    for tag in soup("script"):
        if _is_data_script(tag):
            tag.string = (tag.string or "")[:DATA_SCRIPT_CAP]
        else:
            tag.decompose()
    trimmed = soup.prettify()[:DIAGNOSTIC_CAP]
    (DIAGNOSTIC_DIR / f"{stem}.html").write_text(
        f"<!-- {url}\n     {describe_unparsed(body)}\n"
        f"     Styles and behaviour-only scripts stripped, data scripts kept\n"
        f"     (capped at {DATA_SCRIPT_CAP} bytes each); file capped at\n"
        f"     {DIAGNOSTIC_CAP} bytes.\n"
        f"     Diagnostic only: delete once this shop parses. -->\n" + trimmed
    )


def describe_unparsed(body):
    """Why a page that responded produced nothing.

    The whole page is in the artifact, but artifacts sit behind blob
    storage, so this has to be small enough to read in a job log and
    specific enough to act on.
    """
    soup = BeautifulSoup(body, "html.parser")
    text = soup.get_text(" ", strip=True)
    prices = scraper.PRICE_PATTERN.findall(text)
    links = [a.get("href", "") for a in soup.find_all("a", href=True)]
    product_ish = [h for h in links if re.search(r"/(vin|produit|product|bouteille)", h, re.I)]
    scripts = len(soup.find_all("script"))
    counts = {}
    for el in soup.find_all(True):
        key = el.name + ("." + ".".join(el.get("class", [])) if el.get("class") else "")
        counts[key] = counts.get(key, 0) + 1
    common = sorted(counts.items(), key=lambda kv: -kv[1])[:6]
    return (
        f"{len(body)} bytes, {len(prices)} currency-adjacent price(s), "
        f"{len(links)} link(s) of which {len(product_ish)} look like products, "
        f"{scripts} script tag(s); commonest elements: "
        + ", ".join(f"{k}x{v}" for k, v in common)
        + (" | sample product links: " + ", ".join(product_ish[:3]) if product_ish else "")
    )


# How many catalogue paths the probe may guess at, per shop. One live run
# spent 24 requests on a single shop, every one a 404, and 107 of 150 across
# eight shops -- so with eleven unverified shops the budget binds and the
# last shops go unprobed. Which they do quietly, and that is the real cost.
MAX_CATALOGUE_GUESSES = 8


def parse_names(raw):
    """Shop names as typed into a text box on a phone.

    Commas, spaces or both: "Lapangee, lavinoterie" arrived as two shell
    arguments once and crashed the run, so neither separator may surprise
    anyone here either.
    """
    return [n for n in re.split(r"[,\s]+", (raw or "").strip()) if n]


def select_shops(shops, only=None, include_verified=False):
    """(shops to probe, names that matched nothing).

    Naming a shop is asking for it, verified or not -- "probe mareehaute"
    quietly probing nothing is worse than probing a shop twice. A name that
    matches nothing is returned rather than dropped: it is almost always a
    typo, and silently probing the empty set looks exactly like success.
    """
    # The example-* entries point at reserved .example.com domains that
    # deliberately do not resolve; probing them just burns requests.
    real = [s for s in shops if not s["name"].startswith("example-")]
    wanted = parse_names(only)
    if not wanted:
        return [s for s in real if include_verified or not s.get("verified", True)], []

    by_key = {s["name"].casefold(): s for s in real}
    chosen, unknown = [], []
    for name in wanted:
        shop = by_key.get(name.casefold())
        if shop is None:
            unknown.append(name)
        elif shop not in chosen:
            chosen.append(shop)
    return chosen, unknown


def report_unsaved(results, applied):
    """A read-only probe that found working shops has to say that it saved
    nothing.

    lavinoterie and pangee both parsed on 2026-08-04 -- Shopify, 250+
    products, Ganevat and Labet already on the shelf -- and stayed dark for
    three more days, because that run was read-only and said so only by
    omission.
    """
    if applied:
        return
    ok = [r["shop"] for r in results if r["status"] == "ok"]
    if not ok:
        return
    print()
    print(f"NOTHING WAS SAVED. {len(ok)} shop(s) answered and parsed cleanly: "
          f"{', '.join(ok)}.")
    print("This was a read-only probe, so they are still verified:false and "
          "still skipped on every run.")
    print("Re-run the probe with apply ticked to save their fixtures and set "
          "verified:true.")


def candidate_endpoints(shop):
    base = shop["url"].rstrip("/")
    # A shop may list nothing on its landing page. Probing the base URL
    # then reports "responded, zero products" for a shop whose catalogue
    # parses perfectly one path over.
    endpoints = [
        ("shopify", f"{base}/products.json", {"limit": 250}, "json"),
        ("woocommerce", f"{base}/wp-json/wc/store/v1/products", {"per_page": 100}, "json"),
    ]
    # A recorded catalog_path goes first, but must not *replace* the search:
    # the path may be a guess, and short-circuiting here meant the one shop
    # catalogue discovery was written for only ever tried its guessed path.
    # The hourly run still fetches the single recorded page; only the probe
    # explores.
    # The landing page first, always: it is where the shop's own menu lives,
    # and nothing may be accepted before that menu has been read. Putting a
    # recorded catalog_path ahead of it let a wrong path confirm itself on
    # every re-probe. The recorded path comes next, then the guesses.
    recorded = [shop[k] for k in ("catalog_path",) if shop.get(k)]
    recorded += list(shop.get("catalog_paths") or [])
    candidates = [""] + recorded + [p for p in autoselect.CATALOGUE_PATHS if p]
    seen, paths = set(), []
    for path in candidates:
        url = urljoin(base + "/", path) if path else shop["url"]
        if url in seen:
            continue
        seen.add(url)
        paths.append(("html", url, None, "html"))
        if len(paths) >= MAX_CATALOGUE_GUESSES:
            break
    return endpoints + paths


# How many links an index-following adapter may chase during a probe.
# Six was not enough: leszinzinsduvin's first few growers happen to have
# nothing listed, so the probe saw zero products for a route that works
# and reported the shop unparseable. It has to be able to reach every
# grower the index matched, or absence of stock at the front of the
# alphabet reads as a broken adapter.
PROBE_FOLLOW_BUDGET = autoselect.MAX_INDEX_LINKS


def try_parse(platform, shop, response, live=None):
    """Run the real fetcher for `platform` against an already-fetched
    response. Returns (items, error_string)."""
    probe_shop = dict(shop)
    probe_shop["platform"] = platform
    if platform == "html":
        probe_shop.setdefault("item_selector", "div.product")
        probe_shop.setdefault("title_selector", "h2.product-title")
        probe_shop.setdefault("price_selector", "span.price")
    try:
        canned = CannedCrawler(response, platform, live=live,
                               follow_budget=PROBE_FOLLOW_BUDGET if platform == "html" else 0)
        items = scraper.FETCHERS[platform](probe_shop, canned)
        return items, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def probe_shop(shop, crawler_client):
    """Try each candidate endpoint until one responds and parses."""
    result = {
        "shop": shop["name"],
        "url": shop["url"],
        "declared_platform": shop["platform"],
        "detected_platform": None,
        "status": "failed",
        "endpoint": None,
        "products_parsed": 0,
        "producer_hits": [],
        "saved_as": None,
        "truncated": False,
        "attempts": [],
        "catalog_path": shop.get("catalog_path"),
    }
    # (paginates, count). Page-one product count alone chose pangee's
    # "new arrivals" strip over its catalogue and winenot's sparkling-wine
    # filter over nine region categories: a strip is one page, a catalogue
    # runs to twenty, and the "next" link is free to check.
    best_html = {"rank": (False, 0), "count": 0, "body": None, "url": None,
                 "items": []}
    # Every page that looked like a real catalogue. A shop whose wines live
    # under nine region categories has no single page to record, and picking
    # one reads one region.
    catalogues = []

    # A list rather than a loop over a fixed sequence: the first HTML body
    # fetched contributes its own navigation links to the search, because no
    # guessed path list knows that a shop calls its catalogue "/la-cave".
    queue = list(candidate_endpoints(shop))
    seen_urls = {c[1] for c in queue}
    followed_menu = False

    while queue:
        platform, url, params, kind = queue.pop(0)
        attempt = {"platform": platform, "url": url}
        try:
            response = crawler_client.get(url, params=params)
        except crawler.Disallowed:
            attempt["outcome"] = "robots.txt disallows"
            result["attempts"].append(attempt)
            continue
        except crawler.CircuitOpen:
            attempt["outcome"] = "circuit breaker open for host"
            result["attempts"].append(attempt)
            result["status"] = "failed"
            break
        except crawler.BudgetExceeded:
            attempt["outcome"] = "run request budget exhausted"
            result["attempts"].append(attempt)
            result["status"] = "not_reached"
            return result
        except crawler.UpstreamError as e:
            attempt["outcome"] = f"unreachable: {e}"
            result["attempts"].append(attempt)
            if e.status_code is None:
                # The connection itself never succeeded, so this host is
                # down/blocked -- its other endpoints cannot be up. Bail
                # out instead of burning 2 more retry cycles (~60s and 6
                # requests) rediscovering that. An HTTP status (404 etc.)
                # is different: the host is fine, just not that platform.
                result["status"] = "host_unreachable"
                return result
            continue

        body = response.text or ""
        attempt["bytes"] = len(body)

        if not body.strip():
            attempt["outcome"] = "empty body (likely JS-rendered)"
            result["attempts"].append(attempt)
            continue

        items, parse_error = try_parse(platform, shop, response, live=crawler_client)

        if platform == "html" and not followed_menu:
            # Before the "parsed nothing" exits below: a landing page may be
            # pure navigation, and that page's menu is the whole reason we
            # can find a catalogue nobody configured. Products already read
            # here are excluded -- a bottle's page is not a catalogue.
            followed_menu = True
            for link in autoselect.find_catalogue_links(
                    body, shop["url"],
                    exclude=[i.get("url", "") for i in (items or [])]):
                if link not in seen_urls:
                    seen_urls.add(link)
                    queue.append(("html", link, None, "html"))

        if parse_error:
            # Record what actually came back. Without this a failure like
            # vinopura's ("not JSON") is undiagnosable from the report --
            # the body is discarded because only successes are saved, and
            # you cannot tell a bot-block page from a real API change.
            attempt["outcome"] = f"responded but parse failed: {parse_error}"
            attempt["body_snippet"] = body[:BODY_SNIPPET].replace("\n", " ")
            result["attempts"].append(attempt)
            continue
        if not items:
            attempt["outcome"] = "responded and parsed, but zero products found"
            attempt["body_snippet"] = body[:BODY_SNIPPET].replace("\n", " ")
            if platform == "html":
                # These are the shops needing real selectors, so keep the
                # whole page -- selectors can't be written from a snippet.
                unparsed = OUTPUT_DIR / f"{shop['name']}.unparsed.html"
                unparsed.write_text(body)
                attempt["saved_unparsed"] = unparsed.name
                attempt["structure"] = describe_unparsed(body)
                save_diagnostic_page(shop["name"], url, body)
            result["attempts"].append(attempt)
            continue

        if not any((item.get("title") or "").strip() for item in items):
            # A hit with no name is unreadable in a digest. Rejecting the
            # shop here, rather than letting the fixture test fail the run,
            # keeps one unusable adapter from blocking every other shop in
            # the same batch from being committed.
            attempt["outcome"] = (
                f"parsed {len(items)} product(s) but none had a usable title"
            )
            attempt["products"] = len(items)
            result["attempts"].append(attempt)
            if platform == "html":
                attempt["structure"] = describe_unparsed(body)
                save_diagnostic_page(shop["name"], url, body)
            continue

        paginates = bool(autoselect.find_next_page(body, url)) if platform == "html" else False
        # No HTML page is ever accepted on the spot -- every candidate is
        # weighed and the best wins. Short-circuiting on "this looks like a
        # catalogue" meant a recorded catalog_path, which is tried early by
        # design, was taken before the shop's menu had been read: a wrong
        # path confirmed itself on every re-probe, three times over for
        # winenot and pangee. A JSON platform still ends the search, because
        # /products.json either is the catalogue or is not there.
        if platform == "html":
            # A landing page's "featured wines" strip parses fine and is not
            # the catalogue. Note it and keep looking; fall back to it only
            # if nothing richer turns up.
            attempt["outcome"] = (
                f"parsed {len(items)} product(s)"
                f"{', paginates' if paginates else ', single page'}"
                f" -- keeping it in case nothing better turns up"
            )
            attempt["products"] = len(items)
            attempt["paginates"] = paginates
            result["attempts"].append(attempt)
            if paginates and len(items) >= MIN_CATALOGUE_PAGE:
                catalogues.append((len(items), url))
            if (paginates, len(items)) > best_html["rank"]:
                best_html.update(rank=(paginates, len(items)), count=len(items),
                                 body=body, url=url, items=items)
            continue

        ext = "json" if kind == "json" else "html"
        saved = OUTPUT_DIR / f"{shop['name']}.{ext}"
        saved.write_text(body)

        hits = sorted({
            producer
            for item in items
            for producer in scraper.match_producers(item["text"])
        })

        # The probe fetches exactly one page. A full page means the real
        # catalogue is larger, so these producer_hits are a lower bound --
        # the scraper paginates, but this report does not.
        page_size = {"shopify": scraper.SHOPIFY_PAGE_SIZE, "woocommerce": scraper.WOO_PAGE_SIZE}
        truncated = len(items) >= page_size.get(platform, len(items) + 1)

        attempt["outcome"] = f"ok, {len(items)} product(s)"
        result["attempts"].append(attempt)
        result.update(
            detected_platform=platform,
            status="ok",
            endpoint=url,
            products_parsed=len(items),
            producer_hits=hits,
            saved_as=str(saved.relative_to(OUTPUT_DIR.parent)),
            truncated=truncated,
            catalog_path=_relative_path(shop, url),
        )
        return result

    # Nothing rich turned up, so a thin page beats no page: six real
    # products still catch a producer, and the alternative is a shop that
    # stays dark.
    if best_html["count"]:
        saved = OUTPUT_DIR / f"{shop['name']}.html"
        saved.write_text(best_html["body"])
        hits = sorted({
            producer
            for item in best_html["items"]
            for producer in scraper.match_producers(item["text"])
        })
        result.update(
            detected_platform="html",
            status="ok",
            endpoint=best_html["url"],
            products_parsed=best_html["count"],
            producer_hits=hits,
            saved_as=str(saved.relative_to(OUTPUT_DIR.parent)),
            truncated=False,
            catalog_path=_relative_path(shop, best_html["url"]),
            thin=best_html["count"] < GOOD_CATALOGUE_AT,
        )
        if len(catalogues) > 1:
            # No page held the whole catalogue, so record the ones that each
            # held part of it -- richest first, capped.
            ordered = [u for _, u in sorted(catalogues, reverse=True)]
            # One page reached two ways ("shop" and "http://host/shop", or a
            # trailing slash) was recorded as two catalogues: one page, two
            # requests every run, and two different rotation orders. The
            # candidate list dedupes by exact URL, which these are not.
            deduped, seen_pages = [], set()
            for url in ordered:
                key = url.replace("http://", "https://").rstrip("/")
                if key not in seen_pages:
                    seen_pages.add(key)
                    deduped.append(url)
            result["catalog_paths"] = [
                _relative_path(shop, u) or "" for u in deduped[:MAX_CATALOGUE_PATHS]
            ]
            result["products_parsed"] = sum(c for c, _ in catalogues)
    return result


def _relative_path(shop, url):
    """The catalogue path to record, relative to the shop's base URL.

    A page outside that base is recorded as an absolute URL rather than
    discarded: pangee's base is /fr and the catalogue it offers is
    /nouveaux-produits, so returning None left the config pointing at the
    landing page while the saved fixture showed the richer one -- a shop
    whose fixture no longer describes what the run fetches. `fetch_html`
    urljoins this, and urljoin passes an absolute URL straight through.
    """
    base = shop["url"].rstrip("/") + "/"
    if url == base or url == base.rstrip("/"):
        return None
    return url[len(base):] if url.startswith(base) else url


# --- turning a probe result into committed config ----------------------------

FIXTURE_SAMPLE = 25


def trim_payload(platform, body, keep=FIXTURE_SAMPLE):
    """Shrink a real response to a fixture-sized one, keeping every product
    that matches a tracked producer plus a sample of the rest.

    The result is still real data from the shop -- just fewer rows, so the
    repo doesn't carry megabytes of catalogue. Keeping the matches means the
    fixture still exercises real producer matching rather than only shape.
    """
    if platform == "shopify":
        payload = json.loads(body)
        products = payload.get("products", [])
        text_of = lambda p: f"{p.get('title','')} {p.get('vendor','')} {p.get('body_html','')}"
    elif platform == "woocommerce":
        products = json.loads(body)
        text_of = lambda p: f"{p.get('name','')} {p.get('short_description','')} {p.get('description','')}"
    else:
        return body, len(body)

    matched = [p for p in products if scraper.match_producers(text_of(p))]
    others = [p for p in products if p not in matched][: max(0, keep - len(matched))]
    kept = matched + others
    note = (
        f"Real response from this shop, trimmed for the repo: {len(kept)} of "
        f"{len(products)} products, keeping every tracked-producer match "
        f"({len(matched)}) plus a sample. Captured by probe.py --apply."
    )

    if platform == "shopify":
        return json.dumps({"_fixture_note": note, "products": kept}, indent=2) + "\n", len(products)
    trimmed = list(kept)
    if trimmed:
        trimmed[0] = {"_fixture_note": note, **trimmed[0]}
    return json.dumps(trimmed, indent=2) + "\n", len(products)


def apply_result(result, src):
    """Point a shop's SHOPS entry at what was actually observed, and mark it
    verified -- only ever called for a response fetched and parsed in this
    same run, which is what decisions/standing-decisions.md requires."""
    name, platform = result["shop"], result["detected_platform"]
    span = apply_issue.find_shop_block(src, name)
    if span is None:
        return src, False
    block = src[span[0]:span[1]]

    block = re.sub(r'("platform": ")[a-z]+(")', lambda m: m.group(1) + platform + m.group(2), block, count=1)
    if platform == "html":
        # HTML shops need selectors; a JSON shop must not carry stale ones.
        if '"item_selector"' not in block:
            block = re.sub(
                r'(\n[ \t]*"url": "[^"]*",\n)',
                lambda m: m.group(1)
                + '        "item_selector": "div.product",\n'
                  '        "title_selector": "h2.product-title",\n'
                  '        "price_selector": "span.price",\n',
                block, count=1,
            )
    else:
        block = re.sub(r'\n[ \t]*"(?:item|title|price)_selector": "[^"]*",', "", block)

    # Record where the catalogue actually was, so the hourly run fetches the
    # right page(s) instead of rediscovering them every time. A list when the
    # shop splits its wines across region categories with no page holding all
    # of them, a single path otherwise.
    paths = result.get("catalog_paths")
    path = result.get("catalog_path")
    if platform == "html" and (paths or path):
        literal = (
            '"catalog_paths": [' + ", ".join(f'"{p}"' for p in paths) + "],"
            if paths else f'"catalog_path": "{path}",'
        )
        # Whichever form the entry carried, replace it with the new one.
        if re.search(r'"catalog_paths?": ', block):
            block = re.sub(r'[ \t]*"catalog_path": "[^"]*",\n', "", block)
            block = re.sub(r'[ \t]*"catalog_paths": \[[^\]]*\],\n', "", block)
        block = re.sub(r'(\n[ \t]*"url": "[^"]*",\n)',
                       lambda m: m.group(1) + f"        {literal}\n",
                       block, count=1)

    block = re.sub(r'("verified": )False', lambda m: m.group(1) + "True", block, count=1)
    return src[:span[0]] + block + src[span[1]:], True


def apply_results(results):
    """Write real fixtures and flip verified for every shop probed OK."""
    src = scraper_source_path().read_text()
    applied, skipped = [], []
    for result in results:
        if result["status"] != "ok":
            skipped.append(result["shop"])
            continue
        platform = result["detected_platform"]
        raw = (OUTPUT_DIR / Path(result["saved_as"]).name).read_text()
        trimmed, total = trim_payload(platform, raw)
        ext = "html" if platform == "html" else "json"
        fixtures = Path(__file__).parent / "tests" / "fixtures"
        for stale in fixtures.glob(f"{result['shop']}.*"):
            stale.unlink()
        (fixtures / f"{result['shop']}.{ext}").write_text(trimmed)
        src, ok = apply_result(result, src)
        (applied if ok else skipped).append(result["shop"])
    scraper_source_path().write_text(src)
    return applied, skipped


def scraper_source_path():
    return Path(scraper.__file__)


def main():
    parser = argparse.ArgumentParser(description="Probe shops' real endpoints.")
    parser.add_argument("--only", help="Comma-separated shop names to probe (default: all unverified)")
    parser.add_argument("--include-verified", action="store_true", help="Also probe already-verified shops")
    parser.add_argument(
        "--capture",
        help="Comma-separated URLs to fetch and save to probe_pages/ for inspection. "
             "Diagnostic only: parses nothing, verifies nothing, commits no config.",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Save real fixtures, correct platforms and set verified:true for shops probed OK",
    )
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.capture:
        # There is no other way to see a page from here: the sandbox has no
        # egress and run artifacts live behind blob storage it cannot reach.
        # Committing a trimmed copy is the only channel back.
        client = crawler.Crawler()
        for url in [u.strip() for u in args.capture.split(",") if u.strip()]:
            try:
                resp = client.get(url)
                resp.raise_for_status()
            except Exception as e:
                print(f"CAPTURE {url}: {type(e).__name__}: {e}")
                continue
            if resp.is_binary:
                # A PDF is the one body worth committing as text rather than
                # as itself: the text is what a parser will read, and the
                # original is someone else's document.
                pages, why = pdf_text(resp.content)
                if pages is None:
                    print(f"CAPTURE {url}: {len(resp.content)} bytes of PDF, "
                          f"unreadable: {why}")
                    continue
                text = "\n\n".join(pages)
                path = save_diagnostic_text("capture", url, text, len(resp.content),
                                            len(pages))
                print(f"CAPTURE {url}: {len(resp.content)} bytes of PDF, "
                      f"{len(pages)} page(s), {len(text)} chars of text -> {path.name}")
                print(f"         {describe_price_lines(text)}")
                continue
            save_diagnostic_page("capture", url, resp.text)
            items = autoselect.find_products(
                resp.text, url, scraper.PRICE_PATTERN, scraper.parse_price)
            print(f"CAPTURE {url}: {len(resp.text)} bytes, "
                  f"autoselect reads {len(items)} product(s)")
            print(f"         {describe_unparsed(resp.text)}")
        return

    shops, unknown = select_shops(
        scraper.SHOPS, only=args.only, include_verified=args.include_verified)
    if unknown:
        known = ", ".join(sorted(
            s["name"] for s in scraper.SHOPS if not s["name"].startswith("example-")))
        print(f"No shop is called {', '.join(repr(n) for n in unknown)}.")
        print(f"Shops that do exist: {known}")
        raise SystemExit(2)
    if not shops:
        print("Nothing to probe: every shop is already verified. Pass "
              "--include-verified (or name one) to re-probe.")
        raise SystemExit(2)

    crawler_client = crawler.Crawler()
    print(f"Probing {len(shops)} shop(s) as {crawler_client.user_agent}")
    print(f"Request budget: {crawler_client.max_requests}\n")

    report_path = OUTPUT_DIR / "report.json"
    results = []
    for i, shop in enumerate(shops, 1):
        result = probe_shop(shop, crawler_client)
        results.append(result)
        print(
            f"[{i}/{len(shops)}] {result['shop']}: {result['status']}"
            f"{' via ' + result['detected_platform'] if result['detected_platform'] else ''}"
        )
        # Rewrite after every shop: an unreachable host costs up to ~3min
        # in backoff, so a slow run can hit the job timeout. Incremental
        # writes mean the artifact still carries everything probed so far.
        report_path.write_text(json.dumps(results, indent=2, sort_keys=True))

    print()

    name_w = max([len(r["shop"]) for r in results] + [4])
    status_w = max([len(r["status"]) for r in results] + [6])
    print(f"{'SHOP':<{name_w}} | {'STATUS':<{status_w}} | {'PLATFORM':<12} | PRODUCTS | PRODUCER HITS")
    print("-" * (name_w + status_w + 50))
    for r in results:
        hits = ", ".join(r["producer_hits"]) or "-"
        platform = r["detected_platform"] or "-"
        count = f"{r['products_parsed']}{'+' if r.get('truncated') else ''}"
        print(
            f"{r['shop']:<{name_w}} | {r['status']:<{status_w}} | {platform:<12} | "
            f"{count:>8} | {hits}"
        )

    ok = [r for r in results if r["status"] == "ok"]
    print(f"\n{len(ok)}/{len(results)} shop(s) probed successfully.")
    print(f"Requests used: {crawler_client.request_count}/{crawler_client.max_requests}")
    if crawler_client.skipped_disallowed:
        print(f"robots.txt disallowed {len(crawler_client.skipped_disallowed)} URL(s).")

    for r in results:
        if r["status"] != "ok":
            reasons = "; ".join(a.get("outcome", "?") for a in r["attempts"]) or "no attempts made"
            print(f"  FAILED {r['shop']}: {reasons}")
            # The artifact holds the whole page, but artifacts are awkward to
            # get at from a sandbox with no egress to blob storage. Echoing
            # the snippet puts the one thing needed to diagnose a parse
            # failure -- what the shop actually served -- into the job log.
            for attempt in r["attempts"]:
                snippet = attempt.get("body_snippet")
                if snippet:
                    print(f"    {attempt.get('platform', '?')} <- {snippet[:BODY_SNIPPET]}")

    print(f"\nRaw bodies and report.json written to {OUTPUT_DIR}/")

    report_unsaved(results, applied=args.apply)

    if args.apply:
        applied, skipped = apply_results(results)
        print()
        if applied:
            print(f"APPLIED to {len(applied)} shop(s): real fixture saved, platform "
                  f"corrected, verified:true -> {', '.join(applied)}")
        if skipped:
            print(f"Left unverified ({len(skipped)}): {', '.join(skipped)}")
        print("Run the tests before trusting this; the workflow does that for you.")


if __name__ == "__main__":
    main()
