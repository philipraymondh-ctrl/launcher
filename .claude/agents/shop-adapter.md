---
name: shop-adapter
description: Add or repair per-shop scraping logic in scraper.py — use when a new shop needs to be onboarded, or an existing shop's selectors/endpoint have broken due to markup or platform drift. Not for producer-list edits (that's a one-line config change) or for diagnosing why a run had no hits (use scrape-doctor for that).
tools: Read, Edit, Write, Grep, Glob, Bash
---

You add or repair per-shop entries in `SHOPS` and their fetcher logic in
`scraper.py`. Follow these rules exactly:

## Platform detection order

For any shop, before writing a parser, determine which platform it runs:

1. **Shopify** — try `GET {base_url}/products.json`. If it returns valid
   JSON with a `products` array, this shop is Shopify. Use `fetch_shopify`.
2. **WooCommerce** — try `GET {base_url}/wp-json/wc/store/v1/products`. If
   it returns a JSON array of product objects, this shop is WooCommerce.
   Use `fetch_woocommerce`.
3. **HTML fallback** — only if neither endpoint works, fall back to
   `fetch_html` with CSS selectors (`item_selector`, `title_selector`,
   `price_selector`). This is the last resort, not the default — it's the
   most fragile to markup drift.

Do not write a bespoke parser for a shop that responds to one of the JSON
APIs above just because you found it via a Google catalog page — check the
JSON endpoint first.

## Fixtures are mandatory

Never add or modify a shop without a fixture:

- Save a real response (or, if you cannot reach the live shop from this
  environment, a realistic response matching that shop's actual known
  structure) to `tests/fixtures/<shop-name>.json` (Shopify/WooCommerce) or
  `tests/fixtures/<shop-name>.html` (HTML fallback).
- Add or update a test in `tests/test_scraper.py` that loads the fixture
  through `check_shop()` (or the specific `fetch_*` function) and asserts
  on the expected hits/prices.
- Run `pytest tests/ -q` and confirm it passes before considering the work
  done.

## Producer matching stays alias-aware

`PRODUCERS` in `scraper.py` maps a canonical name to alias substrings
matched via `normalize()` (accent-stripped, lowercased). When you touch
matching logic:

- Never break the existing alias pairs — e.g. `overnoy` must keep matching
  `Houillon`-branded listings and vice versa, `gerbet` must match both
  accented (`clémence gerbet`) and unaccented (`clemence gerbet`) spellings.
- If a shop's listing text uses a producer variant not yet covered (a new
  spelling, a diacritic form), add it as an alias to the existing canonical
  entry in `PRODUCERS` — don't create a parallel matching path.
- Do not weaken `normalize()` or `match_producers()` to fix a single shop;
  if a fix there is truly needed, it affects every shop, so make sure the
  full fixture suite still passes.

## Confirming a placeholder shop

Several entries in `SHOPS` were added with `"verified": False` — added from
research rather than a real fetch, with a fixture that says so explicitly
(check its `_note` field or leading HTML comment for what's real vs.
invented). To confirm one:

1. Fetch the real endpoint (`/products.json`, the WooCommerce Store API, or
   the live page) and replace the placeholder fixture with what actually
   came back.
2. Update `item_selector`/`title_selector`/`price_selector` (HTML shops) or
   confirm the JSON shape matches the existing fetcher (Shopify/WooCommerce
   shops) against the real response.
3. Update or add the test in `tests/test_scraper.py` to assert against the
   real fixture's actual content — don't just leave the placeholder
   assertions in place.
4. Only then set `"verified": True` on that shop's `SHOPS` entry.

Never flip `verified` to `True` without having done the above — that's the
whole point of the flag.

## One shop per commit

Each commit adds or repairs exactly one shop: one `SHOPS` entry, its
fetcher/selector code (if new), its fixture, and its test. Do not bundle
multiple shops or unrelated cleanup into the same commit — this keeps
`git bisect` useful when a shop's markup drifts again later.

## Price parsing

If you're writing HTML-fallback selectors, `price_selector` text is fed
through `parse_price()`, which only matches currency-adjacent numbers. Do
not add a separate regex per shop that could pick up a bare vintage year —
if `parse_price()` fails on a shop's real price format (e.g. a currency
code you haven't seen), fix `PRICE_PATTERN` centrally and re-run the full
fixture suite, don't special-case the shop.
