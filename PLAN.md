# Round 3 — the shops that still don't deliver

Four council seats (platform archaeologist, crawl-budget architect, red-team
skeptic, robustness engineer — the last died mid-report to a session limit and
its ground was covered by the other three), one live coverage run, and one live
capture probe of nine pages nobody had ever looked at.

Everything below is either reproduced locally against `probe_pages/` and
`tests/fixtures/`, or measured on a runner.

---

## What the evidence actually says

### The bug that reorders the whole plan

`parse_price("Ganevat Poulprix 2022 €45,00")` returns **2022.0**.

`PRICE_PATTERN`'s second branch, `(\d{1,4}(?:[.,]\d{2})?)\s?(?:€|EUR|USD|\$)`,
matches the vintage, the space, and the *following* price's currency symbol.
It has never fired because every verified HTML shop is French and writes
`45,00 €` — number first. Symbol-first is Belgian and Dutch usage, and every
shop this plan would enable is Belgian or Dutch.

The blast radius is not local. `market.py` keeps observations 180 days and
`MIN_SHOPS = 1`, so one poisoned observation makes another shop's honest €44
bottle a DEAL; the correction later reads as a 97.8% price drop, which bypasses
the cooldown by design. One bad night, two false alerts, six months of poisoned
reference.

**A precondition for every other item, not a line item.**

### Two shops are not broken, they are challenged

The capture probe settled both, and neither diagnosis was on the table:

- `vinnaturel.fr` returns **HTTP 200** with `<title>One moment, please...</title>`
  and "Please wait while your request is being verified..." — a JS bot
  challenge. Not a dead domain, not an outage, not the wrong TLD.
- `vinopura.nl` returns **HTTP 200**, 221 bytes, meta-refresh to
  `/.well-known/sgcaptcha/` — SiteGround's captcha. That, not a Store API
  change, is the "parse error" in two consecutive runs.

The honest report for both is *blocked*; today it says `ok, 0 products` for one
and `parse error` for the other. A 200 that is a challenge page is a failure
class the code has no name for — and it lands in the 6h disk cache, so one
challenge poisons six runs.

We do not evade challenges. We name them.

### One generic parser bug hides a whole shop

`autoselect._price_nodes` accepts an element only when its **own** text carries
a currency-adjacent price. WooCommerce — and most theme families — render
`<span class="amount">12.50<span class="currencySymbol">€</span></span>`, so the
digits and the marker sit in different elements and *no* element qualifies. The
entire grid is invisible.

Measured over all 28 captures: an innermost-full-text rule takes
`vinovivo.shop.html` from **0 → 10 products** and leaves **24 of 28 files
byte-identical** (the three other changes are vinovivo's own pages).

That, not selectors and not a platform route, is what makes vinovivo readable.

### What the shops are, on tested evidence

| shop | platform | verdict |
|---|---|---|
| vinovivo | WooCommerce 3.4.8 | **readable** once `_price_nodes` and the next-link rule land. `/shop`, 315 products, 10/page, `/shop/page/N` confirmed live on page 2. |
| purovino | Squarespace Commerce | **readable** via `?format=json`: ~96 items in **one request**, with `title`, `fullUrl`, `variants[].priceMoney.value`, `qtyInStock`. |
| vinnaturelbe | PrestaShop 1.6 | **unreadable**. The real catalogue (`/fr/categorie/11-acheter-en-ligne`, 61KB, 40 product links) has **zero currency markers**. Price wall or catalogue mode. |
| purewijnen | Drupal 7, no commerce module | **unreadable**. `/nl/wijnen-bestellen` and `/nl/wijnkaart` both **zero prices**, on top of the grower bio's 0-in-28KB. |
| leszinzinsduvin | hand-rolled PHP | **already correct**. Prices *are* on the grower pages (`110,00 €` on Labet's); Ganevat simply has no bottles listed. "8 products, all sold out" is the right answer. |
| naturavin / vinscheznous | — | 403 / no DNS. Unchanged. |

Two of those are deletions of a hope, written down as a tested reason. That is
a result: four shops already carry exactly that.

### Coverage is worse than the table admits, and the table lies when the budget binds

- **vinnouveau**: its own page says `Affichage 1-24 de 2827 article(s)` and
  links `?page=118`. We read 480 — **17%**.
- **pangee**: still pointed at `/nouveaux-produits` (91) while `/fr/25-vins`
  holds **791** — the exact "a strip is one page, a catalogue runs to twenty"
  bug CLAUDE.md says the probe was rewritten to stop making.
- **winenot**: `fetch_html` spends one shared 20-page budget over
  `catalogue_starts()` **in fixed list order**, so rosé, sparkling, moelleux and
  muté have never been read once, at any budget.
- **`shop_order()`** computes `hour % len(shops)` with 29 shops, so offsets
  24-28 never occur: five shops can never lead a run. The test that claims
  otherwise runs against the 3-shop canned list and passes vacuously.
- **When the budget binds, the digest lies.** Reproduced at `max_requests=1`:
  unfetched shops vanish from the coverage table entirely and their producers
  are reported under "Watched but found nowhere" — the one note whose whole
  purpose is to mean "this alias matches nothing".

---

## The plan

Ordered so each step is safe alone, and nothing that could produce a *wrong*
number ships before the guard that stops it.

1. **Vintage guard on `PRICE_PATTERN`** (blocks everything else). Reject a bare
   4-digit year in the number-then-symbol branch. Keep `positive_price`. Do not
   loosen whitespace anywhere.
2. **Name a challenge page instead of believing it.** `crawler.py` detects an
   interstitial (meta-refresh to a captcha path; a tiny "verifying your request"
   body) and raises rather than returning a 200. **Never cache it.** `main()`
   gets a `blocked (challenge)` status and a digest note. No UA spoofing, no
   retry-until-through.
3. **`_price_nodes`: innermost element whose full text holds the price**, with
   the red-team's three guards — no descendant `<a>`, a length cap so prose is
   not a price cell, `_block_for`'s climb untouched.
4. **Stock from markup, not only text.** WooCommerce puts it in the `<li>` class
   list and the card text is identical either way. Without this, vinovivo's
   sold-out bottles are alerted *and* written to `seen.json`, which permanently
   destroys the restock alert. The signal may only ever add out-of-stock, never
   flip a listing to in stock; `products_parsed` must not change.
5. **Next page by class token** (`next`, `suivant` as whole tokens): vinovivo's
   next arrow is an empty `<a class="next page-numbers">`. Validated across 44
   real pages: 6 correct hits, 0 false positives. **Not** numeric `/page/N`
   guessing.
6. **A 404 on page ≥ 2 ends a catalogue; it does not lose one.**
   `_walk_pages` calls `raise_for_status()` outside its `try`, so one 404
   discards every page already read and reports the shop `unreachable`.
7. **Squarespace fetcher for purovino**, reading `variants[].priceMoney.value`
   (the item-level `priceMoney` is `0.00` — reading it would make every bottle a
   permanent DEAL), `qtyInStock`, `fullUrl`, `positive_price` on the way out,
   cursor pagination. Prerequisites: an `EMPTY_PAGE` entry, a `trim_payload`
   field whitelist (or `websiteSettings` — contact email, address, phone — gets
   committed to a public repo), and **robots.txt read first**, because
   `urllib.robotparser` ignores `*` wildcards and Squarespace writes
   query-string exclusions in exactly that form.
8. **Tell the truth when the budget binds.** Unreached shops get a
   `STATUS = not reached` row; "found nowhere" is computed only over shops
   actually fetched. Precondition for item 9.
9. **Coverage arithmetic.** Monotonic hour counter in `shop_order` (+ a test
   over the real `SHOPS`); rotate `catalogue_starts`; derive each catalogue's
   real size from its own page-1 counter (free) and report
   `pages_read/pages_total`; `MAX_REQUESTS_PER_RUN` 120 → 160 (the largest that
   still fits 900s at a pessimistic 5.5s/request; past ~163 the clock binds and
   the clock drops *whole shops*). `MAX_PAGES_PER_SHOP` stays 20 globally.
10. **pangee's catalogue through the probe** — `/fr/25-vins`, set by
    `probe.py --apply` against a real response, never by hand.
11. **Write the tested verdicts into CLAUDE.md** — one sentence each for
    purewijnen, vinnaturelbe, vinnaturel, vinopura, naming the page tested and
    what it held.

## Explicitly not doing

- **Looser price-whitespace matching.** Fixes nothing real (0 price nodes before
  *and* after on vinovivo's actual markup — the problem is a tag boundary, not
  indentation), only "fixes" our own prettified diagnostic artifact, and turns
  `Clavelin 2016 EUR 250,00` from an honest `NOREF` into a confident 2016.0.
- **Numeric pagination discovery** (item 5).
- **Runtime platform re-detection.** Breaks what `verified` means; 12
  requests/hour to re-ask what the probe already answers.
- **Retrying vinopura.** A retry re-reads the poisoned cache entry, and a
  partial retry launders a truncated catalogue into a "complete" one.
- **Rotating page-offset cursors.** Unstable sort orders make the window skip
  and duplicate silently; the notes flap hourly; a new state file is one
  `git add -A` from being pinned for ever.
- **Product-page fetching.** 315-330 requests for one shop; `autoselect` on a
  product page reads the *related-products strip*; `NON_PRODUCT_PATH` does not
  exclude `?add-to-cart=`, so following discovered links can perform
  state-changing GETs.
- **A global `MAX_PAGES_PER_SHOP` raise** without item 8.

## Noted, not scheduled

- `urllib.robotparser` ignores `*` wildcards, so robots compliance is weaker
  than it looks for query-string rules. Item 7 works around it by hand.
- `notify.item_key` hashes the raw URL, so a shop with a rotating query
  parameter would re-alert hourly. Not observed on any current shop.
- `MIN_SHOPS = 1`: one observation sets a reference. The amplifier behind item 1.
- `coverage.json` is tracked and holds test rows; `observations.json` is neither
  tracked nor ignored, and two workflows run `git add -A`.
