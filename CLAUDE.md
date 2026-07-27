# Wine producer scraper

## Autonomy

Agents may not ask the user anything. If an agent would stop to ask, it
invokes the `decision-proxy` agent instead and acts on its verdict (see
`.claude/agents/decision-proxy.md` and `decisions/standing-decisions.md`).
On ESCALATE, the caller continues with all remaining unblocked work — an
escalation is logged to `decisions/open-questions.md` for the human, it is
never a reason to idle waiting for one.

## Architecture

No framework, no database, no queue — five plain files, each owning one
concern, wired together by `scraper.py`:

1. **`crawler.py`** — the only module that calls `requests`. Every fetch
   goes through `Crawler.get()`: robots.txt (cached per host, `Crawl-delay`
   honoured), a minimum 3s + 0-2s jitter per-host rate limit, concurrency 1
   per host, exponential backoff on 429/503 (or `Retry-After` if present),
   a circuit breaker (3 consecutive failed requests skips that host for
   the rest of the run), conditional requests (ETag/Last-Modified, a 304
   reuses the cached body), a 6h disk cache (bypass with `FRESH=1`), and a
   hard `MAX_REQUESTS_PER_RUN` budget (default 120) that stops the run
   cleanly and logs which shops weren't reached.
2. **`scraper.py`** — loops over `SHOPS`, dispatching each to a platform
   fetcher (`fetch_shopify`, `fetch_woocommerce`, `fetch_html`, all taking
   a `Crawler` instance) that returns `{text, title, price, url,
   variant_title}` items, then runs `match_producers(text)` (accent/case-
   insensitive alias matching against `PRODUCERS`) to produce raw hits.
3. **`prices.yaml`** / **`pricebook.py`** — the human-edited reference
   price book (per-producer `reference_750_eur`, optional per-cuvee
   overrides, a `verified`/`last_verified` pair per producer) plus a CLI
   (`python pricebook.py --stale`) listing entries that are unverified or
   older than 180 days. Nothing in this codebase fetches Wine-Searcher —
   that's blocked and against their terms; numbers come from manual entry
   only.
4. **`evaluate.py`** — turns a raw hit into a priced one: parses bottle
   size from the title/variant (including the Jura 620ml clavelin, and
   defaulting to 750ml at low confidence if nothing matches), flags
   coffrets/cases as bundles whose per-bottle price is unknowable, detects
   Burgundy cru tier from the cuvee text for `region: burgundy` producers, computes `expected = reference × tier
   multiplier × format multiplier`, and classifies `DEAL`/`FAIR`/`HIGH`/
   `NOREF`. Never drops a hit — an unverified reference or low size/tier
   confidence sets `caveat: true`, it doesn't suppress anything.
5. **`notify.py`** — one digest email per run, never one per hit. State in
   `seen.json` (keyed by `sha256(shop + product_url + variant)`) drives a
   30-day per-item cooldown; a hit alerts only if it's new, its price
   dropped >10% since the last alert, or its classification improved to
   `DEAL`. A run where nothing qualifies sends nothing and exits 0 — that's
   a valid, successful run, not a failure. The full evaluated hit set
   always goes to `hits.json` (uploaded as the workflow artifact) even
   when the email itself is empty or capped at 40 rows.

Two more files exist for operating it rather than scraping:
**`probe.py`** detects each shop's real platform from a runner (the dev
sandbox has no egress); with `--apply` it also saves the real response as
that shop's fixture, corrects the platform and sets `verified: true` --
allowed only because the fetch, parse and flag happen in one run against a
real response. **`dashboard.py`** generates `wine.html`, a
static status page and control panel. `wine.html` is generated, never
hand-edited -- change `dashboard.py` instead.

Runs hourly from `.github/workflows/scraper.yml`, which runs the fixture
tests first, best-effort persists `seen.json`/`.cache` across runs via
`actions/cache`, and uploads `hits.json` as an artifact.

Every `SHOPS` entry also carries a `verified` flag. `main()` skips any shop
with `"verified": False` before it ever makes a network call — this is for
shops added from research/guesswork rather than a real observed response
(platform assumed, selectors invented). Flip it to `True` only after
`shop-adapter` has fetched a real response and replaced the placeholder
fixture with it. As of this writing every shop in `SHOPS` is unverified;
none of them run live yet.

Secrets (`GMAIL_SENDER`, `GMAIL_APP_PASSWORD`, `NOTIFY_EMAIL`) plus one
non-secret repo variable (`CONTACT_EMAIL`, used honestly in the crawler's
User-Agent) are the only external configuration; everything else is in
this repo.

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
- No module but `crawler.py` may call `requests` directly. If you're adding
  a new fetcher, it takes a `Crawler` instance and calls `.get()`.
- `evaluate.py` never suppresses a hit for missing/unverified reference
  data or low confidence — it classifies and flags a caveat instead. Don't
  add filtering there; that's exactly the behavior this scraper exists to
  avoid.
- Reference prices come from manual entry in `prices.yaml` only. Never add
  code that fetches Wine-Searcher.
- `notify.py` sends at most one digest email per run. Don't reintroduce a
  per-hit email path.
- A coffret/caisse is several bottles, so its price is not comparable to a
  per-bottle reference. `evaluate.py` must keep detecting bundles, applying
  no format multiplier, and always caveating them -- real listings like
  "COFFRET ANNIVERSAIRE GANEVAT" at EUR 450 would otherwise be scored
  against a ~EUR 70 bottle reference and shouted about.
- Catalogues are paged. Any new fetcher must walk pages, not just read the
  first one -- seeing only page one turns a real hit into a silent miss,
  which is the exact failure this project exists to avoid.
- `wine.html` is generated by `dashboard.py`. Never edit it directly.
- The repo is public, so issue forms are untrusted input. `apply_issue.py`
  must keep rejecting quote/backslash injection, non-https URLs and unsafe
  shop names, and `apply-config.yml` must keep its `author_association ==
  'OWNER'` gate. Never put a token in `wine.html` -- it is world-readable.
- `apply-config.yml` commits to `main` without a PR, so its test step is
  the only thing standing between a bad edit and the default branch. Never
  reorder the commit ahead of the tests.
- Don't write tests that pin today's config state as an invariant ("every
  producer is unverified", "producer X does not exist"). The issue forms
  and `probe.py --apply` change that state, and such a test turns the next
  legitimate edit into a CI failure. Assert the rule, not the current data.
- No test may touch the network. `scraper.main()` fetches for real once any
  shop is verified, so tests that call it must stub `crawler.Crawler` --
  otherwise the suite hits live shops on every CI run.

## Subagents

- **shop-adapter** — add or repair per-shop scraping logic. Detects
  platform (Shopify `/products.json` → WooCommerce Store API → HTML
  fallback), requires a fixture per shop, keeps alias-aware producer
  matching, one shop per commit. Fetches through `Crawler`, never
  `requests` directly.
- **scrape-doctor** — diagnoses a zero-hit or wrong-hit run. Distinguishes
  unreachable vs. empty/JS-rendered vs. genuinely absent vs. parsed wrong,
  reproduces against the fixture before live network, and treats zero hits
  as a potentially correct result rather than something to "fix".
- **actions-ops** — runs/schedules/observes the GitHub Actions workflow.
  Owns the `DRY_RUN` path, never echoes secret values, and validates YAML
  after any workflow edit.
