# Wine producer scraper

## Autonomy

Agents may not ask the user anything. If an agent would stop to ask, it
invokes the `decision-proxy` agent instead and acts on its verdict (see
`.claude/agents/decision-proxy.md` and `decisions/standing-decisions.md`).
On ESCALATE, the caller continues with all remaining unblocked work — an
escalation is logged to `decisions/open-questions.md` for the human, it is
never a reason to idle waiting for one.

## Architecture

No framework, no database, no queue — eight plain files, each owning one
concern, wired together by `scraper.py`:

1. **`crawler.py`** — the only module that calls `requests`. Every fetch
   goes through `Crawler.get()`: robots.txt (cached per host, `Crawl-delay`
   honoured), a minimum 3s + 0-2s jitter per-host rate limit, concurrency 1
   per host, exponential backoff on 429/503 (or `Retry-After` if present),
   a circuit breaker (3 consecutive failed requests skips that host for
   the rest of the run), conditional requests (ETag/Last-Modified, a 304
   reuses the cached body), a 6h disk cache (bypass with `FRESH=1`), and a
   hard `MAX_REQUESTS_PER_RUN` budget (default 160, sized to fit
   `MAX_RUN_SECONDS` at a pessimistic 5.5s per request) that stops the run
   cleanly and logs which shops weren't reached. It also refuses to believe
   a 200 that is a bot challenge: `looks_like_challenge` reads a meta-refresh
   to a captcha path, or one of a handful of interstitial phrases on a page
   that links nowhere, and raises `Challenged` **before** the cache write,
   without retrying.
2. **`scraper.py`** — loops over `SHOPS`, dispatching each to a platform
   fetcher (`fetch_shopify`, `fetch_woocommerce`, `fetch_html`, all taking
   a `Crawler` instance) that returns `{text, title, price, url,
   variant_title}` items, then runs `match_producers(text)` (alias matching
   against `PRODUCERS` through `textnorm.match_key`, so accents, case and
   separators are all out of the comparison) to produce raw hits. A match on
   a listing that is out of stock is not dropped but set aside on
   `ShopResult.sold_out`, which is what lets the run distinguish a broken
   alias from a producer that is simply sold out everywhere.
3. **`autoselect.py`** — reads a catalogue nobody wrote selectors for.
   Most HTML shops match none of the generic `div.product` guesses because
   their markup is their own (leszinzinsduvin is hand-rolled PHP serving
   `/vin-<id>-<region>_..._<producer>.html`), and nine bespoke selector
   sets is nine things to maintain that each break silently on a restyle.
   Instead the structure is derived: find the innermost elements whose text
   holds a currency-adjacent price and no link (a card is not a price cell),
   climb to the widest ancestor still describing one product *and* holding a
   product link, group those by shared parent, and take the busiest group.
   Innermost-and-full-text, not own-text: WooCommerce and Squarespace both
   render `<span>12.50<span>€</span></span>`, so an own-text rule sees no
   price at all and the whole grid is invisible -- which is exactly what hid
   vinovivo's 315 wines and purovino's 122.
   `fetch_html` uses configured selectors when they match and falls back to
   this when they don't, walking pages via `find_next_page` (`rel="next"`,
   then "next"/"suivant" labels, then a `next`/`suivant` class token for the
   pager arrow that carries no text at all). Returns `[]` rather than
   guessing when a page has no repeated priced structure.
   An optional `catalog_path` on a SHOPS entry points at the catalogue when
   the landing page isn't it, and `catalog_paths` (a list) exists because
   winenot.fr and vinnouveau.fr keep their wines under region categories
   (`/12-alsace`, `/19-jura`, `/21-loire`) with no "all wines" page at all --
   one path there reads one region, which is how winenot came to be
   configured to read its sparkling-wine filter and nothing else. Every entry
   is walked, products are deduplicated by URL across them, and
   `MAX_PAGES_PER_SHOP` bounds the shop rather than each category.
   `find_catalogue_links` derives all of this from the shop's own navigation,
   because no list of guessed paths knows what a shop calls its catalogue.
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
   (`_meta.last_recap_at` in `seen.json`, reset by any email). A run a
   *human* started reports unconditionally -- the workflow sets
   `FORCE_REPORT=1` on `workflow_dispatch` only -- because silence from a
   run you pressed a button for reads as a dead scraper, and twice did. The full
   evaluated hit set always goes to `hits.json` (uploaded as the workflow
   artifact) even when the email itself is empty or capped at 40 rows.

8. **`textnorm.py`** — how text is compared, in one place, as two
   functions that must not be confused. `strip_accents` (NFKD, drop
   combining marks, lowercase) is what `scraper`, `market`, `evaluate`,
   `apply_issue` and `autoselect` each used to carry as a private copy —
   five identical copies waiting for one divergent edit, which would have
   made a producer added through the issue form derive an alias matching
   nothing. `match_key` adds separator folding (`&` → `et`, every other
   non-alphanumeric → space) and exists only for deciding whether a *name*
   matches, so one alias `bruyere houillon` covers "Bruyère-Houillon" and
   "Renaud Bruyère–Houillon" with no per-producer variant. It must never
   touch text a price or vintage regex will read afterwards: it removes the
   currency markers `PRICE_PATTERN` and `market.VINTAGE_RE` use to tell a
   price from a year.

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

Any digest or recap also carries what the run could not do quietly:
shops that returned nothing, producers **matched but sold out everywhere**
(with the shops that had them, so there is somewhere to look again),
producers **found nowhere at all** — an alias signal, now that sold-out
matches no longer land here — and **alias near-misses**, words on a shop's
pages one edit away from an alias that matched nothing.

Every `SHOPS` entry also carries a `verified` flag. `main()` skips any shop
with `"verified": False` before it ever makes a network call — this is for
shops added from research/guesswork rather than a real observed response
(platform assumed, selectors invented). It is set only by `probe.py
--apply`, which fetches, parses and flags in a single run against a real
response; never by hand.

22 shops are currently verified and fetched on every run. Two of them,
vinovivo and purovino, spent months listed here as "prices are rendered
client-side" — and that verdict was wrong twice over. The price *was* in
their HTML; `autoselect._price_nodes` could not see it, because it accepted a
price only when one element's own text held both the number and the currency
marker, and both shops (like most WooCommerce and Squarespace themes) wrap
the marker in its own tag. The evidence for the old verdict was also drawn
from the wrong page: "vinovivo's index has 330 product links and 6 prices" is
true of its *home page*, while `/shop` carries ten priced cards and says
`1–10 de 315 resultats`. Read a shop's catalogue before concluding anything
about a shop.

The four that remain unverified each have a tested reason:
  - **naturavin** blocks us with 403;
  - **vinscheznous** no longer resolves;
  - **purewijnen** is Drupal 7 (`li.leaf`, `/sites/purewijnen/files/`) with
    no commerce module, and there is no price anywhere on it to read.
    Captured and checked: `/nl/wijnen-bestellen` and `/nl/wijnkaart` (the two
    pages whose names promise a price list) hold **zero currency markers**,
    as does the grower bio at
    `probe_pages/capture.nl-renaud-bruyere-houillon.html` — 28KB, not one
    marker. Its producer index is real, and
    `find_producer_links` does find our Renaud Bruyère-Houillon in it
    (ignoring the Overnoy-Crinquand two lines up --
    `tests/fixtures/purewijnen-growers-excerpt.html` keeps that real markup
    as a namesake test), but the link leads to prose;
  - **vinnaturelbe** is PrestaShop 1.6 whose product miniatures carry a name,
    a stock line and a "Détails" button and no price at all. Its real
    catalogue is `/fr/categorie/11-acheter-en-ligne`, and
    `probe_pages/capture.vin-naturel-be.fr-categorie-11-acheter-en-ligne.html`
    has 40 product links and **zero currency markers** — catalogue mode, or a
    price wall for guests, not client-side rendering. Its `/fr/vignerons`
    index lists 41 growers and none of them is a producer we watch.

Two verified shops answer HTTP 200 with a bot challenge rather than their
catalogue, which is why `crawler.Challenged` exists: **vinnaturel.fr** serves
"One moment, please... Please wait while your request is being verified", and
**vinopura.nl** serves 221 bytes of meta-refresh to
`/.well-known/sgcaptcha/`. Both read as healthy for weeks — one as
"ok, 0 products", the other as a "parse error" — and both went into the 6h
cache, so each challenge stayed true for six more runs. They now get a
`blocked` coverage row and a line in the digest. Do not try to get past
either: a challenge is a shop saying no.

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
- A zero is not a price. Cart widgets ("Voir mon panier -- 0,00 EUR"), gift
  cards and "price on request" all carry a currency-adjacent zero, and zero
  is below every reference there will ever be, so such a row is a permanent
  DEAL -- one live dry run put exactly that in the digest. `positive_price`
  rejects it on all three platform paths, and `autoselect` does not treat a
  zero-priced block as a listing at all.
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
- The probe never accepts an HTML page on the spot. Every candidate is
  fetched and weighed, and the best `(paginates, count)` wins -- the landing
  page always going first, because that is where the shop's menu lives.
  Short-circuiting on "this looks like a catalogue" meant a recorded
  `catalog_path` (tried early by design) was taken before the menu had been
  read, so a wrong path confirmed itself on every re-probe: three runs left
  winenot on its sparkling-wine filter. A JSON platform still ends the
  search, because `/products.json` either is the catalogue or is not there. Product count alone chose
  pangee's "new arrivals" strip over its catalogue and winenot's
  sparkling-wine filter over nine region categories -- a strip is one page, a
  catalogue runs to twenty. When several categories each hold part of the
  catalogue and none holds all of it, they are recorded as `catalog_paths`.
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
- A sold-out match is remembered, never alerted. `check_shop` matches
  every parsed listing and puts out-of-stock matches on
  `ShopResult.sold_out`; they must stay out of `hits.json`, out of the
  market pool and above all out of `seen.json`. That last one is what makes
  a **restock** read as a new item and alert — the most valuable alert this
  scraper sends, and it works because nothing about a sold-out listing is
  persisted. `tests/test_coverage.py` asserts both halves; don't "improve"
  it by recording sold-out state.
- "Watched but found nowhere" means matched nowhere at all, in stock or
  not. A producer stocked somewhere but sold out belongs in "Matched but
  sold out everywhere" with its shops named; collapsing the two back
  together restores the exact ambiguity the note was added to remove (one
  run called 13 of 16 producers missing while a single shop hid 2135
  sold-out listings).
- Near-misses are suspicions, not findings: only for producers that matched
  nothing, only alias tokens of 7+ characters, only at edit distance 1, and
  only when *neither* side is a word the corpus shows at more than one shop.
  That last filter is doing the real work -- two live runs offered
  `'pierres'`, `'pierra'`, `'pierro'` and `'malice'` before it existed,
  because "pierre" and "calice" are French before they are estates.
  Loosening any of it turns the note into noise, and a noisy note is an
  ignored note.
- Producer aliases must name an estate, not a surname. `match_producers`
  prefers the longest matching alias, but that only separates producers we
  track -- it cannot help against an untracked namesake. Jura and Savoie
  are full of them: "overnoy" alone also matches Domaine Overnoy,
  Overnoy-Crinquand and Overnoy Jean-Louis et Guillaume; "houillon" also
  matches Corentin Houillon and Fimbel-Houillon; "brochet" also matches
  Emmanuel Brochet. Every one of those was reported under the wrong
  producer from real catalogues. Use the full name.
- Text comparison goes through `textnorm`, never a local copy. Use
  `strip_accents` for anything a price, vintage or cru regex reads
  afterwards, and `match_key` only where a *name* decides a match
  (`scraper.matched_aliases`, `apply_issue`'s derived alias — those two must
  agree or a producer added through the form matches nothing). Putting
  `match_key` in front of `market.VINTAGE_RE` turns "2018,50 €" into
  "2018 50" and a price becomes a vintage.
- A listing that is sold out is not a find. Stock comes from the platform
  when it answers (`available` on a Shopify variant, `is_in_stock` on the
  WooCommerce Store API) and from the listing text otherwise ("epuise",
  "rupture de stock"). `in_stock` is set by the fetchers and dropped by
  `check_shop`, never by the parser -- the probe counts parsed products to
  decide whether an adapter works, so a shop whose stock is out today must
  not read as broken. Silence from an API is not "sold out".
- `notify.py` sends at most one email per run -- a digest, a recap, or an
  on-demand report, never two of them, and never one per hit. Don't
  reintroduce a per-hit email path.
- A hand-started run always answers. `FORCE_REPORT` is set by the workflow
  for `workflow_dispatch` and nothing else: the hourly schedule stays quiet
  without news, but a dispatched run emails everything currently matched
  even when the list is empty -- that empty table is the only way to tell
  "nothing new" from "credentials expired" from a button. It marks nothing
  as alerted, exactly like the recap, and `DRY_RUN` still overrides it.
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
- Every run states its coverage: one row per live shop with products read,
  in stock, sold out, hits and the producers matched, in the log, in every
  email and in `coverage.json` (uploaded with the artifact). It exists to
  answer "does what we read match what the shop actually sells", which was
  previously only answerable by reading a log line by line. A shop that
  failed still gets a row -- a shop missing from the table entirely is how
  "we never looked" hides. `STATUS` is `TRUNCATED` when the walk stopped
  before the catalogue did (`ParsedItems.truncated`, set by `_paged` and
  `fetch_html`), and that is the one cell that decides whether the row can
  be compared with the shop's real selection at all.
- The run stops itself on wall clock as well as on requests. A cold-cache
  crawl of 22 polite shops took 8m38s against a 10-minute job timeout, and
  every shop added shrinks that margin; a job killed at the ceiling loses
  the whole crawl -- no `hits.json`, no email, a red run and no
  explanation. `MAX_RUN_SECONDS` (default 900) breaks the shop loop, names
  the shops not reached, and lets the run report what it has.
  `timeout-minutes` in the workflow is only the outer backstop and must stay
  well clear of it.
- `shop_order()` rotates SHOPS by *hours since the epoch*, not by the hour
  of the day. The budget is global and was spent in list order, so the moment
  it binds it is always the same tail that goes unfetched -- systematic, not
  random. `hour % len(shops)` looks like it fixes that and does not: with 29
  shops only 24 offsets ever occur, so five shops could never lead a run.
  All three "not reached this run" messages must index the rotated order, not
  `SHOPS` -- there are three, and the pre-fetch budget check is the one that
  gets forgotten.
- A shop the run never reached still gets a coverage row (`not reached`), and
  "watched but found nowhere" is only that when every shop was read.
  Otherwise a binding budget silently converts "we never looked" into an
  accusation against the aliases, which is the opposite of what that note is
  for.
- `catalogue_starts()` pins the measured-best catalogue first and rotates
  only the rest. One page budget is shared across every `catalog_paths`
  entry, so a fixed order spent all of it on the first category -- winenot
  has never read its rosé, sparkling, moelleux or muté at any budget.
  Rotating everything is not the fix either: the probe also records pages
  that merely parsed (a portfolio page, a sub-category), and giving those
  first place spends the budget on a slice of the shop instead of the whole
  of it.
- Catalogues are paged. Any new fetcher must walk pages, not just read the
  first one -- seeing only page one turns a real hit into a silent miss,
  which is the exact failure this project exists to avoid.
- `wine.html` is generated by `dashboard.py`. Never edit it directly, and
  never commit a rebuilt copy on a feature branch: main rebuilds it on the
  push after a merge, so a branch-side copy only produces a merge conflict
  on a file whose correct resolution is always "regenerate it". Two merges
  stopped on exactly that. No test reads the file from disk -- they render
  through `dashboard.render()` -- so a branch never needs it current.
- A workflow input never reaches a shell command line. It goes through
  `env:` and is read as a quoted variable -- `"$ONLY"`, never
  `--only ${{ inputs.only }}`. A probe dispatched with "Lapangee,
  lavinoterie" in that box became two shell arguments and crashed the run,
  and on a public repo the same shape is an injection waiting for a quote.
  The same goes for `github.event.*` and any `steps.*.outputs.*` derived
  from them. `tests/test_workflows.py` enforces this, plus two neighbours:
  every expansion is quoted, and every variable a script reads is defined
  on *that* step (an `env:` block one step away expands to the empty
  string, which is how `--kind ""` would have shipped).
- The probe saves what it finds by default. A read-only probe that reaches
  two shops, parses their catalogues and commits nothing looks identical to
  a probe that failed -- it kept lavinoterie and pangee dark for three days
  after they had already answered. `apply` defaults to true in `probe.yml`
  and in the dashboard form, and a read-only run that found working shops
  prints a `NOTHING WAS SAVED` block naming them and the next step. Safety
  is not lost: `--apply` still only promotes shops that parsed a real
  response in that same run, and only after the suite passes.
- `probe.py --only` is forgiving about how names are typed (commas or
  spaces, any case) and unforgiving about names that do not exist: an
  unknown name exits non-zero and lists the real ones. Probing the empty
  set and exiting 0 is the failure mode this replaces.
- The probe's catalogue-path guessing is capped (`MAX_CATALOGUE_GUESSES`).
  One live run spent 24 requests on a single shop, all 404, and 107 of 150
  across eight -- with eleven unverified shops the budget binds and the
  tail goes unprobed without saying so.
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
