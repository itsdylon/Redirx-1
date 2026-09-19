import type { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import * as z from 'zod';
import type { PivotEnvelope, RedirxApiError } from '../backend/redirxClient.js';
import { clientForCall, type ToolExtra } from './context.js';

const UUID = z.string().uuid();
const IDEMPOTENCY = z.string().min(1).max(200).regex(/^[^\x00-\x1f\x7f]+$/);
const PAGE = z.number().int().min(1).max(500).default(100);
const FORMATS = ['apache', 'nginx', 'wordpress', 'vercel', 'cloudflare', 'shopify', 'csv', 'json'] as const;
const ACTIONS = ['accept_repair', 'set_target', 'approve', 'reject', 'defer', 'intentional_removal'] as const;

type ToolResult = { content: Array<{ type: 'text'; text: string }>; isError?: boolean };

function localError(code: string, message: string, nextAction = 'none'): PivotEnvelope {
  return {
    contract_version: '1.0.0', migration_id: null, operation_id: null,
    status: 'failed', next_action: nextAction, data: {},
    error: { code, message, retryable: false, next_action: nextAction },
  };
}

function stableEnvelope(envelope: PivotEnvelope): string {
  // Select and order only the published envelope fields. Backend-owned `data`
  // may grow without making the outer MCP response shape unstable.
  return JSON.stringify({
    contract_version: envelope.contract_version,
    migration_id: envelope.migration_id ?? null,
    operation_id: envelope.operation_id ?? null,
    status: envelope.status,
    next_action: envelope.next_action,
    ...(envelope.retry_after_seconds === undefined ? {} : { retry_after_seconds: envelope.retry_after_seconds }),
    ...(envelope.progress === undefined ? {} : { progress: envelope.progress }),
    data: envelope.data,
    error: envelope.error,
  });
}

function failure(error: RedirxApiError): ToolResult {
  // Do not reflect arbitrary error response bodies: the API contract's error
  // fields are the only safe cross-boundary error authority.
  const body = error.body;
  const nested = body.error;
  const candidate = nested && typeof nested === 'object' ? nested as Record<string, unknown> : body;
  const next = typeof candidate.next_action === 'string' ? candidate.next_action : 'none';
  const retryable = candidate.retryable === true;
  return {
    isError: true,
    content: [{ type: 'text', text: stableEnvelope({
      ...localError(error.code, error.message, next),
      error: { code: error.code, message: error.message, retryable, next_action: next },
    }) }],
  };
}

async function call(
  extra: ToolExtra, method: 'GET' | 'POST' | 'PATCH', path: string, body?: Record<string, unknown>,
): Promise<ToolResult> {
  try {
    const { client } = await clientForCall(extra);
    const result = await client.pivot(method, path, body);
    if (!result.ok) return failure(result.error);
    const envelope = result.data;
    if (!envelope || typeof envelope !== 'object' || typeof envelope.data !== 'object'
        || !envelope.contract_version || !envelope.status || !envelope.next_action) {
      return { isError: true, content: [{ type: 'text', text: stableEnvelope(localError('internal_error', 'Invalid backend response.')) }] };
    }
    return { content: [{ type: 'text', text: stableEnvelope(envelope) }] };
  } catch {
    return { isError: true, content: [{ type: 'text', text: stableEnvelope(localError('internal_error', 'Gateway request failed.')) }] };
  }
}

function query(path: string, values: Record<string, string | number | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) if (value !== undefined) params.set(key, String(value));
  const suffix = params.toString();
  return suffix ? `${path}?${suffix}` : path;
}

/** The opt-in v2 surface. Keep this list in lockstep with pivot-v1.json. */
export function registerPivotTools(server: McpServer): void {
  server.registerTool('plan_migration', {
    title: 'Plan a migration', description: 'Create an owned old-site/new-site migration plan. This starts no paid work.',
    inputSchema: { old_site: z.string().min(1), new_site: z.string().min(1), name: z.string().max(200).optional(), aliases: z.array(z.string()).max(20).optional(), gsc_property: z.string().max(2048).optional(), idempotency_key: IDEMPOTENCY },
    annotations: { readOnlyHint: false, idempotentHint: true, openWorldHint: true },
  }, (args, extra) => call(extra as ToolExtra, 'POST', '/migrations', args));

  server.registerTool('run_migration', {
    title: 'Run a migration', description: 'Reserve and dispatch an entitled migration run using immutable inventories. Payment-required states are recoverable.',
    inputSchema: { migration_id: UUID, old_inventory_id: UUID, new_inventory_id: UUID, quote_id: UUID.optional(), grant_id: UUID.optional(), rerun_of: UUID.optional(), idempotency_key: IDEMPOTENCY },
    annotations: { readOnlyHint: false, idempotentHint: true, openWorldHint: true },
  }, ({ migration_id, old_inventory_id, new_inventory_id, ...args }, extra) => call(extra as ToolExtra, 'POST', `/migrations/${encodeURIComponent(migration_id)}/runs`, { inventory_ids: { old: old_inventory_id, new: new_inventory_id }, ...args }));

  server.registerTool('get_migration', {
    title: 'Get migration status', description: 'Read an owned migration summary and its next recoverable action.',
    inputSchema: { migration_id: UUID, run_id: UUID.optional(), operation_id: UUID.optional() },
    annotations: { readOnlyHint: true, idempotentHint: true },
  }, ({ migration_id, run_id, operation_id }, extra) => call(extra as ToolExtra, 'GET', query(`/migrations/${encodeURIComponent(migration_id)}`, { run_id, operation_id })));

  server.registerTool('list_matches', {
    title: 'List migration matches', description: 'Read owned match exceptions in stable traffic order. Traffic absence is distinct from observed zero.',
    inputSchema: { migration_id: UUID, run_id: UUID, filter: z.enum(['all', 'needs_review', 'unmatched', 'approved', 'rejected']).default('all'), cursor: z.string().max(4096).optional(), limit: PAGE },
    annotations: { readOnlyHint: true, idempotentHint: true },
  }, ({ migration_id, run_id, filter, cursor, limit }, extra) => call(extra as ToolExtra, 'GET', query(`/migrations/${encodeURIComponent(migration_id)}/runs/${encodeURIComponent(run_id)}/matches`, { filter, cursor, limit })));

  server.registerTool('resolve_matches', {
    title: 'Resolve match exceptions', description: 'Apply audited, optimistic-concurrency match decisions. Ambiguous matches are never blanket-approved.',
    inputSchema: { migration_id: UUID, run_id: UUID, idempotency_key: IDEMPOTENCY, decisions: z.array(z.object({ mapping_id: UUID, expected_revision: z.number().int().min(0), action: z.enum(ACTIONS), target_url: z.string().min(1).max(8192).optional(), rationale: z.string().max(2000).optional() })).min(1).max(100) },
    annotations: { readOnlyHint: false, idempotentHint: true },
  }, ({ migration_id, run_id, ...args }, extra) => call(extra as ToolExtra, 'PATCH', `/migrations/${encodeURIComponent(migration_id)}/runs/${encodeURIComponent(run_id)}/matches`, args));

  server.registerTool('export_redirects', {
    title: 'Export redirects', description: 'Create an immutable redirect artifact. Large downloads are returned only as backend-authorized artifact resources; this gateway never follows arbitrary URLs.',
    inputSchema: { migration_id: UUID, run_id: UUID, format: z.enum(FORMATS), revision: z.number().int().min(0), allow_partial: z.boolean().default(false), idempotency_key: IDEMPOTENCY },
    annotations: { readOnlyHint: false, idempotentHint: true },
  }, ({ migration_id, ...args }, extra) => call(extra as ToolExtra, 'POST', `/migrations/${encodeURIComponent(migration_id)}/artifacts`, args));

  server.registerTool('verify_redirects', {
    title: 'Verify redirects', description: 'Start or resume a persisted redirect verification. Deployment confirmation is advisory and server-validated.',
    inputSchema: { migration_id: UUID, artifact_id: UUID, deployment_confirmation: z.boolean().optional(), live_origins: z.array(z.string()).max(20).optional(), idempotency_key: IDEMPOTENCY },
    annotations: { readOnlyHint: false, idempotentHint: true, openWorldHint: true },
  }, ({ migration_id, ...args }, extra) => call(extra as ToolExtra, 'POST', `/migrations/${encodeURIComponent(migration_id)}/verifications`, args));

  server.registerTool('manage_monitoring', {
    title: 'Manage monitoring', description: 'Start, pause, resume, or cancel monitoring for an owned deployed artifact. Renewal consent remains explicit.',
    inputSchema: { migration_id: UUID, action: z.enum(['start', 'pause', 'resume', 'cancel']), artifact_id: UUID.optional(), alert_email: z.string().email().optional(), idempotency_key: IDEMPOTENCY },
    annotations: { readOnlyHint: false, idempotentHint: true },
  }, ({ migration_id, ...args }, extra) => call(extra as ToolExtra, 'POST', `/migrations/${encodeURIComponent(migration_id)}/monitoring`, args));

  server.registerTool('get_monitoring_status', {
    title: 'Get monitoring status', description: 'Read monitoring coverage and issues; unchecked is not reported as healthy.',
    inputSchema: { migration_id: UUID, cursor: z.string().max(4096).optional(), limit: PAGE },
    annotations: { readOnlyHint: true, idempotentHint: true },
  }, ({ migration_id, cursor, limit }, extra) => call(extra as ToolExtra, 'GET', query(`/migrations/${encodeURIComponent(migration_id)}/monitoring`, { cursor, limit })));

  server.registerTool('get_monitoring_fixes', {
    title: 'Get monitoring fixes', description: 'Read evidence and ready correction artifacts. Generating a fix never marks it deployed.',
    inputSchema: { migration_id: UUID, cursor: z.string().max(4096).optional(), limit: PAGE },
    annotations: { readOnlyHint: true, idempotentHint: true },
  }, ({ migration_id, cursor, limit }, extra) => call(extra as ToolExtra, 'GET', query(`/migrations/${encodeURIComponent(migration_id)}/monitoring/fixes`, { cursor, limit })));

  server.registerTool('connect_search_console', {
    title: 'Connect Search Console', description: 'Begin consent, inspect properties, sync approved data, or disconnect. Never provide Google credentials to this tool.',
    inputSchema: { action: z.enum(['connect', 'status', 'properties', 'disconnect', 'sync']), migration_id: UUID.optional(), property: z.string().max(2048).optional(), window_days: z.number().int().min(1).max(540).optional(), idempotency_key: IDEMPOTENCY.optional() },
    annotations: { readOnlyHint: false, idempotentHint: true, openWorldHint: true },
  }, (args, extra) => {
    if (['connect', 'disconnect', 'sync'].includes(args.action) && !args.idempotency_key) {
      return Promise.resolve({ isError: true, content: [{ type: 'text' as const, text: stableEnvelope(localError('invalid_input', 'idempotency_key is required for this action.')) }] });
    }
    return call(extra as ToolExtra, 'POST', '/connections/search-console/actions', args);
  });
}
