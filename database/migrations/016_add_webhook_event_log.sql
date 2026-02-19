-- Migration 016: Add webhook event log for idempotency
-- Prevents double-processing of Stripe webhook events on retries.

CREATE TABLE IF NOT EXISTS stripe_webhook_events (
    stripe_event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Index for cleanup queries (delete events older than 30 days)
CREATE INDEX IF NOT EXISTS idx_webhook_events_processed_at
    ON stripe_webhook_events (processed_at);
