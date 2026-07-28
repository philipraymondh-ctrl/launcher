# PLAN — end-to-end coverage of a scraper run

## Why

Every stage of this pipeline has unit tests. The *seam between* them has
none. `scraper.main()` is the only place where fetching, producer
matching, stock filtering, observation recording, market pricing,
evaluation and notification meet, and nothing exercises it as one piece.

The single test that calls `main()` today —
`test_unverified_shops_are_skipped_by_main` — stubs the crawler so that
any real fetch raises. It proves shops are skipped and nothing else. Every
bug this session was in a seam:

| bug | unit-tested part | broken seam |
|---|---|---|
| sold-out Shopify variants alerting | `is_out_of_stock` was fine | the fetcher never set `in_stock` |
| `KeyError: 'href'` | `find_producer_links` was fine | `find_products` used the same helper |
| index route never read | `find_producer_links` was fine | `fetch_html` passed the wrong URL |
| 404 tripping the breaker | `_record_failure` was fine | every HTML shop's discovery |

So the target is not more coverage of the parts. It is one test module
that runs the whole thing on canned responses and asserts what comes out
the far end.

## Constraints (from CLAUDE.md)

- **No test may touch the network.** `main()` builds its own `Crawler`, so
  `crawler.Crawler` must be replaced, not merely fed.
- **Nothing may write into the repo.** `main()` writes `seen.json`,
  `hits.json` and `observations.json` at module-level default paths. All
  three must be redirected to `tmp_path`.
- **Don't pin today's config.** `SHOPS` and `PRODUCERS` must be replaced
  with a synthetic set, so adding or dropping a real shop never fails
  these tests.

## Steps

1. **`tests/test_end_to_end.py`** with a `run_pipeline` fixture that:
   - substitutes a synthetic `SHOPS` (one Shopify, one WooCommerce, one
     HTML) and `PRODUCERS`;
   - substitutes `crawler.Crawler` with a fake serving canned bodies by
     URL and counting requests;
   - redirects `notify.STATE_PATH`, `notify.HITS_PATH` and
     `market.OBSERVATIONS_PATH` into `tmp_path`;
   - captures the digest by stubbing `notify.send_email`, so the *real*
     send path runs (state is saved) without SMTP.

2. **Happy path.** Three shops, several producers, mixed stock. Assert:
   hits from all three platforms; `hits.json` written; digest contains the
   header row and one line per alerting hit.

3. **Stock.** A sold-out Shopify variant, a WooCommerce `is_in_stock:
   false`, and an HTML listing saying "épuisé" must all be absent from the
   digest while still being parsed (the probe counts parsed products).

4. **Market pricing across shops.** The same wine at two shops at
   different prices must produce a cross-shop reference, and the cheaper
   one classify `DEAL` with a basis naming the other shop — not a
   placeholder.

5. **Négoce vs domaine.** A cheap négoce bottle and an expensive domaine
   bottle from one producer must not be scored against each other.

6. **Cooldown.** Running twice must email once: the second run finds
   nothing new and exits without sending.

7. **A dry run persists nothing.** `seen.json` and `observations.json`
   must be untouched, so previewing never consumes a real find.

8. **Failure isolation.** One shop raising `UpstreamError` must not stop
   the others, and the digest must still contain their hits.

9. **Budget.** With `MAX_REQUESTS_PER_RUN` below the shop count, the run
   stops cleanly and names the shops it did not reach.

10. **Empty run.** No qualifying hits sends no email and does not crash.

## Order of work

Write all of the above **first** and run them. Expect failures — they mark
either a real defect or a seam that cannot currently be tested (e.g. a
path that cannot be redirected). Fix with the minimum change, preferring a
change to the code under test only where the code is genuinely at fault;
otherwise adjust the harness.

## Definition of done

- The whole suite green, including the pre-existing 299.
- Every file touched re-read and cross-checked against this plan.
- No test writes outside `tmp_path`; no test constructs a real `Crawler`.
