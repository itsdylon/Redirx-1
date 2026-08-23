import { randomUUID } from 'node:crypto';
import { McpError } from '@modelcontextprotocol/sdk/types.js';

/**
 * MPP (Machine Payments Protocol, mpp.dev — co-authored by Stripe and
 * Tempo, x402-interoperable). Verified against the live spec
 * (mpp.dev/protocol/{challenges,transports/mcp}, mpp.dev/payment-methods/stripe,
 * mpp.dev/intents/charge) before writing this, per the brief's instruction not
 * to build from a secondhand summary. Two things that summary got right and
 * are worth restating because they're load-bearing here:
 *
 *  - The JSON-RPC error code is -32042, "Payment Required" — the MCP mapping
 *    of HTTP 402. `error.data.challenges` carries one or more Challenge
 *    objects.
 *  - `opaque` is base64url-encoded JCS JSON on the Challenge, and "clients
 *    must echo `opaque` back unchanged when they submit a Credential." That
 *    is exactly the "short-lived opaque resume token" agentic-pivot.md §3.5
 *    describes — encoding the pending export call (migration id, format,
 *    url_format, min_confidence) into it means the retry is fully
 *    reconstructable from the opaque alone, not just from tool arguments an
 *    agent might not repeat verbatim.
 *
 * One place this deliberately deviates from the documented Stripe method flow,
 * flagged rather than silently done: MPP's `stripe` payment method is built
 * for an agent that can itself collect a payment method (Stripe Elements) and
 * submit a Credential containing an SPT (Shared Payment Token) — genuine
 * autonomous machine payment. A chat agent (Claude included) cannot run
 * Stripe.js. RedirX's actual settlement is human-in-the-loop: the existing
 * per-project Stripe Checkout flow the web app already has (pricing_service.py
 * / stripe_service.create_project_checkout_session), reached via the
 * `upgrade_url` v1's 402 already returns. So `methodDetails.checkoutUrl` is an
 * extension beyond the documented SPT shape, not a documented field — the spec
 * page for the Stripe method doesn't mention a hosted-checkout variant, and I
 * could not find one in mpp.dev's docs (see the research trail in this
 * project's PR/commit history). It fits how `methodDetails` is described to
 * work (payment methods extend the base request schema with method-specific
 * fields) and keeps the envelope wire-compatible with MPP tooling that reads
 * `challenges[].id/realm/method/intent/opaque`, even though nothing consumes
 * `checkoutUrl` except this gateway's own retry-by-polling-v1 logic.
 *
 * The other deliberate simplification: our retry does not actually depend on
 * the client echoing anything. v1's export endpoint (v1_routes.py,
 * export_migration) re-derives paid/unpaid fresh on every call via
 * entitlement_service.check_export, keyed only on migration_id + the caller's
 * own API key — so "call export again" already works correctly whether or
 * not the credential/opaque round-trips through a particular client's `_meta`
 * handling. The opaque/credential machinery below exists for MPP protocol
 * correctness and explicit agent-facing signaling, not because the gate
 * requires it. "The server is the sole authority on payment state; the agent
 * only relays" — see how export.ts uses (or ignores) the credential.
 */

export const PAYMENT_REQUIRED_CODE = -32042;

export const CREDENTIAL_META_KEY = 'org.paymentauth/credential';
export const RECEIPT_META_KEY = 'org.paymentauth/receipt';

export interface OpaquePayload {
  migrationId: string;
  format: string;
  urlFormat: 'paths' | 'full';
  minConfidence: number;
}

function base64urlEncodeJson(value: unknown): string {
  // JCS (RFC 8785) proper would sort object keys recursively; a single
  // flat, hand-authored object (OpaquePayload) is already in a stable key
  // order, so JSON.stringify is canonical enough here without pulling in a
  // JCS library for one call site. Don't reuse this helper for anything with
  // nested objects or non-deterministic key order.
  return Buffer.from(JSON.stringify(value), 'utf8').toString('base64url');
}

function base64urlDecodeJson<T>(value: string): T {
  return JSON.parse(Buffer.from(value, 'base64url').toString('utf8')) as T;
}

export function encodeOpaque(payload: OpaquePayload): string {
  return base64urlEncodeJson(payload);
}

export function decodeOpaque(opaque: string): OpaquePayload | null {
  try {
    const payload = base64urlDecodeJson<Partial<OpaquePayload>>(opaque);
    if (!payload.migrationId || !payload.format || !payload.urlFormat) return null;
    return {
      migrationId: payload.migrationId,
      format: payload.format,
      urlFormat: payload.urlFormat,
      minConfidence: payload.minConfidence ?? 0,
    };
  } catch {
    return null;
  }
}

export interface StripeChallengeInput {
  migrationId: string;
  format: string;
  urlFormat: 'paths' | 'full';
  minConfidence: number;
  realm: string;
  checkoutUrl: string;
  description: string;
}

export interface Challenge {
  id: string;
  realm: string;
  method: string;
  intent: string;
  request: string;
  opaque: string;
  description: string;
  methodDetails: { checkoutUrl: string };
}

/**
 * Builds the single Challenge our `export` tool ever issues. `intent:
 * "session"` rather than `"charge"`: the charge intent's request schema
 * requires a known `amount` up front, and RedirX's export price is graduated
 * per-page (pricing_service.calculate_graduated_price) — not known until the
 * web checkout page computes a quote, which is exactly the case mpp.dev
 * describes the session intent for ("metered billing ... where the total
 * cost isn't known upfront"). I could not find session intent's full request
 * schema in mpp.dev's docs to confirm this is the intended shape for a
 * single unknown-until-quoted charge specifically (as opposed to its more
 * clearly-documented streaming/metered use case) — flagged here rather than
 * asserted with false confidence; revisit if mpp.dev publishes a fuller
 * session-intent spec.
 */
export function buildExportPaymentChallenge(input: StripeChallengeInput): Challenge {
  const opaque = encodeOpaque({
    migrationId: input.migrationId,
    format: input.format,
    urlFormat: input.urlFormat,
    minConfidence: input.minConfidence,
  });

  const request = base64urlEncodeJson({
    currency: 'usd',
    description: input.description,
  });

  return {
    id: `rdx_ch_${randomUUID()}`,
    realm: input.realm,
    method: 'stripe',
    intent: 'session',
    request,
    opaque,
    description: input.description,
    methodDetails: { checkoutUrl: input.checkoutUrl },
  };
}

/** Throws the JSON-RPC -32042 error a tool handler's exception becomes. */
export function throwPaymentRequired(challenge: Challenge): never {
  throw new McpError(PAYMENT_REQUIRED_CODE, 'Payment Required', {
    httpStatus: 402,
    challenges: [challenge],
  });
}

/**
 * Reads whatever Credential the client sent back on a retry, if any. Never
 * required — see the module docstring on why the gate doesn't depend on it.
 * Returns null for a client that didn't send one, which is the common case
 * for "the agent just called the tool again."
 */
export function extractCredential(meta: Record<string, unknown> | undefined): unknown {
  return meta?.[CREDENTIAL_META_KEY] ?? null;
}

/** Attaches a Receipt to a successful tool result's `_meta`, MPP-shape. */
export function attachReceipt(
  meta: Record<string, unknown> | undefined,
  receipt: Record<string, unknown>,
): Record<string, unknown> {
  return { ...(meta ?? {}), [RECEIPT_META_KEY]: receipt };
}
