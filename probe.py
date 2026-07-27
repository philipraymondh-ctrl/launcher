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

It deliberately does NOT edit SHOPS or flip `verified`. Per
decisions/standing-decisions.md, `verified: true` is only written once a
real response has been received and parsed -- this run produces the
evidence for that, and the fixture/flag change is made from the evidence
afterwards, reviewably.

All requests go through crawler.Crawler, so robots.txt, the per-host rate
limit, backoff, the circuit breaker and the run budget all apply exactly
as they do in a real scrape.
"""
import argparse
import json
import os
from pathlib import Path

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


def main():
    parser = argparse.ArgumentParser(description="Probe shops' real endpoints.")
    parser.add_argument("--only", help="Comma-separated shop names to probe (default: all unverified)")
    parser.add_argument("--include-verified", action="store_true", help="Also probe already-verified shops")
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


if __name__ == "__main__":
    main()
