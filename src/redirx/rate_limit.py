"""
Global per-host crawl politeness.

Concurrency limits were per-worker asyncio semaphores, so N workers each doing
a "polite" rate produced N times that rate at the origin — which is why real
sites already 429 us. This limiter is shared state in Postgres, so the rate is
global across every worker and the API.

Postgres rather than Redis deliberately: the limiter caps at ~1 req/s per host,
so sub-millisecond op latency buys nothing, and a second stateful service would
be a second source of truth to monitor. Revisit only above a few hundred
limiter ops/sec.

State lives in host_buckets and is manipulated only through the functions in
migration 025, each of which is a single atomic statement (lazy refill from
elapsed wall-clock time, so there is no background job and no drift).

Transport is direct async Postgres rather than PostgREST: lower latency, and
it keeps the limiter functions off the public REST surface entirely.

Connection note: point CRAWL_LIMITER_DATABASE_URL at the Supabase transaction
pooler (port 6543) so scaling workers cannot exhaust connections. Transaction
mode is safe here because these calls rely only on ON CONFLICT row locks,
which are transaction-scoped — the limiter never takes a session-scoped
advisory lock (which silently does nothing in transaction mode).
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

# Imported for its side effect of loading .env, so the DSN and tunables below
# resolve the same way whether we run under Flask, the worker, or a script.
from .config import Config  # noqa: F401

logger = logging.getLogger(__name__)

# Bucket depth. Small burst allowance so a handful of sitemap fetches go out
# back-to-back without the origin seeing sustained parallelism.
DEFAULT_CAPACITY = float(os.getenv("CRAWL_BURST_CAPACITY", "3"))
# Starting steady-state rate, per host. AIMD adapts from here.
DEFAULT_RATE = float(os.getenv("CRAWL_DEFAULT_RATE", "1"))
MAX_RATE = float(os.getenv("CRAWL_MAX_RATE", "4"))
RATE_INCREMENT = float(os.getenv("CRAWL_RATE_INCREMENT", "0.1"))
BREAKER_THRESHOLD = int(os.getenv("CRAWL_BREAKER_THRESHOLD", "3"))
BREAKER_COOLDOWN = float(os.getenv("CRAWL_BREAKER_COOLDOWN", "900"))
MIN_RATE = float(os.getenv("CRAWL_MIN_RATE", "0.1"))
# Ceiling on how long a single acquire will wait before giving up on a host.
MAX_ACQUIRE_WAIT = float(os.getenv("CRAWL_MAX_ACQUIRE_WAIT", "30"))

# Statuses that mean "you are going too fast" or "you are not welcome".
BACKOFF_STATUSES = (403, 429, 503)


class CircuitOpen(Exception):
    """Host is in breaker cooldown; stop fetching it for this job."""

    def __init__(self, host: str, retry_after: float):
        super().__init__(f"circuit open for {host} ({retry_after:.0f}s remaining)")
        self.host = host
        self.retry_after = retry_after


@dataclass
class AcquireResult:
    allowed: bool
    retry_after: float
    reason: str


def host_of(url: str) -> str:
    """Bucket key: registrable-ish host, www-normalized so www and bare share a bucket."""
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def parse_retry_after(value: str | None) -> float | None:
    """Retry-After is either delta-seconds or an HTTP-date. Honor both."""
    if not value:
        return None
    raw = value.strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, IndexError):
        return None


def parse_crawl_delay(robots_txt: str, user_agent_token: str = "redirxbot") -> float | None:
    """
    Crawl-delay for our UA, falling back to the wildcard group. Returns seconds.
    """
    ua_delay: float | None = None
    wildcard_delay: float | None = None
    current: str | None = None

    for line in (robots_txt or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field = field.strip().lower()
        value = value.strip()

        if field == "user-agent":
            current = value.lower()
        elif field == "crawl-delay" and current is not None:
            try:
                delay = float(value)
            except ValueError:
                continue
            if current == "*":
                wildcard_delay = delay
            elif user_agent_token in current:
                ua_delay = delay

    return ua_delay if ua_delay is not None else wildcard_delay


def limiter_dsn() -> Optional[str]:
    """
    Prefer a pooler URL dedicated to the limiter; fall back to DATABASE_URL.
    Returns None when no database is configured (limiter then no-ops).
    """
    return os.getenv("CRAWL_LIMITER_DATABASE_URL") or os.getenv("DATABASE_URL") or None


_pool = None
_pool_lock = asyncio.Lock()


async def _get_pool():
    """Lazily open one shared async pool per process."""
    global _pool
    if _pool is not None:
        return _pool
    async with _pool_lock:
        if _pool is None:
            dsn = limiter_dsn()
            if not dsn:
                return None
            from psycopg_pool import AsyncConnectionPool

            pool = AsyncConnectionPool(
                dsn,
                min_size=0,
                max_size=int(os.getenv("CRAWL_LIMITER_POOL_SIZE", "4")),
                open=False,
                timeout=10,
                # Transaction-pooler friendly: no server-side prepared statements.
                kwargs={"prepare_threshold": None},
            )
            await pool.open(wait=True, timeout=10)
            _pool = pool
    return _pool


async def close_pool() -> None:
    """Close the shared pool (tests, worker shutdown)."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


class HostRateLimiter:
    """Async facade over the Postgres token-bucket functions."""

    def __init__(self, dsn: Optional[str] = None, enabled: bool | None = None):
        self._dsn = dsn
        if enabled is None:
            enabled = os.getenv("CRAWL_POLITENESS_ENABLED", "true").strip().lower() not in (
                "0", "false", "no", "off",
            )
        self.enabled = enabled
        # Hosts whose breaker tripped during this job — skip them outright.
        self._tripped: set[str] = set()
        # Hosts we've already warned about, so a limiter outage logs once per
        # host instead of once per fetch.
        self._warned: set[str] = set()

    async def _call(self, sql: str, params: tuple):
        pool = await _get_pool()
        if pool is None:
            raise RuntimeError("no limiter database configured")
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                if cur.description is None:
                    return []
                columns = [d.name for d in cur.description]
                return [dict(zip(columns, row)) for row in await cur.fetchall()]

    async def try_acquire(self, host: str) -> AcquireResult:
        rows = await self._call(
            "SELECT allowed, retry_after, reason FROM try_consume_host_token("
            "%s::text, %s::double precision, %s::double precision)",
            (host, DEFAULT_CAPACITY, DEFAULT_RATE),
        )
        row = rows[0] if rows else {}
        return AcquireResult(
            allowed=bool(row.get("allowed")),
            retry_after=float(row.get("retry_after") or 0.0),
            reason=str(row.get("reason") or "unknown"),
        )

    async def acquire(self, url: str) -> None:
        """
        Wait until a token is available for this URL's host.

        Raises:
            CircuitOpen: breaker is open for the host, or we waited too long.
        """
        if not self.enabled:
            return
        host = host_of(url)
        if not host:
            return
        if host in self._tripped:
            raise CircuitOpen(host, BREAKER_COOLDOWN)

        waited = 0.0
        while True:
            try:
                result = await self.try_acquire(host)
            except Exception as exc:
                # Never let limiter infrastructure failure stop a crawl; a brief
                # unthrottled window is preferable to a dead pipeline.
                if host not in self._warned:
                    self._warned.add(host)
                    logger.warning(
                        "rate limiter unavailable for %s (failing open): %s",
                        host,
                        str(exc).splitlines()[0],
                    )
                return

            if result.allowed:
                return
            if result.reason == "circuit_open":
                self._tripped.add(host)
                raise CircuitOpen(host, result.retry_after)

            delay = min(max(result.retry_after, 0.05), 5.0)
            if waited + delay > MAX_ACQUIRE_WAIT:
                raise CircuitOpen(host, result.retry_after)
            await asyncio.sleep(delay)
            waited += delay

    async def record_success(self, url: str) -> None:
        if not self.enabled:
            return
        host = host_of(url)
        if not host:
            return
        try:
            await self._call(
                "SELECT record_host_success(%s::text, %s::double precision, %s::double precision)",
                (host, RATE_INCREMENT, MAX_RATE),
            )
        except Exception as exc:
            logger.debug("record_host_success failed for %s: %s", host, exc)

    async def record_failure(self, url: str, retry_after: float | None = None) -> bool:
        """
        Halve the host's rate and count toward the breaker.

        Returns True if the breaker tripped.
        """
        if not self.enabled:
            return False
        host = host_of(url)
        if not host:
            return False
        try:
            rows = await self._call(
                "SELECT circuit_open, new_rate FROM record_host_failure("
                "%s::text, %s::double precision, %s::integer, "
                "%s::double precision, %s::double precision)",
                (host, retry_after, BREAKER_THRESHOLD, BREAKER_COOLDOWN, MIN_RATE),
            )
        except Exception as exc:
            logger.debug("record_host_failure failed for %s: %s", host, exc)
            return False

        row = rows[0] if rows else {}
        tripped = bool(row.get("circuit_open"))
        if tripped:
            self._tripped.add(host)
            logger.warning("circuit breaker tripped for %s", host)
        return tripped

    async def apply_crawl_delay(self, url: str, robots_txt: str) -> float | None:
        """Pin the host's rate from robots.txt Crawl-delay, if present."""
        if not self.enabled:
            return None
        delay = parse_crawl_delay(robots_txt)
        if not delay or delay <= 0:
            return None
        host = host_of(url)
        if not host:
            return None
        try:
            await self._call("SELECT set_host_crawl_delay(%s::text, %s::double precision)", (host, delay))
        except Exception as exc:
            logger.debug("set_host_crawl_delay failed for %s: %s", host, exc)
        return delay

    def note_response(self, status: int) -> bool:
        """Whether a status code should count as a politeness failure."""
        return status in BACKOFF_STATUSES


# The limiter is a job-scoped dependency threaded through deep call stacks
# (discovery strategies, scraper fan-out). A ContextVar propagates correctly
# across asyncio tasks without churning every intermediate signature, and
# defaults to None so existing call paths and tests are unaffected.
import contextvars  # noqa: E402

_current_limiter: contextvars.ContextVar[Optional["HostRateLimiter"]] = contextvars.ContextVar(
    "redirx_host_limiter", default=None
)


def get_limiter() -> Optional["HostRateLimiter"]:
    return _current_limiter.get()


def set_limiter(limiter: Optional["HostRateLimiter"]):
    """Install the limiter for the current async context. Returns the token."""
    return _current_limiter.set(limiter)
