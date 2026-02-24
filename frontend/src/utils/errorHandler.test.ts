import { describe, it, expect } from 'vitest';
import {
  ApiError,
  handleApiError,
  handleSimpleError,
  throwApiErrorFromResponse,
  toApiError,
} from './errorHandler';

describe('errorHandler', () => {
  describe('handleApiError', () => {
    it('handles network errors', async () => {
      const error = new TypeError('Failed to fetch');
      const result = await handleApiError(error);

      expect(result.type).toBe('network_error');
      expect(result.message).toContain('Connection lost');
      expect(result.retryable).toBe(true);
    });

    it('handles timeout errors', async () => {
      const error = new Error('timeout');
      error.name = 'AbortError';
      const result = await handleApiError(error);

      expect(result.type).toBe('timeout_error');
      expect(result.message).toContain('timed out');
      expect(result.retryable).toBe(true);
    });

    it('uses retry_after_seconds from structured payload for 429', async () => {
      const response = new Response(
        JSON.stringify({
          code: 'trial_redeem_rate_limited',
          user_message: 'Too many redemption attempts. Please try again later.',
          retry_after_seconds: 22,
        }),
        { status: 429, headers: { 'Content-Type': 'application/json' } }
      );

      const result = await handleApiError(null, response);
      expect(result.type).toBe('rate_limit');
      expect(result.retryAfter).toBe(22);
      expect(result.message).toContain('Too many redemption attempts');
    });

    it('falls back to status text when non-json body is technical', async () => {
      const response = new Response('<html><body>500 Internal Error</body></html>', {
        status: 500,
        headers: { 'Content-Type': 'text/html' },
      });

      const result = await handleApiError(null, response);
      expect(result.type).toBe('server_error');
      expect(result.message).toContain('Server error');
    });
  });

  describe('ApiError conversion', () => {
    it('prefers user_message then legacy error', async () => {
      const response = new Response(
        JSON.stringify({
          code: 'auth_email_unconfirmed',
          error: 'Legacy fallback text',
          user_message: 'Please confirm your email before signing in.',
          retryable: false,
          next_action: 'verify_email',
        }),
        { status: 403, headers: { 'Content-Type': 'application/json' } }
      );

      const apiError = await toApiError(response);
      expect(apiError).toBeInstanceOf(ApiError);
      expect(apiError.code).toBe('auth_email_unconfirmed');
      expect(apiError.message).toBe('Please confirm your email before signing in.');
      expect(apiError.user_message).toBe('Please confirm your email before signing in.');
      expect(apiError.next_action).toBe('verify_email');
      expect(apiError.retryable).toBe(false);
      expect(apiError.status).toBe(403);
    });

    it('throwApiErrorFromResponse throws ApiError with parsed metadata', async () => {
      const response = new Response(
        JSON.stringify({
          code: 'billing_no_customer',
          user_message: 'No billing account was found for this user.',
          retryable: false,
        }),
        { status: 404, headers: { 'Content-Type': 'application/json' } }
      );

      await expect(throwApiErrorFromResponse(response)).rejects.toMatchObject({
        name: 'ApiError',
        code: 'billing_no_customer',
        status: 404,
        retryable: false,
      });
    });
  });

  describe('handleSimpleError', () => {
    it('handles TypeError', () => {
      const error = new TypeError('Some type error');
      const result = handleSimpleError(error);

      expect(result.type).toBe('network_error');
      expect(result.retryable).toBe(true);
    });

    it('handles AbortError', () => {
      const error = new Error('Aborted');
      error.name = 'AbortError';
      const result = handleSimpleError(error);

      expect(result.type).toBe('timeout_error');
      expect(result.retryable).toBe(true);
    });

    it('preserves custom error messages', () => {
      const error = new Error('Custom error message');
      const result = handleSimpleError(error);

      expect(result.message).toBe('Custom error message');
    });
  });
});
