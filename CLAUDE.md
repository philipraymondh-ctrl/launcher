# Wine producer scraper

## Architecture

Single file, `scraper.py`, no framework/database/queue. It:

1. Loops over `SHOPS`, dispatching each to a platform fetcher
   (`fetch_shopify`, `fetch_woocommerce`, `fetch_html`) that returns a flat
   list of `{text, title, price, url}` items.
2. Runs `match_producers(text)` against each item, checking accent- and
   case-insensitive alias substrings from `PRODUCERS`.
3. Collects hits across all shops and, if any exist, emails a summary via
   Gmail SMTP (`send_email`) — or, with `DRY_RUN=1`, prints the email body
   instead of sending it.
4. Runs hourly from `.github/workflows/scraper.yml`, which also runs the
   fixture tests before the scrape step.

Every `SHOPS` entry carries a `verified` flag. `main()` skips any shop with
`"verified": False` before it ever makes a network call — this is for
shops added from research/guesswork rather than a real observed response
(platform assumed, selectors invented). Flip it to `True` only after
`shop-adapter` has fetched a real response and replaced the placeholder
fixture with it. As of this writing every shop in `SHOPS` is unverified;
none of them run live yet.

Three secrets (`GMAIL_SENDER`, `GMAIL_APP_PASSWORD`, `NOTIFY_EMAIL`) are the
only external configuration; everything else is in this file.

## Rules

- Do not invoke a subagent for a one-line edit. Just make it.
- Adding a producer name to `PRODUCERS` is a config change, not an agent
  task — edit the dict directly.
- Every scraping change (new shop, adapter fix, selector change) must pass
  the fixture tests in `tests/` before commit.
- Prices: parse currency-adjacent numbers only (a `€`, `$`, `£`, `EUR`, or
  `USD` marker touching the number). Never treat a bare 4-digit number as a
  price — it could be a vintage year. This is what `PRICE_PATTERN` /
  `parse_price` in `scraper.py` enforce; don't loosen it.
- Keep it one file. No framework, no database, no queue.

## Subagents

- **shop-adapter** — add or repair per-shop scraping logic. Detects
  platform (Shopify `/products.json` → WooCommerce Store API → HTML
  fallback), requires a fixture per shop, keeps alias-aware producer
  matching, one shop per commit.
- **scrape-doctor** — diagnoses a zero-hit or wrong-hit run. Distinguishes
  unreachable vs. empty/JS-rendered vs. genuinely absent vs. parsed wrong,
  reproduces against the fixture before live network, and treats zero hits
  as a potentially correct result rather than something to "fix".
- **actions-ops** — runs/schedules/observes the GitHub Actions workflow.
  Owns the `DRY_RUN` path, never echoes secret values, and validates YAML
  after any workflow edit.
