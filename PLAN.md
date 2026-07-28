# PLAN — make silent failure impossible to miss

Three changes, one theme. Every bug in this project has been silent until
a human happened to look: a fetcher that never set `in_stock`, a breaker
that ate 404s, an alias that reported the wrong estate for weeks. The
scraper is loud about crashes and mute about being wrong.

## A. Adapter drift is currently indistinguishable from quiet

`main()` prints `[shop] ok, N hit(s)`. A shop that parses **zero
products** — because it restyled, changed platform, or started rendering
client-side — prints `ok, 0 hit(s)`, identical to a shop that simply has
nothing we watch. mareehaute dropping from 76 hits to 0 would look like a
normal night.

The baseline needs no new state: **every verified shop has a real saved
fixture that parses to > 0 products.** So a verified shop returning zero
products from a live fetch is drift, full stop.

- `check_shop` returns hits only; it must also report how many products
  were parsed. Return a small result object rather than a bare list.
- `main()` counts shops that parsed nothing and prints a distinct
  `DRIFT` line naming them.
- Any digest that is sent carries a `Shops that returned nothing` block,
  so it reaches the person rather than only the log.
- Deliberately **not** failing the run: a shop can be legitimately empty
  for a night, and a red run every hour trains you to ignore red runs.

## B. The digest cannot be audited by reading it

Three misattributions were caught by the owner reading producer names and
recognising the wrong estate. Nothing in a row says *why* it matched, so
checking one means opening the shop.

- `format_row` gains the matched alias: `Ganevat [ganevat]`. That is the
  whole diagnosis for a bad alias, inline.
- Requires `check_shop` to record which alias fired — `match_producers`
  currently discards it.

## C. Producers found nowhere are invisible

A producer can vanish from every catalogue — or its alias can break — and
the digest simply has no row for it. Absence reads as absence of stock.

- A `Watched but found nowhere` line listing those producers, in any
  digest that is sent.
- This is the cheapest possible detector for a broken alias: an alias
  typo makes a producer disappear from every shop at once.

## D. The tail of SHOPS starves

`main()` walks `SHOPS` in list order under a global
`MAX_REQUESTS_PER_RUN`. Currently ~77 of 120 are used, so nothing starves
— but the order is fixed, so the moment the budget binds it is *always
the same shops* that go unfetched, forever. That is a systematic blind
spot waiting to happen, not a random one.

- Rotate the starting offset by the hour: `offset = hour % len(shops)`.
  Deterministic, needs no stored counter, and over a day every shop gets
  an early slot.
- The existing "not reached this run" message must keep naming the shops
  actually skipped, which after rotation is no longer a list suffix.

## Order of work

1. Tests first, for all four, run to confirm failure.
2. Implement minimally.
3. Full suite green.
4. Re-read every changed file against this plan.

## Risks and how each is contained

| risk | containment |
|---|---|
| `check_shop`'s return type changes | it has three call sites (main, probe, tests); grep them all |
| digest grows noisier | the two new blocks appear only when non-empty |
| rotation confuses the budget message | assert the named shops are the unfetched ones, not a suffix |
| a legitimately empty shop reads as drift | it is reported, not escalated; run still exits 0 |

## Definition of done

- Whole suite green, including the 315 already there.
- No new persisted file, no workflow change, no new dependency.
- Every changed file re-read against this plan; no dead code.


---

# PLAN REVIEW — four corrections found by checking the code

Read against the actual source before writing anything. The plan was
wrong in four places, three of them about breaking existing callers.

## R1. `check_shop` must not change its return type

The plan said "return a small result object rather than a bare list".
`check_shop` has **15 call sites, 14 of them in tests**, and they assert on
the value directly: `== []`, `len(...) == 1`, iteration. Swapping in an
object rewrites ~13 tests for a field nobody asked them about — the
opposite of the minimum change, and it would bury the real diff.

**Corrected:** return a `list` subclass carrying the count as an
attribute. `ShopResult(list)` with `.products_parsed`. Every existing
assertion keeps working unchanged, because it still *is* a list.

## R2. The matched alias is already computed and discarded

The plan implied new matching work. `match_producers` already builds
`matched[canonical] = max(hits, key=len)` internally and returns only the
keys. But its return type is asserted all over the suite
(`== ["Ganevat"]`), so it cannot start returning pairs.

**Corrected:** add `matched_aliases(text) -> {producer: alias}` and let
`match_producers` return `list(matched_aliases(text))`. One function, no
duplicated logic, no changed signatures.

## R3. Rotation would make an existing test time-dependent

`test_the_run_stops_cleanly_when_the_budget_runs_out` runs with
`max_requests=1`. With the offset derived from `datetime.now().hour`,
*which* shop gets that single request depends on the hour the suite runs.
That is a flaky test waiting to happen, and I would have shipped it.

**Corrected:** extract `shop_order(shops, now=None)` taking an injectable
clock. `main()` passes nothing; tests pass a fixed hour and assert the
rotation directly.

## R4. `build_digest_body` cannot grow a required parameter

Existing notify tests call it with one argument.

**Corrected:** `build_digest_body(hits, notes=None)`. Blocks render only
when `notes` is non-empty, so every existing call and test is unaffected
and a clean run gains nothing.

## Unchanged after review

The `SHOPS[i:]` risk was real and is already in the risk table: with
rotation, the unfetched shops are no longer a list suffix, so both
messages must index the rotated order. Two occurrences, both in `main()`.
