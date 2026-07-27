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

import apply_issue
import crawler
import scraper

OUTPUT_DIR = Path(os.environ.get("PROBE_OUTPUT_DIR", Path(__file__).parent / "probe_output"))


# An exhausted-catalogue payload per platform, so a paginating fetcher
# terminates after the single page the probe actually fetched instead of
# replaying page 1 forever.
EMPTY_PAGE = {"shopify": '{"products": []}', "woocommerce": "[]", "html": ""}


class CannedCrawler:
    """Replays one already-fetched response into a real fetcher, so the
    probe validates the actual parse path without a second network call.

    The fetchers paginate, so only the first call gets the real body; every
    later page reads as empty and ends the walk. The probe therefore
    measures page one, which is all it fetched -- see `truncated` in the
    report for whether more pages exist.
    """

    def __init__(self, response, platform):
        self._response = response
        self._empty = crawler.FetchResult(200, EMPTY_PAGE.get(platform, ""))
        self._served = False
        self.request_count = 0
        self.max_requests = 1

    def get(self, url, params=None):
        if self._served:
            return self._empty
        self._served = True
        return self._response


def candidate_endpoints(shop):
    base = shop["url"].rstrip("/")
    return [
        ("shopify", f"{base}/products.json", {"limit": 250}, "json"),
        ("woocommerce", f"{base}/wp-json/wc/store/v1/products", {"per_page": 100}, "json"),
        ("html", shop["url"], None, "html"),
    ]


def try_parse(platform, shop, response):
    """Run the real fetcher for `platform` against an already-fetched
    response. Returns (items, error_string)."""
    probe_shop = dict(shop)
    probe_shop["platform"] = platform
    if platform == "html":
        probe_shop.setdefault("item_selector", "div.product")
        probe_shop.setdefault("title_selector", "h2.product-title")
        probe_shop.setdefault("price_selector", "span.price")
    try:
        items = scraper.FETCHERS[platform](probe_shop, CannedCrawler(response, platform))
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
    }

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

        items, parse_error = try_parse(platform, shop, response)
        if parse_error:
            attempt["outcome"] = f"responded but parse failed: {parse_error}"
            result["attempts"].append(attempt)
            continue
        if not items:
            attempt["outcome"] = "responded and parsed, but zero products found"
            result["attempts"].append(attempt)
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
        )
        return result

    return result


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
        "--apply", action="store_true",
        help="Save real fixtures, correct platforms and set verified:true for shops probed OK",
    )
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

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
