import type { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import * as z from 'zod';
import { config } from '../config.js';
import { buildExportPaymentChallenge, throwPaymentRequired } from '../payments/mpp.js';
import { clientForCall, type ToolExtra } from './context.js';

/**
 * The one paid tool. The description leads with "paid" and "full quality
 * already ran for free" precisely to reinforce deep_match's own description —
 * an agent should never conclude "export failing" means "the match was bad,"
 * only "payment is needed."
 */
const DESCRIPTION = `Get the deploy-ready redirect file for a completed deep_match run — the actual artifact you install on the old server (.htaccess, nginx config, vercel.json, etc.).

This is what's paid for; deep_match itself already ran free at full quality. If payment is required you'll get a structured Payment Required response (MPP, JSON-RPC -32042) with a checkout URL — that page must be opened by a human in a browser; you cannot complete payment yourself. Once paid, call export again with the SAME arguments (or with the "opaque" value echoed back) and it will succeed — no separate "confirm payment" step exists, this tool re-checks entitlement itself.

Formats: apache, nginx, wordpress, vercel, cloudflare, shopify, csv, json.`;

const FORMATS = ['apache', 'nginx', 'wordpress', 'vercel', 'cloudflare', 'shopify', 'csv', 'json'] as const;

export function registerExportTool(server: McpServer): void {
  server.registerTool(
    'export',
    {
      title: 'Export redirect file',
      description: DESCRIPTION,
      inputSchema: {
        migration_id: z.string().describe('The migration_id from deep_match.'),
        format: z.enum(FORMATS).default('csv').describe('Target platform format for the redirect file.'),
        url_format: z
          .enum(['paths', 'full'])
          .default('paths')
          .describe("'paths' (default) for platforms that match on request path — almost always what you want. 'full' only if your target explicitly requires absolute URLs."),
        min_confidence: z.number().min(0).max(1).default(0).describe('Drop matches below this confidence (0-1). Default 0 includes everything deep_match found.'),
        opaque: z
          .string()
          .optional()
          .describe('Echo back the "opaque" value from a prior Payment Required response, if you have one. Optional — retrying with the same migration_id works too.'),
      },
      annotations: { readOnlyHint: false, idempotentHint: true, openWorldHint: false },
    },
    async ({ migration_id, format, url_format, min_confidence }, extra) => {
      const { client } = await clientForCall(extra as ToolExtra);
      const result = await client.getExport(migration_id, { format, urlFormat: url_format, minConfidence: min_confidence });

      if (result.ok) {
        return {
          content: [
            { type: 'text', text: `${result.redirectCount} redirects, ${result.filename}:` },
            { type: 'text', text: result.content },
          ],
        };
      }

      if (result.error.status === 402) {
        const upgradeUrl = String(result.error.body.upgrade_url ?? `${config.backendBaseUrl}`);
        const challenge = buildExportPaymentChallenge({
          migrationId: migration_id,
          format,
          urlFormat: url_format,
          minConfidence: min_confidence,
          realm: new URL(config.publicUrl).host,
          checkoutUrl: upgradeUrl,
          description: result.error.message,
        });
        // Never reached with a normal return — this throws an McpError the
        // SDK serializes as the JSON-RPC error envelope (code -32042), not a
        // CallToolResult with isError. See payments/mpp.ts for why that
        // matters: MPP's error must be a transport-level JSON-RPC error, not
        // content the agent has to parse out of a successful-looking result.
        throwPaymentRequired(challenge);
      }

      return {
        isError: true,
        content: [{ type: 'text', text: `${result.error.code}: ${result.error.message}` }],
      };
    },
  );
}
