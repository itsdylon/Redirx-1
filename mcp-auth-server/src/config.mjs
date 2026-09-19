function url(value, name, local) {
  const parsed = new URL(value);
  if (parsed.username || parsed.password || parsed.search || parsed.hash ||
      (parsed.protocol !== 'https:' && !(local && parsed.protocol === 'http:' &&
        ['127.0.0.1', 'localhost', '[::1]'].includes(parsed.hostname)))) throw new Error(`Invalid ${name}`);
  return parsed;
}

export function configuration(env = process.env) {
  const required = (key) => { if (!env[key]?.trim()) throw new Error(`${key} is required`); return env[key]; };
  const local = env.AUTH_LOCAL_TEST === '1' && env.NODE_ENV !== 'production';
  const issuer = url(required('AUTH_ISSUER_URL'), 'AUTH_ISSUER_URL', local);
  if (issuer.pathname !== '/') throw new Error('Authorization issuer must be an origin without a path');
  const resource = url(required('MCP_PUBLIC_URL'), 'MCP_PUBLIC_URL', local);
  const upstream = url(required('SUPABASE_AUTH_ISSUER'), 'SUPABASE_AUTH_ISSUER', local);
  const parseKeys = (key) => {
    const result = JSON.parse(required(key));
    if (!Array.isArray(result) || !result.length || result.length > 3 ||
        result.some((value) => typeof value !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(value))) {
      throw new Error(`${key} must contain one to three base64url 32-byte keys`);
    }
    return result;
  };
  const jwks = JSON.parse(required('AUTH_SIGNING_JWKS'));
  if (!Array.isArray(jwks.keys) || !jwks.keys.length || !jwks.keys.some((key) =>
    key.kty === 'RSA' && key.d && key.kid && key.alg === 'RS256') ||
    jwks.keys.some((key) => key.kty !== 'RSA' || key.alg !== 'RS256' || !key.kid ||
      (key.use !== undefined && key.use !== 'sig'))) throw new Error('An RS256 signing key with kid is required');
  const port = Number(env.PORT || 8790);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid PORT');
  const databaseUrl = required('AUTH_DATABASE_URL');
  const database = new URL(databaseUrl);
  if (!['postgres:', 'postgresql:'].includes(database.protocol) ||
      (!['127.0.0.1', 'localhost', '[::1]'].includes(database.hostname) &&
        database.searchParams.get('sslmode') !== 'verify-full')) {
    throw new Error('Remote AUTH_DATABASE_URL requires verified TLS (sslmode=verify-full)');
  }
  return { issuer: issuer.origin, resource: resource.href,
    supabaseIssuer: upstream.href.replace(/\/$/, ''), upstreamClientId: required('SUPABASE_OAUTH_CLIENT_ID'),
    publicKey: required('SUPABASE_ANON_KEY'), jwks,
    cookieKeys: parseKeys('AUTH_COOKIE_KEYS'), encryptionKeys: parseKeys('AUTH_STORAGE_KEYS').map((key) => Buffer.from(key, 'base64url')),
    databaseUrl, trustedProxy: env.AUTH_TRUST_PROXY === '1',
    local, port, host: env.HOST || '127.0.0.1' };
}
