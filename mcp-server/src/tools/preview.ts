import type { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import * as z from 'zod';
import { clientForCall, type ToolExtra } from './context.js';

const DESCRIPTION = `See how well a completed deep_match run did before deciding whether to pay for its export: match count, a confidence breakdown (high/medium/low), how many pages are still unmatched, and about 20 sample pairings spanning the confidence range (not just the best ones).

Always free — the matching already ran; this only reads and summarizes results that already exist. Call this before "export" so you (or whoever is paying) know what you're paying for. Requires the migration to be "done" — poll deep_match first if you're not sure.`;

export function registerPreviewTool(server: McpServer): void {
  server.registerTool(
    'preview',
    {
      title: 'Preview match quality',
      description: DESCRIPTION,
      inputSchema: {
        migration_id: z.string().describe('The migration_id from deep_match.'),
      },
      annotations: { readOnlyHint: true, idempotentHint: true },
    },
    async ({ migration_id }, extra) => {
      const { client } = await clientForCall(extra as ToolExtra);
      const result = await client.getPreview(migration_id);

      if (!result.ok) {
        return { isError: true, content: [{ type: 'text', text: `${result.error.code}: ${result.error.message}` }] };
      }

      const p = result.data;
      const dist = p.confidence_distribution;
      const lines = [
        `${p.total_mappings} matches (${dist.high} high, ${dist.medium} medium, ${dist.low} low confidence; ${p.needs_review_count} flagged for review).`,
      ];
      if (typeof p.unmatched_old_urls === 'number' && p.unmatched_old_urls > 0) {
        lines.push(`${p.unmatched_old_urls} old URLs found no match at all.`);
      }
      lines.push('', 'Sample pairings:');
      for (const m of p.sample) {
        lines.push(`  [${m.confidence_band}, ${m.confidence}%] ${m.old_url} -> ${m.new_url}`);
      }

      return { content: [{ type: 'text', text: lines.join('\n') }] };
    },
  );
}
