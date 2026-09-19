import * as oauth from 'oauth4webapi';

export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Supabase tokens are identity evidence here, never gateway credentials. */
export class SupabaseIdentity {
  constructor({ issuer, clientId, publicKey, callback, allowLoopback = false }) {
    this.as = { issuer, authorization_endpoint: `${issuer}/oauth/authorize`, token_endpoint: `${issuer}/oauth/token` };
    this.client = { client_id: clientId, token_endpoint_auth_method: 'none' };
    this.publicKey = publicKey;
    this.callback = callback;
    this.options = { [oauth.allowInsecureRequests]: allowLoopback, signal: () => AbortSignal.timeout(10000) };
  }

  async begin(state) {
    const verifier = oauth.generateRandomCodeVerifier();
    const challenge = await oauth.calculatePKCECodeChallenge(verifier);
    const url = new URL(this.as.authorization_endpoint);
    url.search = new URLSearchParams({ response_type: 'code', client_id: this.client.client_id,
      redirect_uri: this.callback, scope: 'email profile', state,
      code_challenge: challenge, code_challenge_method: 'S256' }).toString();
    return { url: url.href, verifier };
  }

  async finish(parameters, state, verifier) {
    try {
      const checked = oauth.validateAuthResponse(this.as, this.client, parameters, state);
      const response = await oauth.authorizationCodeGrantRequest(this.as, this.client, oauth.None(),
        checked, this.callback, verifier, this.options);
      const result = await oauth.processAuthorizationCodeResponse(this.as, this.client, response);
      if (result.access_token.length > 16384 || result.token_type.toLowerCase() !== 'bearer') throw new Error();
      const userResponse = await fetch(`${this.as.issuer}/user`, {
        headers: { apikey: this.publicKey, Authorization: `Bearer ${result.access_token}` },
        redirect: 'error', signal: AbortSignal.timeout(10000),
      });
      if (!userResponse.ok) throw new Error();
      const reader = userResponse.body.getReader();
      const chunks = []; let size = 0;
      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          size += value.length;
          if (size > 65536) throw new Error();
          chunks.push(value);
        }
      } finally { await reader.cancel(); }
      const user = JSON.parse(Buffer.concat(chunks));
      // Decode ONLY after Supabase verified the identical bearer. Require this
      // registered upstream client; a normal app session cannot enter the bridge.
      const claims = JSON.parse(Buffer.from(result.access_token.split('.')[1], 'base64url'));
      const now = Math.floor(Date.now() / 1000);
      const audience = typeof claims.aud === 'string' ? [claims.aud] : claims.aud;
      if (!UUID.test(user.id) || claims.sub !== user.id || claims.iss !== this.as.issuer ||
          claims.client_id !== this.client.client_id || !Array.isArray(audience) ||
          !audience.includes('authenticated') || !Number.isSafeInteger(claims.exp) || claims.exp <= now ||
          ['iat', 'nbf'].some((key) => claims[key] !== undefined &&
            (!Number.isSafeInteger(claims[key]) || claims[key] > now))) throw new Error();
      return { accountId: user.id };
    } catch {
      throw new Error('Supabase sign-in could not be verified');
    }
  }
}
