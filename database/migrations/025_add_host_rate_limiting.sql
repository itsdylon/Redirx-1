-- Migration 025: Global per-host crawl politeness (token bucket + circuit breaker)
--
-- Concurrency was per-worker asyncio semaphores only, so N workers each doing
-- a "polite" rate produced N times that rate at the origin. Real sites already
-- 429 us (measured: allbirds.com refused 10 of 12 requests at 12-concurrent).
--
-- Postgres rather than Redis on purpose: the limiter deliberately caps at
-- ~1 req/s per host, so sub-millisecond op latency buys nothing, and a second
-- stateful service would be a second source of truth to monitor. Revisit only
-- if limiter throughput exceeds a few hundred ops/sec.

CREATE TABLE IF NOT EXISTS host_buckets (
  host                 TEXT PRIMARY KEY,
  tokens               DOUBLE PRECISION NOT NULL,
  -- Adaptive (AIMD): additive increase on clean responses, multiplicative
  -- decrease on 429/503. Persisted so learned politeness survives restarts.
  refill_rate          DOUBLE PRECISION NOT NULL,
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  -- Circuit breaker: set on repeated 403/429/503 so we stop grinding into a ban.
  blocked_until        TIMESTAMPTZ
);

-- Every acquire writes a dead tuple; without this the table bloats fast.
ALTER TABLE host_buckets SET (
  autovacuum_vacuum_scale_factor = 0.01,
  autovacuum_vacuum_threshold = 50
);

ALTER TABLE host_buckets ENABLE ROW LEVEL SECURITY;


-- Atomically refill (lazily, from elapsed time) and consume one token.
-- Atomic under concurrency because ON CONFLICT DO UPDATE takes a row lock;
-- per-host contention is exactly the serialization we want.
--
-- Returns: allowed, retry_after (seconds to wait), reason
CREATE OR REPLACE FUNCTION try_consume_host_token(
  p_host TEXT,
  p_capacity DOUBLE PRECISION DEFAULT 3,
  p_default_rate DOUBLE PRECISION DEFAULT 1
)
RETURNS TABLE (allowed BOOLEAN, retry_after DOUBLE PRECISION, reason TEXT)
LANGUAGE plpgsql
AS $$
DECLARE
  v_tokens DOUBLE PRECISION;
  v_blocked_until TIMESTAMPTZ;
  v_rate DOUBLE PRECISION;
BEGIN
  -- Circuit breaker check first: an open breaker short-circuits everything.
  SELECT hb.blocked_until, hb.refill_rate
    INTO v_blocked_until, v_rate
    FROM host_buckets hb WHERE hb.host = p_host;

  IF v_blocked_until IS NOT NULL AND v_blocked_until > NOW() THEN
    RETURN QUERY SELECT
      FALSE,
      EXTRACT(EPOCH FROM (v_blocked_until - NOW()))::DOUBLE PRECISION,
      'circuit_open'::TEXT;
    RETURN;
  END IF;

  INSERT INTO host_buckets (host, tokens, refill_rate, updated_at)
  VALUES (p_host, p_capacity - 1, p_default_rate, NOW())
  ON CONFLICT (host) DO UPDATE SET
    tokens = LEAST(
               p_capacity,
               host_buckets.tokens
                 + EXTRACT(EPOCH FROM (NOW() - host_buckets.updated_at))
                   * host_buckets.refill_rate
             ) - 1,
    updated_at = NOW(),
    blocked_until = NULL
  WHERE LEAST(
          p_capacity,
          host_buckets.tokens
            + EXTRACT(EPOCH FROM (NOW() - host_buckets.updated_at))
              * host_buckets.refill_rate
        ) >= 1
  RETURNING host_buckets.tokens INTO v_tokens;

  IF v_tokens IS NULL THEN
    -- Zero rows updated: bucket empty. Time to earn one token at current rate.
    RETURN QUERY SELECT
      FALSE,
      GREATEST(0.05, 1.0 / COALESCE(NULLIF(v_rate, 0), p_default_rate))::DOUBLE PRECISION,
      'rate_limited'::TEXT;
    RETURN;
  END IF;

  RETURN QUERY SELECT TRUE, 0::DOUBLE PRECISION, 'ok'::TEXT;
END;
$$;


-- Additive increase on a clean response, capped. Also clears failure state.
CREATE OR REPLACE FUNCTION record_host_success(
  p_host TEXT,
  p_increment DOUBLE PRECISION DEFAULT 0.1,
  p_max_rate DOUBLE PRECISION DEFAULT 4
)
RETURNS VOID
LANGUAGE SQL
AS $$
  UPDATE host_buckets SET
    refill_rate = LEAST(p_max_rate, refill_rate + p_increment),
    consecutive_failures = 0,
    blocked_until = NULL
  WHERE host = p_host;
$$;


-- Multiplicative decrease on 429/503/403, honoring Retry-After when supplied.
-- Trips the circuit breaker at p_breaker_threshold consecutive failures.
CREATE OR REPLACE FUNCTION record_host_failure(
  p_host TEXT,
  p_retry_after DOUBLE PRECISION DEFAULT NULL,
  p_breaker_threshold INTEGER DEFAULT 3,
  p_breaker_cooldown DOUBLE PRECISION DEFAULT 900,
  p_min_rate DOUBLE PRECISION DEFAULT 0.1
)
RETURNS TABLE (circuit_open BOOLEAN, new_rate DOUBLE PRECISION)
LANGUAGE plpgsql
AS $$
DECLARE
  v_failures INTEGER;
  v_rate DOUBLE PRECISION;
  v_open BOOLEAN := FALSE;
BEGIN
  INSERT INTO host_buckets (host, tokens, refill_rate, updated_at, consecutive_failures)
  VALUES (p_host, 0, p_min_rate, NOW(), 1)
  ON CONFLICT (host) DO UPDATE SET
    refill_rate = GREATEST(p_min_rate, host_buckets.refill_rate / 2),
    consecutive_failures = host_buckets.consecutive_failures + 1,
    tokens = 0,
    updated_at = NOW()
  RETURNING host_buckets.consecutive_failures, host_buckets.refill_rate
    INTO v_failures, v_rate;

  IF v_failures >= p_breaker_threshold THEN
    v_open := TRUE;
    UPDATE host_buckets SET
      blocked_until = NOW() + MAKE_INTERVAL(
        secs => GREATEST(COALESCE(p_retry_after, 0), p_breaker_cooldown)
      )
    WHERE host = p_host;
  ELSIF p_retry_after IS NOT NULL AND p_retry_after > 0 THEN
    -- Honor Retry-After literally even before the breaker trips.
    UPDATE host_buckets SET
      blocked_until = NOW() + MAKE_INTERVAL(secs => p_retry_after)
    WHERE host = p_host;
  END IF;

  RETURN QUERY SELECT v_open, v_rate;
END;
$$;


-- Pin the crawl rate for a host, e.g. from robots.txt Crawl-delay.
CREATE OR REPLACE FUNCTION set_host_crawl_delay(
  p_host TEXT,
  p_delay_seconds DOUBLE PRECISION
)
RETURNS VOID
LANGUAGE SQL
AS $$
  INSERT INTO host_buckets (host, tokens, refill_rate, updated_at)
  VALUES (p_host, 1, 1.0 / GREATEST(p_delay_seconds, 0.05), NOW())
  ON CONFLICT (host) DO UPDATE SET
    refill_rate = LEAST(host_buckets.refill_rate, 1.0 / GREATEST(p_delay_seconds, 0.05));
$$;
