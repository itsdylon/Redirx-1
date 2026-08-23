import { requireBearerAuth } from '@modelcontextprotocol/sdk/server/auth/middleware/bearerAuth.js';
import { getOAuthProtectedResourceMetadataUrl, mcpAuthMetadataRouter } from '@modelcontextprotocol/sdk/server/auth/router.js';
import { createMcpExpressApp } from '@modelcontextprotocol/sdk/server/express.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import type { RequestHandler } from 'express';
import { DevApiKeyAdapter } from './auth/devApiKeyAdapter.js';
import { SupabaseAuthAdapter } from './auth/supabaseAuthAdapter.js';
import type { AuthorizationServerAdapter } from './auth/types.js';
import { config } from './config.js';
import { buildMcpServer } from './mcpServer.js';
import { shutdownTelemetry } from './telemetry/posthog.js';

function buildAdapter(): AuthorizationServerAdapter {
  if (config.authMode === 'dev') {
    console.warn(
      '[auth] MCP_AUTH_MODE=dev — accepting raw Redirx API keys as bearer tokens. ' +
        'Do not run this mode with a public MCP_PUBLIC_URL.',
    );
    return new DevApiKeyAdapter(config.backendBaseUrl);
  }

  if (!config.authIssuerUrl) {
    throw new Error(
      'OAUTH_ISSUER_URL is required in oauth mode (MCP_AUTH_MODE unset or "oauth"). ' +
        'Set it to your Supabase project\'s auth URL, e.g. https://<ref>.supabase.co/auth/v1, ' +
        'or set MCP_AUTH_MODE=dev for local/API-key-only testing.',
    );
  }
  const anonKey = process.env.SUPABASE_ANON_KEY;
  if (!anonKey) {
    throw new Error('SUPABASE_ANON_KEY is required alongside OAUTH_ISSUER_URL in oauth mode.');
  }
  return new SupabaseAuthAdapter(config.authIssuerUrl, anonKey);
}

async function main() {
  const adapter = buildAdapter();
  const resourceServerUrl = new URL(config.publicUrl);

  const app = createMcpExpressApp({
    host: config.host,
    // hostHeaderValidation compares against the Host header's hostname with
    // the port already stripped (new URL(`http://${header}`).hostname) — an
    // entry here that still has a port on it silently never matches.
    allowedHosts: [resourceServerUrl.hostname, ...config.allowedHosts],
  });

  app.get('/health', (_req, res) => {
    res.json({ status: 'ok', authMode: config.authMode });
  });

  let authMiddleware: RequestHandler | undefined;
  const oauthMetadata = await adapter.metadata();

  if (oauthMetadata) {
    app.use(
      mcpAuthMetadataRouter({
        oauthMetadata,
        resourceServerUrl,
        resourceName: 'RedirX',
        scopesSupported: [],
      }),
    );
    authMiddleware = requireBearerAuth({
      verifier: adapter,
      resourceMetadataUrl: getOAuthProtectedResourceMetadataUrl(resourceServerUrl),
    });
  } else if (config.authMode === 'dev') {
    // No AS to discover in dev mode by design (see DevApiKeyAdapter) — still
    // enforce the bearer check, just without a resource_metadata hint since
    // there is nowhere for a client to go get a token from.
    authMiddleware = requireBearerAuth({ verifier: adapter });
  } else {
    // oauth mode but discovery failed at startup: fail closed. Serving tools
    // with no way to verify who's calling them is worse than not serving at
    // all — see SupabaseAuthAdapter's own log line for the fix.
    throw new Error('Authorization server metadata discovery failed; refusing to start in oauth mode.');
  }

  const mcpHandler: RequestHandler = async (req, res) => {
    try {
      // Stateless: a fresh McpServer + transport per request, no session map.
      // Simpler to run correctly behind Render's autoscaling (no sticky
      // sessions needed) at the cost of no server->client streaming across
      // calls, which none of the four tools need — each is a single
      // request/response.
      const server = buildMcpServer();
      const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
      res.on('close', () => {
        transport.close();
        server.close();
      });
      await server.connect(transport);
      await transport.handleRequest(req, res, req.body);
    } catch (err) {
      console.error('Error handling MCP request:', err);
      if (!res.headersSent) {
        res.status(500).json({
          jsonrpc: '2.0',
          error: { code: -32603, message: 'Internal server error' },
          id: null,
        });
      }
    }
  };

  app.post('/mcp', authMiddleware, mcpHandler);
  app.get('/mcp', (_req, res) => {
    res.status(405).json({ jsonrpc: '2.0', error: { code: -32000, message: 'Method not allowed.' }, id: null });
  });
  app.delete('/mcp', (_req, res) => {
    res.status(405).json({ jsonrpc: '2.0', error: { code: -32000, message: 'Method not allowed.' }, id: null });
  });

  app.listen(config.port, () => {
    console.log(`RedirX MCP server listening on :${config.port} (auth: ${config.authMode})`);
    console.log(`Public URL: ${config.publicUrl}`);
  });
}

async function shutdown() {
  await shutdownTelemetry();
  process.exit(0);
}
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

main().catch((err) => {
  console.error('Fatal startup error:', err);
  process.exit(1);
});
