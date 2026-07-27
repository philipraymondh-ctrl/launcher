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


def test_the_default_contact_is_a_reachable_url():
    agent = crawler_mod.Crawler().user_agent
    assert "https://github.com/" in agent
    assert agent.startswith("WineTrackerBot/")


def test_no_workflow_puts_an_email_on_the_wire():
    from pathlib import Path
    workflows = Path(__file__).parent.parent / ".github" / "workflows"
    for path in workflows.glob("*.yml"):
        assert "CONTACT_EMAIL" not in path.read_text(), (
            f"{path.name} still injects CONTACT_EMAIL into the crawler"
        )
