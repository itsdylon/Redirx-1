import { expect, it } from 'vitest';
import type { CaptureResult } from 'posthog-js';
import { scrubAuthSecrets, scrubSensitiveUrlParams } from './posthogScrub';

const IMPLICIT_CALLBACK =
  'https://app.redirx.dev/auth/callback#access_token=eyJhbGciOi.payload.sig&expires_at=1&expires_in=3600' +
  '&provider_token=ya29.fixture&refresh_token=fixture-refresh&token_type=bearer';

it('strips implicit-flow tokens from the fragment and keeps harmless params', () => {
  const scrubbed = scrubSensitiveUrlParams(IMPLICIT_CALLBACK);
  expect(scrubbed).not.toMatch(/eyJhbGciOi|ya29\.fixture|fixture-refresh/);
  expect(scrubbed).toContain('access_token=redacted');
  expect(scrubbed).toContain('refresh_token=redacted');
  expect(scrubbed).toContain('provider_token=redacted');
  expect(scrubbed).toContain('expires_in=3600&');
  expect(scrubbed).toContain('token_type=bearer');
});

it('strips PKCE codes and email token hashes but not look-alike params', () => {
  expect(scrubSensitiveUrlParams('https://app.redirx.dev/auth/callback?code=abc-123&next=%2F')).toBe(
    'https://app.redirx.dev/auth/callback?code=redacted&next=%2F'
  );
  expect(scrubSensitiveUrlParams('/verify?token_hash=h4sh&type=signup')).toBe('/verify?token_hash=redacted&type=signup');
  expect(scrubSensitiveUrlParams('/auth/callback?error_code=otp_expired&zipcode=94110')).toBe(
    '/auth/callback?error_code=otp_expired&zipcode=94110'
  );
});

it('scrubs every URL-bearing property, including person properties, without touching others', () => {
  const event = {
    uuid: 'fixture',
    event: '$pageview',
    properties: {
      $current_url: IMPLICIT_CALLBACK,
      $referrer: IMPLICIT_CALLBACK,
      $pathname: '/auth/callback',
      source_repo: 'app',
      $web_vitals_LCP_event: { $current_url: IMPLICIT_CALLBACK, value: 1200 },
    },
    $set_once: { $initial_current_url: IMPLICIT_CALLBACK },
    $set: { $current_url: IMPLICIT_CALLBACK },
  } as unknown as CaptureResult;

  const scrubbed = scrubAuthSecrets(event)!;
  expect(JSON.stringify(scrubbed)).not.toMatch(/eyJhbGciOi|ya29\.fixture|fixture-refresh/);
  expect(scrubbed.properties.$pathname).toBe('/auth/callback');
  expect(scrubbed.properties.source_repo).toBe('app');
  expect(scrubbed.properties.$web_vitals_LCP_event.value).toBe(1200);
  // The input event is not mutated.
  expect(event.properties.$current_url).toBe(IMPLICIT_CALLBACK);
});

it('returns unchanged objects by reference and passes null through', () => {
  const properties = { $current_url: 'https://app.redirx.dev/dashboard', nested: { a: 1 } };
  const event = { uuid: 'x', event: '$pageview', properties } as unknown as CaptureResult;
  expect(scrubAuthSecrets(event)!.properties).toBe(properties);
  expect(scrubAuthSecrets(null)).toBeNull();
});
