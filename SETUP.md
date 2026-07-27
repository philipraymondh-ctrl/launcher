# Wine producer scraper — setup

Monitors configured French/EU natural wine shops for named producers and
emails an alert on a hit. Runs hourly via GitHub Actions.

## 1. Install dependencies

```
pip install -r requirements.txt
```

## 2. Configure GitHub secrets

In the repo's Settings → Secrets and variables → Actions, add:

| Secret               | Value                                            |
|-----------------------|--------------------------------------------------|
| `GMAIL_SENDER`         | The Gmail address the alert is sent from         |
| `GMAIL_APP_PASSWORD`   | A Gmail [app password](https://myaccount.google.com/apppasswords) for that address (not the account password) |
| `NOTIFY_EMAIL`         | Where the alert should be sent                   |

The workflow (`.github/workflows/scraper.yml`) reads these as environment
variables at run time. It never prints their values.

## 3. Add shops

Shops live in the `SHOPS` list in `scraper.py`. Each entry needs a
`platform` (`shopify`, `woocommerce`, or `html`) and the fields that
platform's fetcher needs. See `CLAUDE.md` for the rules on adding a shop —
use the `shop-adapter` agent, one shop per commit, with a fixture under
`tests/fixtures/`.

## 4. Run locally

Normal run (sends email on a hit — requires the three env vars above):

```
GMAIL_SENDER=... GMAIL_APP_PASSWORD=... NOTIFY_EMAIL=... python scraper.py
```

Dry run (no SMTP, no secrets needed — prints the would-be email body to
stdout instead):

```
DRY_RUN=1 python scraper.py
```

## 5. Run the tests

```
pytest tests/ -q
```

Every shop in `SHOPS` has a saved fixture response under `tests/fixtures/`
and a test asserting what it should match. Run these before committing any
scraping change.

## Schedule

Hourly at :00 UTC (`0 * * * *`), 24 runs/day. Each run also has a
`workflow_dispatch` trigger for on-demand runs from the Actions tab.
