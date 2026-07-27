# Wine producer scraper — setup

Monitors configured French/EU natural wine shops for named producers,
prices each hit against a manually-maintained reference book, and sends a
digest email on anything alert-worthy. Runs hourly via GitHub Actions,
politely (robots.txt, rate limiting, backoff — see `crawler.py`).

## 1. Install dependencies

```
pip install -r requirements.txt
```

## 2. Configure GitHub secrets and variables

In the repo's Settings → Secrets and variables → Actions:

Secrets (sensitive):

| Secret               | Value                                            |
|-----------------------|--------------------------------------------------|
| `GMAIL_SENDER`         | The Gmail address the alert is sent from         |
| `GMAIL_APP_PASSWORD`   | A Gmail [app password](https://myaccount.google.com/apppasswords) for that address (not the account password) |
| `NOTIFY_EMAIL`         | Where the alert should be sent                   |

Repository variables (not sensitive — this is who the crawler identifies
itself as, deliberately visible in the User-Agent it sends):

| Variable        | Value                                                     |
|-----------------|------------------------------------------------------------|
| `CONTACT_EMAIL`  | An email a shop operator could reach you at if this bot causes them trouble |

The workflow (`.github/workflows/scraper.yml`) reads these as environment
variables at run time. It never prints secret values.

## 3. Add / confirm shops

Shops live in the `SHOPS` list in `scraper.py`. Each entry needs a
`platform` (`shopify`, `woocommerce`, or `html`), the fields that
platform's fetcher needs, and a `verified` flag. `main()` skips any shop
with `verified: False` before making a network call — as shipped, every
shop in `SHOPS` is unverified (added from research, not a real fetch; see
each fixture's `_note`/leading comment for what's real vs. guessed), so a
normal run does nothing until shops are confirmed.

To bring shops online, run the **Probe Shops** workflow with **apply**
ticked. It fetches each shop's real endpoint, and for every shop that
returns a parseable catalogue it saves the real response as that shop's
fixture (trimmed, keeping every producer match), corrects the platform and
sets `verified: true` -- then runs the full test suite and only commits if
it passes. Shops that fail to probe are left unverified with the reason
logged.

Run it read-only first (apply unticked) to see what would happen; that
just uploads a report and the real responses as an artifact.

## 4. Run locally

Normal run (sends a digest email on anything alert-worthy — requires the
three secrets above):

```
GMAIL_SENDER=... GMAIL_APP_PASSWORD=... NOTIFY_EMAIL=... CONTACT_EMAIL=you@example.com python scraper.py
```

Dry run (no SMTP, no secrets needed — prints the would-be digest to stdout
instead):

```
DRY_RUN=1 python scraper.py
```

Useful crawl-layer env vars (see `crawler.py`):

| Var                     | Effect                                                  |
|--------------------------|----------------------------------------------------------|
| `CONTACT_EMAIL`           | Included in the User-Agent so a shop operator can reach you |
| `MAX_REQUESTS_PER_RUN`    | Hard cap on requests this run (default 120); the run stops cleanly and logs unreached shops when hit |
| `FRESH=1`                 | Bypass the 6h disk cache for this run                    |

State/output files (all gitignored, all safe to delete): `seen.json` (per-item
cooldown state), `.cache/` (the disk cache), `hits.json` (every evaluated hit
from the last run, regardless of whether it was alert-worthy).

## 5. Check the price book

```
python pricebook.py --stale
```

Lists every producer in `prices.yaml` that's still `verified: false` or
whose `last_verified` is more than 180 days old. Fill in real numbers
manually from Wine-Searcher (this project never fetches it automatically —
that's against their terms), then set `verified: true` and `last_verified`
to today's date.

## 6. Run the tests

```
pytest tests/ -q
```

Every shop in `SHOPS` has a saved fixture response under `tests/fixtures/`
and a test asserting what it should match. Run these before committing any
scraping change.

## 7. The dashboard

`wine.html` is a generated status page and control panel: current shops and
their platforms, producers and reference prices, and buttons to run the
workflows or add config. Regenerate locally with `python dashboard.py`; a
workflow rebuilds it whenever `scraper.py`, `prices.yaml` or `dashboard.py`
changes on `main`.

If GitHub Pages is enabled for the repo it is served at
`https://<user>.github.io/launcher/wine.html` (the existing `index.html`
launcher app keeps the root path). It holds no credentials -- every button
links out to GitHub, which handles sign-in.

## 8. Changing config from a phone

Two issue forms, linked from the dashboard. Each one adds, updates **and**
removes -- there is no separate "edit" path to hunt for.

**Producers** -- name, aliases, region, reference price.
- Naming an existing producer *updates* it. Fill only what you want to
  change; blank fields keep their current value. So correcting a price is
  the same one-step action as adding a producer.
- Ticking "checked myself" marks the price verified and stamps today's
  date; you never type a date. It's refused if there's no price to vouch
  for.
- The bulk box adds several at once, one per line:
  `Name | aliases | region | price` (region and price optional).
- Ticking Remove in the danger zone deletes the producer instead.

**Shops** -- short name and URL.
- An existing name re-points that shop and resets it to `verified: false`,
  since the old verification no longer applies. Re-probe it afterwards.
- Ticking Remove deletes the shop and its fixture.

Submitting a form runs `.github/workflows/apply-config.yml`, which
validates the input, edits `scraper.py`/`prices.yaml`, rebuilds the
dashboard, runs the full test suite, and only then commits to `main`. It
comments the commit link on the issue and closes it. There is no pull
request to merge -- if the tests fail nothing is committed, and every
change is revertible through git.

Only issues opened by the repo owner are processed -- the repo is public,
so this gate stops a stranger from driving edits to `scraper.py`.

## Schedule

Hourly at :00 UTC (`0 * * * *`), 24 runs/day. Each run also has a
`workflow_dispatch` trigger for on-demand runs from the Actions tab, with
inputs to bypass the cache (`fresh`) or override the request budget
(`max_requests_per_run`) for that one run.
