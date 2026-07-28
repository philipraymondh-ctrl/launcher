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
import scraper

OUTPUT_DIR = Path(os.environ.get("PROBE_OUTPUT_DIR", Path(__file__).parent / "probe_output"))


# An exhausted-catalogue payload per platform, so a paginating fetcher
# terminates after the single page the probe actually fetched instead of
# replaying page 1 forever.
EMPTY_PAGE = {"shopify": '{"products": []}', "woocommerce": "[]", "html": ""}

# Enough of a failing body to tell a bot-block page from an API change.
BODY_SNIPPET = 400
# Under this many products an HTML page is a shop window, not a catalogue.
BETTER_CATALOGUE_AT = 12


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
    path = urlparse(url).path.strip("/") or "index"
    return re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")[:48]


def save_diagnostic_page(name, url, body):
    """Commit a readable copy of a page that responded but parsed to nothing.

    The full body goes to the run artifact, but artifacts live behind blob
    storage that the dev sandbox has no route to, so the only way this
    evidence reaches the person writing the parser is through git. Scripts
    and styles are stripped -- they are most of the bytes and none of the
    structure.
    """
    soup = BeautifulSoup(body, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    trimmed = soup.prettify()[:DIAGNOSTIC_CAP]
    DIAGNOSTIC_DIR.mkdir(parents=True, exist_ok=True)
    (DIAGNOSTIC_DIR / f"{name}.{_page_slug(url)}.html").write_text(
        f"<!-- {url}\n     {describe_unparsed(body)}\n"
        f"     Scripts/styles stripped, capped at {DIAGNOSTIC_CAP} bytes.\n"
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
    candidates = ([shop["catalog_path"]] if shop.get("catalog_path") else []) + autoselect.CATALOGUE_PATHS
    seen, paths = set(), []
    for path in candidates:
        url = urljoin(base + "/", path) if path else shop["url"]
        if url not in seen:
            seen.add(url)
            paths.append(("html", url, None, "html"))
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
    best_html = {"count": 0, "body": None, "url": None, "items": []}

    for platform, url, params, kind in candidate_endpoints(shop):
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

        if platform == "html" and len(items) < BETTER_CATALOGUE_AT:
            # A landing page's "featured wines" strip parses fine and is not
            # the catalogue. Note it and keep looking; fall back to it only
            # if nothing richer turns up.
            attempt["outcome"] = f"parsed {len(items)} product(s) -- looks like a shop window, not a catalogue"
            attempt["products"] = len(items)
            result["attempts"].append(attempt)
            if len(items) > best_html["count"]:
                best_html.update(count=len(items), body=body, url=url, items=items)
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
            thin=True,
        )
    return result


def _relative_path(shop, url):
    """The catalogue path to record, relative to the shop's base URL."""
    base = shop["url"].rstrip("/") + "/"
    return url[len(base):] if url.startswith(base) and url != base else None


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

    path = result.get("catalog_path")
    if platform == "html" and path:
        # Record where the catalogue actually was, so the hourly run fetches
        # one page instead of rediscovering it every time.
        if '"catalog_path"' in block:
            block = re.sub(r'("catalog_path": ")[^"]*(")',
                           lambda m: m.group(1) + path + m.group(2), block, count=1)
        else:
            block = re.sub(r'(\n[ \t]*"url": "[^"]*",\n)',
                           lambda m: m.group(1) + f'        "catalog_path": "{path}",\n',
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
            save_diagnostic_page("capture", url, resp.text)
            items = autoselect.find_products(
                resp.text, url, scraper.PRICE_PATTERN, scraper.parse_price)
            print(f"CAPTURE {url}: {len(resp.text)} bytes, "
                  f"autoselect reads {len(items)} product(s)")
            print(f"         {describe_unparsed(resp.text)}")
        return

    shops = [s for s in scraper.SHOPS if args.include_verified or not s.get("verified", True)]
    # The example-* entries point at reserved .example.com domains that
    # deliberately do not resolve; probing them just burns requests.
    shops = [s for s in shops if not s["name"].startswith("example-")]
    if args.only:
        wanted = {n.strip() for n in args.only.split(",")}
        shops = [s for s in shops if s["name"] in wanted]

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
