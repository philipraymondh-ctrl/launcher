"""The arithmetic that decides how much of the catalogue we ever see.

Three limits bound a run, and they are not independent: the request budget,
the wall clock the run stops itself on, and the workflow's job timeout. Get
their order wrong and the run fails in the worst available way -- a job killed
at the ceiling loses the whole crawl (no hits.json, no email, a red run and no
explanation), where the request budget merely degrades one catalogue and says
so in the coverage table.

These assert the *relationship*, never today's numbers, so raising a limit is
allowed and raising it past its neighbour is a CI failure.
"""
import re
from pathlib import Path

import yaml

import crawler
import scraper

WORKFLOW = Path(__file__).parent.parent / ".github" / "workflows" / "scraper.yml"

# Politeness plus a slow shop. The measured figure from a real cold-cache run
# was 4.58s/request (518s for 113 requests); this is the pessimistic end, and
# the budget must fit even then.
PESSIMISTIC_SECONDS_PER_REQUEST = (
    crawler.MIN_DELAY_SECONDS + crawler.JITTER_MAX_SECONDS / 2 + 1.5)


def test_the_request_budget_fits_inside_the_wall_clock():
    """The budget must bind before the clock does.

    The clock is only checked between shops, so when it binds it drops whole
    shops; the budget raises BudgetExceeded per request, which _paged and
    _walk_pages catch and turn into a TRUNCATED row. Degrading one catalogue
    beats losing a shop silently."""
    spent = crawler.DEFAULT_MAX_REQUESTS_PER_RUN * PESSIMISTIC_SECONDS_PER_REQUEST
    assert spent <= scraper.MAX_RUN_SECONDS, (
        f"{crawler.DEFAULT_MAX_REQUESTS_PER_RUN} requests need up to {spent:.0f}s "
        f"but the run stops itself at {scraper.MAX_RUN_SECONDS:.0f}s -- past this "
        f"point the wall clock binds first and drops whole shops"
    )


def test_the_wall_clock_fits_inside_the_job_timeout():
    """A cold-cache crawl of 22 shops took 8m38s against a 10-minute job
    timeout. The clock check happens between shops, so one shop can overshoot
    it by a full page budget before the run gets to stop."""
    doc = yaml.safe_load(WORKFLOW.read_text())
    timeout_seconds = doc["jobs"]["scrape"]["timeout-minutes"] * 60
    worst_overshoot = scraper.MAX_PAGES_PER_SHOP * PESSIMISTIC_SECONDS_PER_REQUEST
    test_step_allowance = 180
    needed = scraper.MAX_RUN_SECONDS + worst_overshoot + test_step_allowance
    assert needed < timeout_seconds, (
        f"the run can take {needed:.0f}s but the job is killed at "
        f"{timeout_seconds}s, which loses the crawl entirely"
    )


def test_the_workflow_default_matches_the_code_default():
    """Two numbers for one limit is how they drift apart."""
    text = WORKFLOW.read_text()
    defaults = set(re.findall(r"MAX_REQUESTS_PER_RUN: \$\{\{ inputs\."
                              r"max_requests_per_run \|\| '(\d+)' \}\}", text))
    assert defaults == {str(crawler.DEFAULT_MAX_REQUESTS_PER_RUN)}


def test_a_retry_costs_the_budget_as_much_as_a_page():
    """request_count increments per attempt, not per get(), so a dead host
    costs three units. Anyone sizing the budget by counting pages is out by
    the retries."""
    source = Path(crawler.__file__).read_text()
    attempt_loop = source.split("def _attempt_with_retries", 1)[1]
    assert "self.request_count += 1" in attempt_loop.split("for attempt", 1)[1]
