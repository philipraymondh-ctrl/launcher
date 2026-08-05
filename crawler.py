"""Single outbound-fetch layer for the wine scraper.

Every HTTP GET the project makes goes through Crawler.get(). No other
module may call `requests` directly once this exists -- that's what makes
robots.txt compliance, rate limiting, backoff, the circuit breaker, and the
disk cache apply uniformly instead of being re-implemented (or forgotten)
per shop.

Preferring JSON endpoints (Shopify /products.json, WooCommerce Store API)
over HTML is not implemented here -- it's already structural in
scraper.py's per-shop "platform" routing (shop-adapter picks the JSON
adapter first, HTML is the last-resort fallback). One JSON call already
replaces many page fetches by construction; this layer just makes whichever
call actually happens polite.
"""
import hashlib
import json
import os
import random
import re
import threading
import time
import urllib.robotparser
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode, urlparse

import requests

TIMEOUT = 15
MIN_DELAY_SECONDS = 3.0
JITTER_MAX_SECONDS = 2.0
BACKOFF_SCHEDULE = [5, 15, 45]
MAX_ATTEMPTS = 3
CIRCUIT_BREAKER_THRESHOLD = 3
CACHE_TTL_SECONDS = 6 * 3600
DEFAULT_MAX_REQUESTS_PER_RUN = 120
DEFAULT_CACHE_DIR = Path(__file__).parent / ".cache"


class FetchError(Exception):
    """Base class for errors Crawler.get() raises after policy checks/retries."""


class Disallowed(FetchError):
    """robots.txt disallows this URL for our user-agent."""


# What every shop sees. Deliberately says nothing about who runs it:
# no personal name, no account name, and not the word "scraper" -- which
# some shops block on sight regardless of how politely the thing behaves.
BOT_NAME = "WineTrackerBot/1.0"
# No contact by default. A repository URL was the obvious polite choice,
# but this repo's URL carries the owner's account name, which is the very
# thing being kept off the wire. Set CONTACT_URL to opt back in to a
# contact that gives nothing away.
DEFAULT_CONTACT = ""


class CircuitOpen(FetchError):
    """This host had 3+ consecutive failures this run; it's being skipped."""


class BudgetExceeded(FetchError):
    """MAX_REQUESTS_PER_RUN was reached; this URL was not fetched."""


class Challenged(FetchError):
    """The server answered 200 with a bot-challenge page instead of content.

    vinnaturel.fr returns "One moment, please... Please wait while your
    request is being verified"; vinopura.nl returns 221 bytes of meta-refresh
    to /.well-known/sgcaptcha/. Both are HTTP 200, so every layer below this
    read them as healthy: one shop reported "ok, 0 products" for weeks and
    the other a "parse error", and the challenge went into the 6h disk cache
    where it stayed true for six more runs.

    A challenge is not something to get past. It is a shop saying no, and the
    run's job is to say so out loud.
    """


class UpstreamError(FetchError):
    """The request ultimately failed (network error or non-2xx) after retries.

    status_code is the HTTP status when the server answered but unhappily
    (404, 500, ...), and None when the connection itself never succeeded
    (DNS failure, timeout, refused). Callers use that to tell "this path
    isn't there" apart from "this host is unreachable".
    """

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class FetchResult:
    """Uniform response shape regardless of whether it came from the network
    (fresh or after a 304) or was served straight from the disk cache."""

    def __init__(self, status_code, text, from_cache=False):
        self.status_code = status_code
        self.text = text
        self.from_cache = from_cache

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise UpstreamError(f"HTTP {self.status_code}", status_code=self.status_code)


# A challenge page is short, says one of a handful of things, and links
# nowhere. Each half of that matters: the phrases alone would misread a shop
# whose copy happens to say "just a moment", and a page's own link count is
# what separates an interstitial from a catalogue. Getting this wrong in the
# generous direction takes a working shop dark, so both halves are required.
CHALLENGE_PHRASES = (
    "one moment, please",
    "just a moment",
    "please wait while your request is being verified",
    "checking your browser",
    "verifying you are human",
    "enable javascript and cookies to continue",
    "attention required",
)
# Only ever matched inside a meta-refresh target, never in page text: a blog
# post about captchas is not a captcha.
CHALLENGE_REDIRECTS = ("sgcaptcha", "captcha", "/cdn-cgi/challenge", "challenge-platform")
CHALLENGE_MAX_LINKS = 2
META_REFRESH = re.compile(
    r"""<meta[^>]+http-equiv=["']?refresh["']?[^>]+content=["']([^"']+)["']""", re.I)


def looks_like_challenge(text):
    """Why this 200 is not content, or None if it is.

    Deliberately conservative -- a false positive is a working shop reported
    as blocked, which is worse than the failure it replaces.
    """
    body = (text or "")[:20_000]
    refresh = META_REFRESH.search(body)
    if refresh:
        target = refresh.group(1).lower()
        for marker in CHALLENGE_REDIRECTS:
            if marker in target:
                return f"meta-refresh to {marker}"
    lowered = body.lower()
    if lowered.count("<a ") <= CHALLENGE_MAX_LINKS:
        for phrase in CHALLENGE_PHRASES:
            if phrase in lowered:
                return f"interstitial saying {phrase!r}"
    return None


def _build_url(url, params):
    if not params:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{urlencode(params)}"


def _host_of(url):
    return urlparse(url).netloc


class Crawler:
    def __init__(self, cache_dir=None, max_requests=None, contact=None, fresh=None):
        self.cache_dir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_requests = (
            max_requests
            if max_requests is not None
            else int(os.environ.get("MAX_REQUESTS_PER_RUN", DEFAULT_MAX_REQUESTS_PER_RUN))
        )
        # A polite crawler identifies a way to be contacted, but that does
        # not have to be a person's mailbox. A repository URL reaches the
        # owner through GitHub issues and exposes nothing personal, so it
        # is what goes on the wire. An address passed here or set in
        # CONTACT_EMAIL is deliberately NOT used: it was being sent as a
        # header to every shop on every request, and echoed into public
        # Actions logs.
        self.contact = contact or os.environ.get("CONTACT_URL", "") or DEFAULT_CONTACT
        if "@" in self.contact:
            raise ValueError(
                "The crawler's contact must not be an email address -- it is sent "
                "to every shop in the User-Agent and printed in public run logs. "
                "Use a URL."
            )
        self.user_agent = f"{BOT_NAME} (+{self.contact})" if self.contact else BOT_NAME
        self.fresh = (os.environ.get("FRESH") == "1") if fresh is None else fresh
        self.request_count = 0

        self._robots_cache = {}
        self._crawl_delay = {}
        self._last_request_at = {}
        self._consecutive_failures = defaultdict(int)
        self._broken_hosts = set()
        self._host_locks = defaultdict(threading.Lock)

        self.skipped_disallowed = []
        self.skipped_budget = []
        self.skipped_circuit = []

    # -- robots.txt ---------------------------------------------------

    def _robots_for(self, url):
        host = _host_of(url)
        if host in self._robots_cache:
            return self._robots_cache[host]
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{host}/robots.txt"
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(robots_url)
        try:
            resp = requests.get(robots_url, timeout=TIMEOUT, headers={"User-Agent": self.user_agent})
            if resp.status_code >= 400:
                rp.parse([])
            else:
                rp.parse(resp.text.splitlines())
        except requests.RequestException:
            rp.parse([])  # unreachable robots.txt -- treat as allow-all
        self._robots_cache[host] = rp
        delay = rp.crawl_delay(self.user_agent) or rp.crawl_delay("*")
        self._crawl_delay[host] = float(delay) if delay else None
        return rp

    def _allowed(self, url):
        return self._robots_for(url).can_fetch(self.user_agent, url)

    # -- rate limiting --------------------------------------------------

    def _wait_for_host(self, host):
        min_delay = self._crawl_delay.get(host) or MIN_DELAY_SECONDS
        last = self._last_request_at.get(host)
        if last is not None:
            wait = min_delay - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        time.sleep(random.uniform(0, JITTER_MAX_SECONDS))

    # -- disk cache -------------------------------------------------------

    def _cache_path(self, url):
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{key}.json"

    def _read_cache(self, url):
        path = self._cache_path(url)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def _write_cache(self, url, entry):
        self._cache_path(url).write_text(json.dumps(entry))

    # -- failure tracking / circuit breaker ------------------------------

    # A 404 is a healthy server saying "no such page". The breaker exists
    # for hosts that are down, refusing us, or rate-limiting -- counting
    # 404s toward it meant catalogue discovery, which is mostly misses,
    # tripped the breaker after three tries and never reached the page that
    # would have worked. 401/403/407 do count: those are a host actively
    # refusing, and hammering on is the rude thing to do.
    HOST_FAILURE_STATUSES = frozenset({401, 403, 407, 429})

    @classmethod
    def _is_host_failure(cls, status_code):
        if status_code is None:
            return True          # the connection itself never succeeded
        return status_code >= 500 or status_code in cls.HOST_FAILURE_STATUSES

    def _record_failure(self, host):
        self._consecutive_failures[host] += 1
        if self._consecutive_failures[host] >= CIRCUIT_BREAKER_THRESHOLD:
            self._broken_hosts.add(host)

    def _record_success(self, host):
        self._consecutive_failures[host] = 0

    # -- the fetch itself -------------------------------------------------

    def get(self, url, params=None):
        """GET url through robots/rate-limit/backoff/cache/budget policy.

        Returns a FetchResult, or raises Disallowed / CircuitOpen /
        BudgetExceeded / UpstreamError.
        """
        full_url = _build_url(url, params)
        host = _host_of(full_url)

        if host in self._broken_hosts:
            self.skipped_circuit.append(full_url)
            raise CircuitOpen(host)

        if not self._allowed(full_url):
            self.skipped_disallowed.append(full_url)
            raise Disallowed(full_url)

        with self._host_locks[host]:
            cached = None if self.fresh else self._read_cache(full_url)
            # A challenge cached before this check existed would otherwise
            # keep being served for its full 6h. Treat it as no cache at all
            # and go ask again -- the shop may have stopped challenging us.
            if cached and looks_like_challenge(cached.get("text")):
                cached = None
            if cached and (time.time() - cached["fetched_at"]) < CACHE_TTL_SECONDS:
                return FetchResult(cached["status_code"], cached["text"], from_cache=True)

            if self.request_count >= self.max_requests:
                self.skipped_budget.append(full_url)
                raise BudgetExceeded(full_url)

            headers = {"User-Agent": self.user_agent}
            if cached:
                if cached.get("etag"):
                    headers["If-None-Match"] = cached["etag"]
                if cached.get("last_modified"):
                    headers["If-Modified-Since"] = cached["last_modified"]

            # Circuit-breaker bookkeeping counts *requests* (this get() call),
            # not individual retry attempts within it -- 3 attempts inside one
            # backoff sequence is normal politeness, not 3 host failures.
            try:
                result = self._attempt_with_retries(full_url, host, headers, cached)
            except Challenged:
                # A shop that challenges us is refusing, the same as a 403.
                # Asking again this run is the rude thing to do, so let the
                # breaker count it and skip the host after three.
                self._record_failure(host)
                raise
            except UpstreamError as e:
                if self._is_host_failure(e.status_code):
                    self._record_failure(host)
                else:
                    # The host answered, so it is up; this path just is not
                    # there. Not a strike against the host.
                    self._record_success(host)
                raise
            self._record_success(host)
            return result

    def _attempt_with_retries(self, full_url, host, headers, cached):
        last_exc = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._wait_for_host(host)
            self.request_count += 1
            try:
                resp = requests.get(full_url, headers=headers, timeout=TIMEOUT)
            except requests.RequestException as e:
                last_exc = e
                self._last_request_at[host] = time.monotonic()
                if attempt < MAX_ATTEMPTS:
                    time.sleep(BACKOFF_SCHEDULE[attempt - 1])
                    continue
                raise UpstreamError(str(e)) from e

            self._last_request_at[host] = time.monotonic()

            if resp.status_code == 304 and cached:
                cached["fetched_at"] = time.time()
                self._write_cache(full_url, cached)
                return FetchResult(cached["status_code"], cached["text"], from_cache=True)

            if resp.status_code in (429, 503):
                if attempt < MAX_ATTEMPTS:
                    retry_after = resp.headers.get("Retry-After")
                    delay = (
                        float(retry_after)
                        if retry_after and retry_after.strip().isdigit()
                        else BACKOFF_SCHEDULE[attempt - 1]
                    )
                    time.sleep(delay)
                    continue
                raise UpstreamError(
                    f"HTTP {resp.status_code} after {MAX_ATTEMPTS} attempts",
                    status_code=resp.status_code,
                )

            if resp.status_code >= 500:
                if attempt < MAX_ATTEMPTS:
                    time.sleep(BACKOFF_SCHEDULE[attempt - 1])
                    continue
                raise UpstreamError(
                    f"HTTP {resp.status_code} after {MAX_ATTEMPTS} attempts",
                    status_code=resp.status_code,
                )

            if resp.status_code >= 400:
                raise UpstreamError(f"HTTP {resp.status_code}", status_code=resp.status_code)

            # Before the cache write, never after: a cached challenge is a
            # lie with a six-hour shelf life. Not retried either -- three
            # goes at a captcha is exactly the behaviour that earns a block.
            challenge = looks_like_challenge(resp.text)
            if challenge:
                raise Challenged(f"{full_url}: {challenge}")

            entry = {
                "status_code": resp.status_code,
                "text": resp.text,
                "etag": resp.headers.get("ETag"),
                "last_modified": resp.headers.get("Last-Modified"),
                "fetched_at": time.time(),
            }
            self._write_cache(full_url, entry)
            return FetchResult(resp.status_code, resp.text, from_cache=False)

        raise UpstreamError(str(last_exc) if last_exc else f"exhausted {MAX_ATTEMPTS} attempts")
