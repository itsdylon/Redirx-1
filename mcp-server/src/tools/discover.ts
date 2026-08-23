import type { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import * as z from 'zod';
import { clientForCall, type ToolExtra } from './context.js';

/**
 * Tool descriptions are load-bearing (brief's own words) — this is the text
 * an agent reads to decide whether to call the tool at all, before it has
 * seen any output. Says what to call it WITH (a bare domain) and what to do
 * with the result (feed straight into deep_match), because the two most
 * likely mistakes are passing a single URL instead of a domain and not
 * knowing this has to run twice (old side, new side).
 */
const DESCRIPTION = `Enumerate a website's page URLs from its root domain — tries sitemap.xml first, then the CMS's own API (WordPress/Shopify), then a crawl.

Call this once for the OLD site and once for the NEW site before deep_match; its "urls" output is exactly deep_match's old_urls/new_urls input. Free, and fast (seconds, not minutes) — it does not run the matcher.

If the site blocks automated discovery (robots.txt, aggressive rate limiting) this returns an error explaining why; provide URLs directly to deep_match instead of retrying discover.`;

export function registerDiscoverTool(server: McpServer): void {
  server.registerTool(
    'discover',
    {
      title: 'Discover site URLs',
      description: DESCRIPTION,
      inputSchema: {
        domain: z.string().describe("Root domain to scan, e.g. 'example.com' or 'https://example.com'. Not a single page URL."),
        side: z
          .enum(['old', 'new'])
          .describe("Which side of the migration this domain is — 'old' (being migrated away from) or 'new' (the destination)."),
      },
      annotations: { readOnlyHint: true, idempotentHint: true, openWorldHint: true },
    },
    async ({ domain, side }, extra) => {
      const { client } = await clientForCall(extra as ToolExtra);
      const result = await client.discover(domain, side);

      if (!result.ok) {
        return {
          isError: true,
          content: [{ type: 'text', text: `${result.error.code}: ${result.error.message}` }],
        };
      }

      const { data } = result;
      return {
        content: [
          {
            type: 'text',
            text: [
              `Found ${data.count} URL${data.count === 1 ? '' : 's'} on ${data.root_url} via ${data.discovery_method}` +
                (data.truncated ? ` (truncated at ${data.max_urls} by your plan)` : '') +
                '.',
              '',
              JSON.stringify(data.urls, null, 2),
            ].join('\n'),
          },
        ],
      };
    },
  );
}
