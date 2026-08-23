-- 032: resume tokens for the MCP `export` tool's pay-and-retry flow
--
-- See docs/architecture/agentic-pivot.md §3.5. An agent calls `export`,
-- quota fails, we create a Stripe Checkout session and mint a short-lived
-- token. The human pays in a browser (a real financial action; the agent
-- cannot and should not complete it). Stripe's webhook flips this row to
-- 'paid'. The agent retries `export`, echoing the token back — this is the
-- MPP `opaque` field's job (docs/architecture/agentic-pivot.md's own framing:
-- reuse `opaque` as the resume token rather than inventing a parallel one) —
-- and gets the artifact from the already-completed run.
--
-- Only the hash is stored, same reasoning as api_keys: this table is queried
-- by an untrusted, unattended caller (an agent), so a leaked row must not
-- itself be a usable credential.

CREATE TABLE IF NOT EXISTS export_resume_tokens (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  token_hash TEXT NOT NULL UNIQUE,
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  session_id UUID NOT NULL REFERENCES migration_sessions(id) ON DELETE CASCADE,
  stripe_checkout_session_id TEXT NOT NULL UNIQUE,
  amount_cents INTEGER NOT NULL,
  currency TEXT NOT NULL DEFAULT 'usd',
  -- pending -> paid (webhook) -> consumed (first successful export fetch).
  -- A separate 'consumed' state (rather than deleting the row on use) keeps
  -- the token idempotently retryable: an agent retrying the exact same
  -- export call after already receiving the file gets the file again, not
  -- an error, matching the existing unlock-status UX.
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'paid', 'consumed', 'expired')),
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_export_resume_tokens_checkout_session
  ON export_resume_tokens (stripe_checkout_session_id);

COMMENT ON TABLE export_resume_tokens IS
  'Opaque, short-lived tokens backing the export tool''s 402-and-resume flow. token_hash is looked up, never the plaintext; the plaintext is the MPP opaque value handed to the agent.';
