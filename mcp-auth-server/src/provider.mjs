import { randomBytes, timingSafeEqual } from 'node:crypto';
import { Provider, errors } from 'oidc-provider';
import { UUID } from './upstream.mjs';

const random = () => randomBytes(32).toString('base64url');
const equal = (a, b) => typeof a === 'string' && typeof b === 'string' &&
  Buffer.byteLength(a) === Buffer.byteLength(b) && timingSafeEqual(Buffer.from(a), Buffer.from(b));
const escape = (value) => String(value).replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

async function form(ctx) {
  if (!ctx.is('application/x-www-form-urlencoded')) ctx.throw(415);
  let value = '';
  for await (const chunk of ctx.req) {
    value += chunk.toString();
    if (Buffer.byteLength(value) > 4096) ctx.throw(413);
  }
  const params = new URLSearchParams(value);
  if ([...params.keys()].some((key) => params.getAll(key).length !== 1)) ctx.throw(400);
  return params;
}

function page(ctx, title, body) {
  const nonce = random();
  // Chrome sends Origin:null for form posts under no-referrer. Keep same-origin
  // form provenance without sending a referrer to Supabase or downstream clients.
  if (body.includes('<form')) ctx.set('Referrer-Policy', 'same-origin');
  ctx.set('Content-Security-Policy', `default-src 'none'; style-src 'nonce-${nonce}'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'`);
  ctx.type = 'html';
  ctx.body = `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>${escape(title)} · RedirX</title><style nonce="${nonce}">
    body{margin:0;background:#f7f5ee;color:#232922;font:17px/1.5 system-ui,sans-serif}main{max-width:560px;margin:8vh auto;padding:28px}
    h1{font-size:30px;line-height:1.15}dl{border-block:1px solid #bbbcb1;padding:16px 0}dt{font-weight:600}dd{margin:4px 0 16px;overflow-wrap:anywhere}
    button{background:#284c39;color:white;border:1px solid #284c39;border-radius:2px;padding:12px 20px;font:inherit;cursor:pointer;margin:8px 8px 0 0}
    button[value=deny]{background:transparent;color:#232922}button:focus-visible,a:focus-visible{outline:3px solid #b66c24;outline-offset:3px}</style>
    <main><p>RedirX · Secure connection</p><h1>${escape(title)}</h1>${body}</main></html>`;
}

function navigateAfterPost(ctx, target) {
  // A form's CSP applies to its HTTP redirect chain in Chrome. Start a fresh
  // navigation from this response so form-action can remain strictly 'self'.
  ctx.status = 200;
  page(ctx, 'Continuing your connection', `<p>Returning to the sign-in flow…</p><a href="${escape(target)}">Continue</a>`);
  ctx.body = ctx.body.replace('<main>', `<meta http-equiv="refresh" content="0;url=${escape(target)}"><main>`);
}

function validateClient(_ctx, _key, _value, metadata) {
  if (!['none', 'client_secret_basic'].includes(metadata.token_endpoint_auth_method) ||
      metadata.subject_type !== 'public' ||
      metadata.grant_types.some((type) => !['authorization_code', 'refresh_token'].includes(type)) ||
      metadata.response_types.some((type) => type !== 'code') ||
      metadata.redirect_uris.length > 8 ||
      metadata.redirect_uris.some((uri) => {
        const url = new URL(uri);
        return url.username || url.password || url.hash ||
          (url.protocol !== 'https:' && !(url.protocol === 'http:' &&
            ['127.0.0.1', '[::1]', 'localhost'].includes(url.hostname)));
      })) throw new errors.InvalidClientMetadata('Unsupported client configuration');
  // No external metadata fetching or attacker-provided HTML is needed for MCP.
  for (const key of ['jwks_uri', 'sector_identifier_uri', 'request_uris', 'initiate_login_uri']) {
    if (metadata[key] !== undefined) throw new errors.InvalidClientMetadata('External client metadata is not supported');
  }
  if (metadata.client_name && metadata.client_name.length > 160) throw new errors.InvalidClientMetadata('Client name is too long');
}

export function createAuthorizationProvider({ issuer, resource, supabaseIssuer, jwks, cookieKeys,
  store, identity, clients = [], trustedProxy = false }) {
  const origin = new URL(issuer).origin;
  const secure = new URL(issuer).protocol === 'https:';
  const cookieName = secure ? '__Host-redirx_bridge' : 'redirx_bridge';
  const provider = new Provider(issuer, {
    adapter: store.Adapter, clients, jwks,
    cookies: { keys: cookieKeys, short: { signed: true, sameSite: 'lax' }, long: { signed: true, sameSite: 'lax' } },
    responseTypes: ['code'], subjectTypes: ['public'],
    scopes: ['openid', 'offline_access', 'mcp:tools'],
    claims: { openid: ['sub'] },
    clientDefaults: { token_endpoint_auth_method: 'none', grant_types: ['authorization_code', 'refresh_token'], response_types: ['code'] },
    allowOmittingSingleRegisteredRedirectUri: false,
    pkce: { methods: ['S256'], required: () => true },
    features: {
      devInteractions: { enabled: false },
      registration: { enabled: true, issueRegistrationAccessToken: true },
      registrationManagement: { enabled: true },
      revocation: { enabled: true, allowedPolicy: (_ctx, client, token) => client.clientId === token.clientId },
      userinfo: { enabled: false },
      resourceIndicators: {
        enabled: true,
        defaultResource() { throw new errors.InvalidTarget('The MCP resource is required'); },
        useGrantedResource: () => true,
        getResourceServerInfo(ctx, requested) {
          if (requested !== resource || ctx.oidc.params.resource !== resource) throw new errors.InvalidTarget();
          return { scope: 'mcp:tools', audience: resource, accessTokenTTL: 300,
            accessTokenFormat: 'jwt', jwt: { sign: { alg: 'RS256' } } };
        },
      },
    },
    extraClientMetadata: { properties: ['redirx_policy'], validator: validateClient },
    findAccount(_ctx, id) {
      if (!UUID.test(id)) return undefined;
      return { accountId: id, claims: async () => ({ sub: id }) };
    },
    extraTokenClaims: () => ({ identity_issuer: supabaseIssuer }),
    issueRefreshToken: (_ctx, client) => client.grantTypeAllowed('refresh_token'),
    rotateRefreshToken: () => true,
    expiresWithSession: () => false,
    ttl: { AccessToken: 300, AuthorizationCode: 60, Interaction: 600, Session: 3600,
      Grant: 30 * 86400, RefreshToken: 30 * 86400, RegistrationAccessToken: 30 * 86400 },
    interactions: { url: (_ctx, interaction) => `/interaction/${interaction.uid}` },
    renderError(ctx) { page(ctx, 'Connection could not complete', '<p>Return to your MCP client and start a new connection.</p>'); },
    clientBasedCORS(_ctx, requestOrigin, client) {
      return client.redirectUris.some((uri) => new URL(uri).origin === requestOrigin);
    },
  });
  provider.proxy = trustedProxy;
  provider.proxyIpHeader = 'X-Forwarded-For';
  provider.maxIpsCount = 1;
  provider.on('error', () => {}); // No URLs, codes, tokens, or upstream bodies in logs.
  provider.on('server_error', () => {});

  // Koa sends buffered responses after middleware returns, so DB commit failure
  // cannot hand a client a token whose grant changes were rolled back.
  provider.use(async (ctx, next) => {
    ctx.set('Cache-Control', 'no-store');
    ctx.set('Referrer-Policy', 'no-referrer');
    ctx.set('X-Content-Type-Options', 'nosniff');
    if (ctx.host !== new URL(issuer).host) { ctx.status = 400; ctx.body = { error: 'invalid_request' }; return; }
    if (ctx.path === '/health') { ctx.body = { status: 'ok', service: 'mcp-authorization' }; return; }
    if (ctx.path === '/.well-known/oauth-authorization-server') ctx.path = '/.well-known/openid-configuration';
    if (['/.well-known/openid-configuration', '/jwks'].includes(ctx.path)) { await next(); return; }
    try {
      await store.transaction(async () => {
        const registration = ctx.path === '/reg' && ctx.method === 'POST';
        if (!await store.allow(`ip:${ctx.ip}:${registration ? 'registration' : 'authorization'}`, registration ? 10 : 180) ||
            (registration && !await store.allow('registration:global', 100))) {
          ctx.status = 429; ctx.set('Retry-After', '60'); ctx.body = { error: 'temporarily_unavailable' }; return;
        }
        await next();
        if (ctx.status >= 500) throw new Error('Authorization request failed');
      });
    } catch {
      ctx.remove('Location'); ctx.remove('Set-Cookie');
      ctx.status = 503; ctx.body = { error: 'temporarily_unavailable' };
    }
  });

  provider.use(async (ctx, next) => {
    if (ctx.path === '/upstream/callback' && ctx.method === 'GET') {
      const params = new URLSearchParams(ctx.querystring);
      const state = params.get('state');
      if (!state || params.getAll('state').length !== 1 || !equal(state, ctx.cookies.get(cookieName, { signed: true }))) {
        ctx.status = 400; page(ctx, 'Sign-in expired', '<p>Return to your MCP client and reconnect.</p>'); return;
      }
      const flow = await store.find('IdentityFlow', 'id_hash', state);
      if (!flow || flow.consumed) { ctx.status = 400; page(ctx, 'Sign-in expired', '<p>Start a new connection.</p>'); return; }
      await store.consume('IdentityFlow', state);
      try {
        const user = await identity.finish(params, state, flow.verifier);
        if (!UUID.test(user.accountId)) throw new Error();
        await store.upsert('VerifiedIdentity', flow.uid, { accountId: user.accountId, state }, 60);
        ctx.redirect(`/interaction/${flow.uid}/resume-login`);
      } catch {
        ctx.status = 400; page(ctx, 'Sign-in could not complete', '<p>Return to your MCP client and try again.</p>');
      }
      return;
    }
    const match = /^\/interaction\/([A-Za-z0-9_-]{1,128})(?:\/(login|confirm|resume-login))?$/.exec(ctx.path);
    if (!match) return next();
    const [, uid, action] = match;
    let details;
    try { details = await provider.interactionDetails(ctx.req, ctx.res); }
    catch { ctx.status = 400; page(ctx, 'Connection expired', '<p>Return to your MCP client and reconnect.</p>'); return; }
    if (details.uid !== uid) { ctx.status = 400; return; }
    if (action === 'resume-login' && ctx.method === 'GET') {
      const user = await store.find('VerifiedIdentity', 'id_hash', uid);
      if (details.prompt.name !== 'login' || !user || user.consumed ||
          !equal(user.state, ctx.cookies.get(cookieName, { signed: true }))) { ctx.status = 400; return; }
      await store.consume('VerifiedIdentity', uid);
      const redirect = await provider.interactionResult(ctx.req, ctx.res,
        { login: { accountId: user.accountId, remember: false } }, { mergeWithLastSubmission: false });
      ctx.cookies.set(cookieName, null, { path: '/', signed: true, secure, httpOnly: true, sameSite: 'lax' });
      ctx.status = 303; ctx.redirect(redirect); return;
    }
    if (!action && ctx.method === 'GET') {
      const csrf = random();
      await store.destroy('InteractionCSRF', uid);
      await store.upsert('InteractionCSRF', uid, { csrf }, 600);
      const client = await provider.Client.find(details.params.client_id);
      const login = details.prompt.name === 'login';
      if (!login && details.prompt.name !== 'consent') { ctx.status = 400; return; }
      const scopes = String(details.params.scope || '').split(' ').filter(Boolean);
      page(ctx, login ? 'Sign in to connect your agent' : `Connect ${client.clientName || client.clientId}`, `
        <p>${login ? 'Use your existing RedirX account. You will review this client’s access after signing in.' : 'Only approve a client you recognize. A registered name does not establish trust.'}</p>
        <dl><dt>Requesting client</dt><dd>${escape(client.clientName || client.clientId)}</dd>
        <dt>Client ID</dt><dd>${escape(client.clientId)}</dd>
        <dt>Return address</dt><dd>${escape(details.params.redirect_uri)}</dd>
        <dt>Service</dt><dd>${escape(resource)}</dd><dt>Requested access</dt><dd>${escape(scopes.join(', '))}</dd></dl>
        <p>The client can operate RedirX on your behalf and stay connected for up to 30 days. Payments still require approval.</p>
        <form method="post" action="/interaction/${uid}/${login ? 'login' : 'confirm'}">
        <input type="hidden" name="csrf" value="${csrf}">
        <button name="decision" value="approve">${login ? 'Continue to RedirX' : 'Approve connection'}</button>
        <button name="decision" value="deny">Decline</button></form>`);
      return;
    }
    if (ctx.method !== 'POST' || !['login', 'confirm'].includes(action)) { ctx.status = 405; return; }
    if (ctx.get('origin') !== origin) { ctx.status = 403; return; }
    let fields;
    try { fields = await form(ctx); } catch { ctx.status = 400; return; }
    const saved = await store.find('InteractionCSRF', 'id_hash', uid);
    if (!saved || saved.consumed || !equal(saved.csrf, fields.get('csrf'))) { ctx.status = 403; return; }
    await store.consume('InteractionCSRF', uid);
    if (fields.get('decision') === 'deny') {
      const redirect = await provider.interactionResult(ctx.req, ctx.res, { error: 'access_denied' }, { mergeWithLastSubmission: false });
      navigateAfterPost(ctx, redirect); return;
    }
    if (fields.get('decision') !== 'approve') { ctx.status = 400; return; }
    if (action === 'login' && details.prompt.name === 'login') {
      const state = random();
      const upstream = await identity.begin(state);
      await store.upsert('IdentityFlow', state, { uid, verifier: upstream.verifier }, 600);
      ctx.cookies.set(cookieName, state, { path: '/', signed: true, secure, httpOnly: true, sameSite: 'lax', maxAge: 600000 });
      navigateAfterPost(ctx, upstream.url); return;
    }
    if (action !== 'confirm' || details.prompt.name !== 'consent' || !UUID.test(details.session?.accountId)) { ctx.status = 400; return; }
    let grant = details.grantId ? await provider.Grant.find(details.grantId) : undefined;
    if (grant && (grant.accountId !== details.session.accountId || grant.clientId !== details.params.client_id)) { ctx.status = 400; return; }
    grant ||= new provider.Grant({ accountId: details.session.accountId, clientId: details.params.client_id });
    const missing = details.prompt.details;
    if (missing.missingOIDCScope) grant.addOIDCScope(missing.missingOIDCScope.join(' '));
    for (const [target, scopes] of Object.entries(missing.missingResourceScopes || {})) {
      if (target !== resource || scopes.some((scope) => scope !== 'mcp:tools')) { ctx.status = 400; return; }
      grant.addResourceScope(target, scopes.join(' '));
    }
    const grantId = await grant.save();
    const redirect = await provider.interactionResult(ctx.req, ctx.res, { consent: { grantId } });
    navigateAfterPost(ctx, redirect);
  });
  return provider;
}
