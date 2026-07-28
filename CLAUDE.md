# Wine producer scraper

## Autonomy

Agents may not ask the user anything. If an agent would stop to ask, it
invokes the `decision-proxy` agent instead and acts on its verdict (see
`.claude/agents/decision-proxy.md` and `decisions/standing-decisions.md`).
On ESCALATE, the caller continues with all remaining unblocked work — an
escalation is logged to `decisions/open-questions.md` for the human, it is
never a reason to idle waiting for one.

## Architecture

No framework, no database, no queue — seven plain files, each owning one
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
3. **`autoselect.py`** — reads a catalogue nobody wrote selectors for.
   Most HTML shops match none of the generic `div.product` guesses because
   their markup is their own (leszinzinsduvin is hand-rolled PHP serving
   `/vin-<id>-<region>_..._<producer>.html`), and nine bespoke selector
   sets is nine things to maintain that each break silently on a restyle.
   Instead the structure is derived: find elements whose own text holds a
   currency-adjacent price, climb to the widest ancestor still describing
   one product *and* holding a product link, group those by shared parent,
   and take the busiest group. `fetch_html` uses configured selectors when
   they match and falls back to this when they don't, walking pages via
   `find_next_page` (`rel="next"`, then "next"/"suivant" links). Returns
   `[]` rather than guessing when a page has no repeated priced structure.
   An optional `catalog_path` on a SHOPS entry points at the catalogue when
   the landing page isn't it.
4. **`market.py`** — where reference prices come from. One typed number
   per producer cannot describe a producer selling a negoce cuvee at EUR 30
   and a domaine vin jaune at EUR 250, and the typing grows with producers
   x cuvees x vintages. So the reference is *observed*: every priced
   listing is recorded to `observations.json`, and a hit is scored against
   what other shops charge. A ladder from strongest evidence down --
   same cuvee + same vintage elsewhere, same cuvee other vintages, then
   the producer's own line -- with the confidence dropping at each rung and
   `None` at the bottom rather than a guess. Three things separate
   themselves with no per-producer config: negoce vs domaine (the label
   carries extra names, so `segment()` splits them), vintage (part of the
   key), and cru (already in the cuvee text). Magnums and clavelins are
   normalised to a per-750 equivalent; coffrets are only ever compared to
   other coffrets. Nothing here contacts a price aggregator -- the data is
   the crawl we already do.
5. **`prices.yaml`** / **`pricebook.py`** — the optional manual override (per-producer `reference_750_eur`, optional per-cuvee
   overrides, a `verified`/`last_verified` pair per producer) plus a CLI
   (`python pricebook.py --stale`) listing entries that are unverified or
   older than 180 days. No longer the primary source: a `verified: true`
   figure still wins over observed data, but leaving it blank is now the
   normal case. Nothing in this codebase fetches Wine-Searcher — that's
   blocked and against their terms.
6. **`evaluate.py`** — turns a raw hit into a priced one: parses bottle
   size from the title/variant (including the Jura 620ml clavelin, and
   defaulting to 750ml at low confidence if nothing matches), flags
   coffrets/cases as bundles whose per-bottle price is unknowable, detects
   Burgundy cru tier from the cuvee text for `region: burgundy` producers, computes `expected = reference × tier
   multiplier × format multiplier`, and classifies `DEAL`/`FAIR`/`HIGH`/
   `NOREF`. Never drops a hit — an unverified reference or low size/tier
   confidence sets `caveat: true`, it doesn't suppress anything.
7. **`notify.py`** — one digest email per run, never one per hit. A row
   names the alias that matched it (`Ganevat [ganevat]`), because a
   misattributed producer is otherwise indistinguishable from a correct
   one -- three estates were reported wrongly before this existed. Any
   digest that is sent also carries the run's `notes`: shops that returned
   nothing, and watched producers found nowhere. State in
   `seen.json` (keyed by `sha256(shop + product_url + variant)`) drives a
   30-day per-item cooldown. A hit alerts if it's new, or its price dropped
   >10% since the last alert — a drop is news, so it ignores the cooldown —
   or its classification improved to `DEAL`, which does wait for the
   cooldown because classification is derived from the market pool and can
   flap hourly. A run where nothing qualifies sends nothing and exits 0 —
   that's a valid, successful run. But a whole *week* of them looks exactly
   like broken credentials from the inbox, so after `RECAP_DAYS` with no
   email at all the run sends one recap of everything currently matched
   (`_meta.last_recap_at` in `seen.json`, reset by any email). The full
   evaluated hit set always goes to `hits.json` (uploaded as the workflow
   artifact) even when the email itself is empty or capped at 40 rows.

Two more files exist for operating it rather than scraping:
**`probe.py`** detects each shop's real platform from a runner (the dev
sandbox has no egress); with `--apply` it also saves the real response as
that shop's fixture, corrects the platform and sets `verified: true` --
allowed only because the fetch, parse and flag happen in one run against a
real response. **`dashboard.py`** generates `wine.html`, a status page
that also operates the scraper: its buttons call the GitHub REST API
directly from the browser (`POST .../workflows/{file}/dispatches` to run
the scraper or probe, polling `.../actions/runs` to show progress, `POST
.../issues` to apply a config change), so nothing on it is a link out to
the repo. The token that authorises this lives in the browser's
`localStorage`, entered once per device -- never in the file.
`wine.html` is generated, never hand-edited -- change `dashboard.py`
instead.

Runs hourly from `.github/workflows/scraper.yml`, which runs the fixture
tests first, best-effort persists `seen.json`/`observations.json`/`.cache`
across runs via `actions/cache`, and uploads `hits.json` plus
`observations.json` as artifacts.

Every `SHOPS` entry also carries a `verified` flag. `main()` skips any shop
with `"verified": False` before it ever makes a network call — this is for
shops added from research/guesswork rather than a real observed response
(platform assumed, selectors invented). It is set only by `probe.py
--apply`, which fetches, parses and flags in a single run against a real
response; never by hand.

20 shops are currently verified and fetched on every run. The six that are
not each have a stated reason, none of which is "needs selectors" any more
-- `autoselect` and the producer-index route removed that category:
  - naturavin blocks us with 403;
  - vinscheznous no longer resolves;
  - purewijnen, purovino, vinnaturelbe and vinovivo all serve a listing
    with product links but no prices in the HTML (vinovivo's index has 330
    product links and 6 prices), so the price is rendered client-side or
    lives only on the product page. Reaching them means either running JS
    or fetching every product page, and the second is hundreds of requests
    for one shop. `probe_pages/` holds a trimmed copy of each page.

Secrets (`GMAIL_SENDER`, `GMAIL_APP_PASSWORD`, `NOTIFY_EMAIL`) are the
only external configuration; everything else is in this repo. Those three
reach Gmail's SMTP server and nothing else -- they are never put in an
HTTP header or printed.

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
- The crawler's User-Agent identifies nobody. It is sent to every shop on
  every request and printed into Actions logs, which are world-readable on
  a public repo, so it carries no email, no personal name, no account name
  and no URL implying any of them -- the repo URL was rejected for exactly
  that reason. It also avoids the word "scraper", which some shops block on
  sight however politely the thing behaves. `Crawler.__init__` raises on a
  contact containing "@"; tests assert the default agent is `BOT_NAME`
  alone and that no workflow injects a contact. `CONTACT_URL` exists to opt
  back in to a contact that gives nothing away.
- Reference prices are observed from our own crawl (`market.py`), never
  fetched from a price aggregator. Wine-Searcher is blocked and against
  their terms -- never add code that fetches it. A hand-entered
  `reference_750_eur` is an *override*, only trusted ahead of observed data
  when `verified: true`; an unverified one ranks below observed data on
  purpose, because a guessed number produces a confident wrong verdict
  where no number produces an honest `NOREF`.
- A new HTML shop does not get hand-written selectors by default. Let
  `autoselect` try first; only write per-shop selectors when the probe
  shows it genuinely cannot read that page. Selectors are a maintenance
  cost paid per shop, per restyle.
- `market.py` must keep cuvee, vintage and format in the comparison key.
  Collapsing any of them re-creates the bug the module exists to fix: a
  EUR 30 negoce bottle scored against a EUR 70 domaine average reads as a
  permanent DEAL, and a EUR 450 coffret as a permanent HIGH.
- A wine seen at no other shop gets no reference. Do not add a fallback
  that invents one -- `NOREF` is a real answer and the hit is still
  reported.
- Producer aliases must name an estate, not a surname. `match_producers`
  prefers the longest matching alias, but that only separates producers we
  track -- it cannot help against an untracked namesake. Jura and Savoie
  are full of them: "overnoy" alone also matches Domaine Overnoy,
  Overnoy-Crinquand and Overnoy Jean-Louis et Guillaume; "houillon" also
  matches Corentin Houillon and Fimbel-Houillon; "brochet" also matches
  Emmanuel Brochet. Every one of those was reported under the wrong
  producer from real catalogues. Use the full name.
- A listing that is sold out is not a find. Stock comes from the platform
  when it answers (`available` on a Shopify variant, `is_in_stock` on the
  WooCommerce Store API) and from the listing text otherwise ("epuise",
  "rupture de stock"). `in_stock` is set by the fetchers and dropped by
  `check_shop`, never by the parser -- the probe counts parsed products to
  decide whether an adapter works, so a shop whose stock is out today must
  not read as broken. Silence from an API is not "sold out".
- `notify.py` sends at most one email per run -- a digest or a recap, never
  both, and never one per hit. Don't reintroduce a per-hit email path.
- `notify.py` persists `seen.json` only *after* the email is actually sent.
  Marking an item alerted is what silences it for 30 days, so saving before
  the send means a dry run or a failed send silently swallows a real find.
  Never move `save_state` back above `send_email`. A `DRY_RUN` writes no
  state on any path, including the silent one.
- The weekly recap must never mark an item alerted. It is not a find, and
  marking one would silence a real drop for 30 days; it refreshes
  `last_price` only, exactly as a silent run does. It is also not a
  heartbeat -- a run with no hits at all still sends nothing.
- A coffret/caisse is several bottles, so its price is not comparable to a
  per-bottle reference. `evaluate.py` must keep detecting bundles, applying
  no format multiplier, and always caveating them -- real listings like
  "COFFRET ANNIVERSAIRE GANEVAT" at EUR 450 would otherwise be scored
  against a ~EUR 70 bottle reference and shouted about.
- Silent failure is the enemy, so `main()` states three things it used to
  keep to itself. `check_shop` returns a `ShopResult` (a `list` subclass,
  so its fifteen callers are unaffected) carrying `products_parsed`: a
  verified shop's fixture always parses to more than zero, so zero from a
  live fetch is adapter drift and gets a `DRIFT` line plus a digest note.
  It is reported, never escalated to a failed run -- a shop can be empty
  for a night, and an hourly red run teaches you to ignore red runs.
  Producers matched at no shop are named too: an alias typo makes a
  producer vanish from every shop at once, which is otherwise invisible.
- `shop_order()` rotates SHOPS by the hour. The budget is global and was
  spent in list order, so the moment it binds it is always the same tail
  that goes unfetched -- systematic, not random. Both "not reached this
  run" messages must index the rotated order, not `SHOPS`.
- Catalogues are paged. Any new fetcher must walk pages, not just read the
  first one -- seeing only page one turns a real hit into a silent miss,
  which is the exact failure this project exists to avoid.
- `wine.html` is generated by `dashboard.py`. Never edit it directly, and
  never commit a rebuilt copy on a feature branch: main rebuilds it on the
  push after a merge, so a branch-side copy only produces a merge conflict
  on a file whose correct resolution is always "regenerate it". Two merges
  stopped on exactly that. No test reads the file from disk -- they render
  through `dashboard.render()` -- so a branch never needs it current.
- The repo is public, so issue forms are untrusted input. `apply_issue.py`
  must keep rejecting quote/backslash injection, non-https URLs and unsafe
  shop names, and `apply-config.yml` must keep its `author_association ==
  'OWNER'` gate. Never put a token in `wine.html` -- it is world-readable.
  The page's own credential is read from `localStorage` at runtime and is
  never written into the generated file; `tests/test_dashboard.py` asserts
  that, and that the script only ever sends it to `api.github.com`.
- `wine.html` builds issue-form bodies in JavaScript that `apply_issue.py`
  parses in Python. Changing a form heading, its field order, or a checkbox
  label breaks that seam silently -- change `.github/ISSUE_TEMPLATE/`,
  `apply_issue.py` and `dashboard.JS` together. The node-backed tests in
  `tests/test_dashboard.py` run the real JS through the real parser; keep
  them working rather than reimplementing the format in the test.
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
