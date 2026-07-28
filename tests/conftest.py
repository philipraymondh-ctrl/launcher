"""The pipeline fixture: one whole scraper run, redirected off the network
and away from the repo, shared by the pipeline suites."""
import pytest

import crawler
import market
import notify
import scraper

from canned_shop import FakeCrawler, PRODUCERS, SHOPS


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    """Runs main() with everything redirected away from the repo."""
    sent = []

    monkeypatch.setattr(scraper, "SHOPS", SHOPS)
    monkeypatch.setattr(scraper, "PRODUCERS", PRODUCERS)
    monkeypatch.setattr(notify, "STATE_PATH", tmp_path / "seen.json")
    monkeypatch.setattr(notify, "HITS_PATH", tmp_path / "hits.json")
    monkeypatch.setattr(market, "OBSERVATIONS_PATH", tmp_path / "observations.json")
    # The real send path must run so state is persisted; only SMTP is stubbed.
    monkeypatch.setattr(notify, "send_email", lambda body: sent.append(body))

    def run(bodies, dry_run=False, max_requests=1000, fail_hosts=()):
        client = FakeCrawler(bodies, max_requests=max_requests, fail_hosts=fail_hosts)
        monkeypatch.setattr(crawler, "Crawler", lambda *a, **k: client)
        monkeypatch.setattr(scraper, "DRY_RUN", dry_run)
        scraper.main()
        return client

    run.sent = sent
    run.tmp = tmp_path
    return run
