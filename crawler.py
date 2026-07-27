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


class CircuitOpen(FetchError):
    """This host had 3+ consecutive failures this run; it's being skipped."""


class BudgetExceeded(FetchError):
    """MAX_REQUESTS_PER_RUN was reached; this URL was not fetched."""


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


def _build_url(url, params):
    if not params:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{urlencode(params)}"


def _host_of(url):
    return urlparse(url).netloc


class Crawler:
    def __init__(self, cache_dir=None, max_requests=None, contact_email=None, fresh=None):
        self.cache_dir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_requests = (
            max_requests
            if max_requests is not None
            else int(os.environ.get("MAX_REQUESTS_PER_RUN", DEFAULT_MAX_REQUESTS_PER_RUN))
        )
        self.contact_email = (
            contact_email if contact_email is not None else os.environ.get("CONTACT_EMAIL", "")
        )
        self.user_agent = (
            f"WineTrackerBot/1.0 (+{self.contact_email})"
            if self.contact_email
            else "WineTrackerBot/1.0"
        )
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
            except UpstreamError:
                self._record_failure(host)
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
