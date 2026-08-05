import json
import time as time_module

import pytest

import crawler as crawler_mod


class FakeResp:
    def __init__(self, status_code=200, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr(crawler_mod.time, "sleep", lambda *_: None)


@pytest.fixture
def tmp_cache(tmp_path):
    return tmp_path / "cache"


def make_crawler(tmp_cache, **kwargs):
    kwargs.setdefault("contact", "https://example.com/bot")
    return crawler_mod.Crawler(cache_dir=tmp_cache, **kwargs)


def test_robots_disallow_never_fetches_blocked_path(monkeypatch, tmp_cache):
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        if url.endswith("/robots.txt"):
            return FakeResp(200, "User-agent: *\nDisallow: /private/\n")
        raise AssertionError(f"blocked path must never be fetched: {url}")

    monkeypatch.setattr(crawler_mod.requests, "get", fake_get)
    c = make_crawler(tmp_cache)

    with pytest.raises(crawler_mod.Disallowed):
        c.get("https://shop.example.com/private/secret.json")

    assert calls == ["https://shop.example.com/robots.txt"]


def test_allowed_path_is_fetched(monkeypatch, tmp_cache):
    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/robots.txt"):
            return FakeResp(200, "User-agent: *\nDisallow: /private/\n")
        return FakeResp(200, '{"ok": true}', headers={"ETag": "abc"})

    monkeypatch.setattr(crawler_mod.requests, "get", fake_get)
    c = make_crawler(tmp_cache)

    result = c.get("https://shop.example.com/products.json")
    assert result.json() == {"ok": True}
    assert result.from_cache is False


def test_conditional_request_reuses_cache_on_304(monkeypatch, tmp_cache):
    responses = iter([
        FakeResp(200, "User-agent: *\n"),                  # robots.txt, fetched once
        FakeResp(200, '{"v": 1}', headers={"ETag": "v1"}),  # first fetch
        FakeResp(304, ""),                                  # second fetch: not modified
    ])

    def fake_get(url, headers=None, timeout=None):
        return next(responses)

    monkeypatch.setattr(crawler_mod.requests, "get", fake_get)
    c = make_crawler(tmp_cache)

    first = c.get("https://shop.example.com/products.json")
    assert first.from_cache is False
    assert first.json() == {"v": 1}

    # Make the cache entry look stale so the TTL doesn't short-circuit to a
    # cache hit without a network round-trip -- we want to exercise the
    # conditional-request path (If-None-Match -> 304), not the TTL path.
    cache_path = c._cache_path("https://shop.example.com/products.json")
    entry = json.loads(cache_path.read_text())
    entry["fetched_at"] = time_module.time() - crawler_mod.CACHE_TTL_SECONDS - 1
    cache_path.write_text(json.dumps(entry))

    second = c.get("https://shop.example.com/products.json")
    assert second.from_cache is True
    assert second.json() == {"v": 1}


def test_cache_hit_within_ttl_skips_network_entirely(monkeypatch, tmp_cache):
    call_count = {"n": 0}

    def fake_get(url, headers=None, timeout=None):
        call_count["n"] += 1
        if url.endswith("/robots.txt"):
            return FakeResp(200, "User-agent: *\n")
        return FakeResp(200, '{"ok": true}')

    monkeypatch.setattr(crawler_mod.requests, "get", fake_get)
    c = make_crawler(tmp_cache)

    c.get("https://shop.example.com/a.json")
    calls_after_first = call_count["n"]

    result = c.get("https://shop.example.com/a.json")
    assert result.from_cache is True
    assert call_count["n"] == calls_after_first


def test_fresh_env_bypasses_cache(monkeypatch, tmp_cache):
    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/robots.txt"):
            return FakeResp(200, "User-agent: *\n")
        return FakeResp(200, '{"ok": true}')

    monkeypatch.setattr(crawler_mod.requests, "get", fake_get)
    c = make_crawler(tmp_cache, fresh=True)

    c.get("https://shop.example.com/a.json")
    result = c.get("https://shop.example.com/a.json")
    assert result.from_cache is False


def test_backoff_retries_and_raises_after_max_attempts(monkeypatch, tmp_cache):
    call_count = {"n": 0}

    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/robots.txt"):
            return FakeResp(200, "User-agent: *\n")
        call_count["n"] += 1
        return FakeResp(503, "")

    monkeypatch.setattr(crawler_mod.requests, "get", fake_get)
    c = make_crawler(tmp_cache)

    with pytest.raises(crawler_mod.UpstreamError):
        c.get("https://shop.example.com/products.json")

    assert call_count["n"] == crawler_mod.MAX_ATTEMPTS


def test_retry_after_header_is_honoured(monkeypatch, tmp_cache):
    sleeps = []
    monkeypatch.setattr(crawler_mod.time, "sleep", lambda s: sleeps.append(s))

    seq = iter([
        FakeResp(200, "User-agent: *\n"),
        FakeResp(429, "", headers={"Retry-After": "7"}),
        FakeResp(200, '{"ok": true}'),
    ])

    def fake_get(url, headers=None, timeout=None):
        return next(seq)

    monkeypatch.setattr(crawler_mod.requests, "get", fake_get)
    c = make_crawler(tmp_cache)

    result = c.get("https://shop.example.com/products.json")
    assert result.json() == {"ok": True}
    assert 7.0 in sleeps


def test_circuit_breaker_opens_after_three_failed_requests(monkeypatch, tmp_cache):
    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/robots.txt"):
            return FakeResp(200, "User-agent: *\n")
        return FakeResp(500, "")

    monkeypatch.setattr(crawler_mod.requests, "get", fake_get)
    c = make_crawler(tmp_cache)

    for i in range(3):
        with pytest.raises(crawler_mod.UpstreamError):
            c.get(f"https://shop.example.com/{i}.json")

    with pytest.raises(crawler_mod.CircuitOpen):
        c.get("https://shop.example.com/one-more.json")


def test_budget_exceeded_stops_before_network_call(monkeypatch, tmp_cache):
    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/robots.txt"):
            return FakeResp(200, "User-agent: *\n")
        return FakeResp(200, '{"ok": true}')

    monkeypatch.setattr(crawler_mod.requests, "get", fake_get)
    c = make_crawler(tmp_cache, max_requests=1)

    first = c.get("https://shop.example.com/a.json")
    assert first.status_code == 200

    with pytest.raises(crawler_mod.BudgetExceeded):
        c.get("https://shop.example.com/b.json")


def test_min_delay_enforced_between_requests_to_same_host(monkeypatch, tmp_cache):
    sleeps = []
    monkeypatch.setattr(crawler_mod.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(crawler_mod.random, "uniform", lambda a, b: 0)

    fake_now = {"t": 1000.0}
    monkeypatch.setattr(crawler_mod.time, "monotonic", lambda: fake_now["t"])

    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/robots.txt"):
            return FakeResp(200, "User-agent: *\n")
        return FakeResp(200, '{"ok": true}')

    monkeypatch.setattr(crawler_mod.requests, "get", fake_get)
    c = make_crawler(tmp_cache, max_requests=10)

    c.get("https://shop.example.com/a.json")
    fake_now["t"] += 1  # only 1s elapsed, less than MIN_DELAY_SECONDS (3s)
    c.get("https://shop.example.com/b.json")

    assert any(abs(s - (crawler_mod.MIN_DELAY_SECONDS - 1)) < 0.01 for s in sleeps)


def test_user_agent_includes_a_contact_url(tmp_cache):
    # Shops should be able to reach the operator; that contact is a URL,
    # never a mailbox -- see the identity tests at the end of this file.
    c = crawler_mod.Crawler(cache_dir=tmp_cache, contact="https://example.com/bot")
    assert "https://example.com/bot" in c.user_agent


def test_crawl_delay_from_robots_overrides_min_delay(monkeypatch, tmp_cache):
    sleeps = []
    monkeypatch.setattr(crawler_mod.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(crawler_mod.random, "uniform", lambda a, b: 0)

    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/robots.txt"):
            return FakeResp(200, "User-agent: *\nCrawl-delay: 10\n")
        return FakeResp(200, '{"ok": true}')

    monkeypatch.setattr(crawler_mod.requests, "get", fake_get)
    c = make_crawler(tmp_cache, max_requests=10)

    fake_now = {"t": 1000.0}
    monkeypatch.setattr(crawler_mod.time, "monotonic", lambda: fake_now["t"])

    c.get("https://shop.example.com/a.json")
    fake_now["t"] += 2
    c.get("https://shop.example.com/b.json")

    # Crawl-delay: 10 should be honoured over the default 3s minimum.
    assert any(abs(s - 8) < 0.01 for s in sleeps)


# --- what counts as a host failure ------------------------------------------

def test_a_404_is_not_a_host_failure():
    """Catalogue discovery is mostly misses. Counting them tripped the
    breaker after three tries, so the path that would have worked was
    never reached."""
    assert crawler_mod.Crawler._is_host_failure(404) is False
    assert crawler_mod.Crawler._is_host_failure(410) is False
    assert crawler_mod.Crawler._is_host_failure(400) is False


def test_a_refusal_or_an_outage_is_a_host_failure():
    assert crawler_mod.Crawler._is_host_failure(None) is True     # no connection
    assert crawler_mod.Crawler._is_host_failure(500) is True
    assert crawler_mod.Crawler._is_host_failure(503) is True
    assert crawler_mod.Crawler._is_host_failure(429) is True      # slow down
    assert crawler_mod.Crawler._is_host_failure(403) is True      # blocked


def test_repeated_404s_never_open_the_circuit(monkeypatch):
    client = crawler_mod.Crawler()
    monkeypatch.setattr(client, "_allowed", lambda url: True)
    monkeypatch.setattr(client, "_wait_for_host", lambda host: None)
    monkeypatch.setattr(
        client, "_attempt_with_retries",
        lambda *a, **k: (_ for _ in ()).throw(crawler_mod.UpstreamError("HTTP 404", status_code=404)),
    )
    for _ in range(crawler_mod.CIRCUIT_BREAKER_THRESHOLD + 3):
        with pytest.raises(crawler_mod.UpstreamError):
            client.get("https://missing.example/nope")
    # Still reachable: the host was answering the whole time.
    assert client._consecutive_failures["missing.example"] == 0


def test_repeated_outages_still_open_the_circuit(monkeypatch):
    client = crawler_mod.Crawler()
    monkeypatch.setattr(client, "_allowed", lambda url: True)
    monkeypatch.setattr(client, "_wait_for_host", lambda host: None)
    monkeypatch.setattr(
        client, "_attempt_with_retries",
        lambda *a, **k: (_ for _ in ()).throw(crawler_mod.UpstreamError("refused")),
    )
    for _ in range(crawler_mod.CIRCUIT_BREAKER_THRESHOLD):
        with pytest.raises(crawler_mod.UpstreamError):
            client.get("https://down.example/x")
    with pytest.raises(crawler_mod.CircuitOpen):
        client.get("https://down.example/y")


# --- the User-Agent must not carry anyone's identity -------------------------

def test_the_user_agent_never_contains_an_email_address():
    """It goes to every shop on every request and into public run logs."""
    assert "@" not in crawler_mod.Crawler().user_agent


def test_a_contact_email_in_the_environment_is_not_used(monkeypatch):
    """CONTACT_EMAIL used to be interpolated straight into the header."""
    monkeypatch.setenv("CONTACT_EMAIL", "someone@example.com")
    agent = crawler_mod.Crawler().user_agent
    assert "someone@example.com" not in agent
    assert "@" not in agent


def test_an_email_passed_as_the_contact_is_refused_loudly(monkeypatch):
    with pytest.raises(ValueError, match="must not be an email"):
        crawler_mod.Crawler(contact="someone@example.com")


def test_the_default_user_agent_identifies_nobody():
    """It goes to every shop on every request. It must not carry the
    owner's name, their account name, or any URL that implies either."""
    agent = crawler_mod.Crawler().user_agent
    assert agent == crawler_mod.BOT_NAME
    assert "@" not in agent
    assert "://" not in agent
    assert "github" not in agent.lower()


def test_the_bot_name_does_not_say_scraper():
    """Some shops block the word on sight, however politely the thing
    behaves, and it describes us to no one's benefit."""
    assert "scraper" not in crawler_mod.BOT_NAME.lower()
    assert "crawler" not in crawler_mod.BOT_NAME.lower()
    assert "bot" in crawler_mod.BOT_NAME.lower(), "still be honest that it is automated"


def test_an_opt_in_contact_url_is_still_appended():
    agent = crawler_mod.Crawler(contact="https://example.com/about-the-bot").user_agent
    assert agent == crawler_mod.BOT_NAME + " (+https://example.com/about-the-bot)"


def test_no_workflow_puts_an_email_on_the_wire():
    from pathlib import Path
    workflows = Path(__file__).parent.parent / ".github" / "workflows"
    for path in workflows.glob("*.yml"):
        assert "CONTACT_EMAIL" not in path.read_text(), (
            f"{path.name} still injects CONTACT_EMAIL into the crawler"
        )


# --- bot challenges: a 200 that is not content -------------------------------
#
# Two verified shops were reported healthy for weeks while serving nothing.
# vinnaturel.fr answers 200 with "One moment, please... Please wait while your
# request is being verified"; vinopura.nl answers 200 with 221 bytes of
# meta-refresh to /.well-known/sgcaptcha/. Both went into the 6h disk cache,
# so each challenge stayed true for six more runs. The captures below are the
# real bodies, trimmed.

VINNATUREL_CHALLENGE = (
    '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
    '<title>One moment, please...</title></head><body>'
    '<div id="outer-container"><div id="container"><div class="throbber"></div>'
    '<div id="text">Please wait while your request is being verified...</div>'
    '</div></div></body></html>'
)
SGCAPTCHA_CHALLENGE = (
    '<html><head><link rel="icon" href="data:;">'
    '<meta http-equiv="refresh" content="0;/.well-known/sgcaptcha/'
    '?r=%2F&y=ipc:172.182.245.132:1785882854.337"></head></html>'
)
A_REAL_SHOP_PAGE = (
    '<html><body><h1>Nos vins</h1>'
    + "".join(f'<a href="/vin/{i}">Cuvee {i} 21,50 €</a>' for i in range(12))
    + '</body></html>'
)


def test_a_challenge_page_is_recognised_for_what_it_is():
    assert crawler_mod.looks_like_challenge(VINNATUREL_CHALLENGE)
    assert "sgcaptcha" in crawler_mod.looks_like_challenge(SGCAPTCHA_CHALLENGE)


def test_a_real_catalogue_is_never_called_a_challenge():
    """A false positive takes a working shop dark, which is worse than the
    failure this replaces."""
    assert crawler_mod.looks_like_challenge(A_REAL_SHOP_PAGE) is None
    assert crawler_mod.looks_like_challenge("") is None
    # The phrases alone must not be enough: a shop whose own copy says
    # "just a moment" while linking to its catalogue is a shop, not a wall.
    chatty = ('<html><body><p>Just a moment while we pour...</p>'
              + "".join(f'<a href="/v/{i}">Cuvee {i} 30,00 €</a>' for i in range(9))
              + '</body></html>')
    assert crawler_mod.looks_like_challenge(chatty) is None
    # And a page that merely writes about captchas is not one.
    assert crawler_mod.looks_like_challenge(
        '<html><body><h1>Why we do not use a captcha</h1>'
        '<a href="/a">a</a><a href="/b">b</a></body></html>') is None


def test_a_challenge_raises_instead_of_returning_a_healthy_200(monkeypatch, tmp_cache):
    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/robots.txt"):
            return FakeResp(200, "")
        return FakeResp(200, VINNATUREL_CHALLENGE)

    monkeypatch.setattr(crawler_mod.requests, "get", fake_get)
    client = make_crawler(tmp_cache)

    with pytest.raises(crawler_mod.Challenged):
        client.get("https://challenged.example/")


def test_a_challenge_is_never_written_to_the_cache(monkeypatch, tmp_cache):
    """A cached challenge is a lie with a six-hour shelf life."""
    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/robots.txt"):
            return FakeResp(200, "")
        return FakeResp(200, SGCAPTCHA_CHALLENGE)

    monkeypatch.setattr(crawler_mod.requests, "get", fake_get)
    client = make_crawler(tmp_cache)
    with pytest.raises(crawler_mod.Challenged):
        client.get("https://challenged.example/shop")

    assert not list(tmp_cache.glob("*.json")) or all(
        "sgcaptcha" not in p.read_text() for p in tmp_cache.glob("*.json"))


def test_a_challenge_already_in_the_cache_is_not_served(monkeypatch, tmp_cache):
    """Six hours of poison were already in the cache when this shipped."""
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        if url.endswith("/robots.txt"):
            return FakeResp(200, "")
        return FakeResp(200, A_REAL_SHOP_PAGE)

    monkeypatch.setattr(crawler_mod.requests, "get", fake_get)
    client = make_crawler(tmp_cache)
    url = "https://recovered.example/shop"
    client._write_cache(url, {
        "status_code": 200, "text": VINNATUREL_CHALLENGE,
        "etag": None, "last_modified": None, "fetched_at": time_module.time(),
    })

    result = client.get(url)

    assert "Nos vins" in result.text
    assert url in calls, "the poisoned cache entry was served instead of refetching"


def test_a_challenge_is_not_retried(monkeypatch, tmp_cache):
    """Three goes at a captcha is what earns a real block."""
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        if url.endswith("/robots.txt"):
            return FakeResp(200, "")
        return FakeResp(200, VINNATUREL_CHALLENGE)

    monkeypatch.setattr(crawler_mod.requests, "get", fake_get)
    client = make_crawler(tmp_cache)
    with pytest.raises(crawler_mod.Challenged):
        client.get("https://challenged.example/")

    assert len([c for c in calls if "robots" not in c]) == 1


def test_a_challenging_host_eventually_trips_the_breaker(monkeypatch, tmp_cache):
    """A shop that challenges us is refusing, the same as a 403."""
    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/robots.txt"):
            return FakeResp(200, "")
        return FakeResp(200, VINNATUREL_CHALLENGE)

    monkeypatch.setattr(crawler_mod.requests, "get", fake_get)
    client = make_crawler(tmp_cache)
    for i in range(crawler_mod.CIRCUIT_BREAKER_THRESHOLD):
        with pytest.raises(crawler_mod.Challenged):
            client.get(f"https://challenged.example/{i}")

    with pytest.raises(crawler_mod.CircuitOpen):
        client.get("https://challenged.example/anything")
