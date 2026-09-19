// Creates a local secret file once. Never prints key material or changes a service.
import { generateKeyPairSync, randomBytes } from 'node:crypto';
import { writeFile } from 'node:fs/promises';
const target = process.argv[2];
if (!target) throw new Error('Pass a new file path outside the repository');
const { privateKey } = generateKeyPairSync('rsa', { modulusLength: 2048 });
const jwk = privateKey.export({ format: 'jwk' });
const values = {
  AUTH_SIGNING_JWKS: JSON.stringify({ keys: [{ ...jwk, alg: 'RS256', use: 'sig', kid: randomBytes(16).toString('hex') }] }),
  AUTH_COOKIE_KEYS: JSON.stringify([randomBytes(32).toString('base64url')]),
  AUTH_STORAGE_KEYS: JSON.stringify([randomBytes(32).toString('base64url')]),
};
await writeFile(target, JSON.stringify(values, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
console.log('Secret file created with owner-only permissions; no key material printed.');
