import type { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import * as z from 'zod';
import { clientForCall, type ToolExtra } from './context.js';

/**
 * The load-bearing sentence in this description is "never gated by plan" —
 * an agent deciding whether it's worth calling this tool at all needs to know
 * up front that quality isn't rationed, only reading it *after* getting
 * blocked doesn't help. The abuse ceiling is mentioned honestly rather than
 * hidden, because an agent that hits it needs to understand the 429 as
 * "come back later," not "broken."
 *
 * Single tool for start+poll+read, not three tools: this can't be one
 * blocking call (a real migration takes minutes; see
 * docs/architecture/agentic-pivot.md §1.4), and three separate tools for one
 * state machine is worse ergonomics than one tool an agent calls repeatedly
 * until `done`.
 */
const DESCRIPTION = `Run RedirX's full content-matching engine (Deep Match) to pair old URLs with their new-site equivalents by scraping and comparing actual page content — not just URL similarity.

Free, full quality, on your full URL set, on every plan. Nothing about match quality is ever gated; only export (a separate tool) is paid. The only limit is an abuse ceiling on how many free runs an account can start in a rolling window — hitting it returns a 429 you should treat as "retry later," not "broken."

This is NOT a single blocking call — it takes minutes on a real site (the engine scrapes every page). Call it once with old_urls/new_urls to start a run and get back a migration_id; call it again with ONLY migration_id to poll status. Keep polling until the response's "done" field is true, then call the "preview" or "export" tool with the same migration_id.`;

export function registerDeepMatchTool(server: McpServer): void {
  server.registerTool(
    'deep_match',
    {
      title: 'Run Deep Match',
      description: DESCRIPTION,
      inputSchema: {
        migration_id: z
          .string()
          .optional()
          .describe('Omit to start a new run. Provide to poll an existing run\'s status instead of starting another.'),
        old_urls: z.array(z.string()).optional().describe('Required to start a run. The old site\'s URLs (from the discover tool, or your own list).'),
        new_urls: z.array(z.string()).optional().describe('Required to start a run. The new site\'s URLs.'),
        name: z.string().optional().describe('A human-readable label for this migration.'),
      },
      annotations: { readOnlyHint: false, idempotentHint: false, openWorldHint: true },
    },
    async ({ migration_id, old_urls, new_urls, name }, extra) => {
      const { client } = await clientForCall(extra as ToolExtra);

      if (!migration_id) {
        if (!old_urls?.length || !new_urls?.length) {
          return {
            isError: true,
            content: [
              {
                type: 'text',
                text: 'Provide either migration_id (to poll a run already started) or both old_urls and new_urls (to start one).',
              },
            ],
          };
        }
        const result = await client.createMigration({ oldUrls: old_urls, newUrls: new_urls, name, pipeline: 'content' });
        if (!result.ok) {
          return { isError: true, content: [{ type: 'text', text: `${result.error.code}: ${result.error.message}` }] };
        }
        const warning = result.data.warning_message ? `\n\nNote: ${result.data.warning_message}` : '';
        return {
          content: [
            {
              type: 'text',
              text:
                `Started migration ${result.data.id} (status: pending). This runs in the background — ` +
                `call deep_match again with migration_id="${result.data.id}" to check progress.${warning}`,
            },
          ],
        };
      }

      const status = await client.getMigration(migration_id);
      if (!status.ok) {
        return { isError: true, content: [{ type: 'text', text: `${status.error.code}: ${status.error.message}` }] };
      }

      const s = status.data;
      const lines = [`Migration ${s.id}: ${s.status}${s.done ? ' (done)' : ''}`];
      if (!s.done && s.stage) lines.push(`Stage: ${s.stage} (${s.stage_index}/${s.total_stages})`);
      if (s.done && s.status === 'completed') {
        lines.push(`Total matches: ${s.total_mappings}.`);
        lines.push(`Next: call "preview" for a free summary, or "export" for the deploy-ready redirect file.`);
      }
      if (s.error) lines.push(`Error: ${s.error}`);
      return { content: [{ type: 'text', text: lines.join('\n') }] };
    },
  );
}
