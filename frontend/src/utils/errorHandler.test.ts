/**
 * Tests for centralized error handler utility
 */

import { describe, it, expect } from 'vitest';
import { handleApiError, handleSimpleError } from './errorHandler';

describe('errorHandler', () => {
  describe('handleApiError', () => {
    it('should handle network errors', async () => {
      const error = new TypeError('Failed to fetch');
      const result = await handleApiError(error);

      expect(result.type).toBe('network_error');
      expect(result.message).toContain('Connection lost');
      expect(result.retryable).toBe(true);
    });

    it('should handle timeout errors', async () => {
      const error = new Error('timeout');
      error.name = 'AbortError';
      const result = await handleApiError(error);

      expect(result.type).toBe('timeout_error');
      expect(result.message).toContain('timed out');
      expect(result.retryable).toBe(true);
    });

    it('should handle 401 unauthorized', async () => {
      const response = new Response(JSON.stringify({ error: 'Unauthorized' }), {
        status: 401,
      });
      const result = await handleApiError(null, response);

      expect(result.type).toBe('unauthorized');
      expect(result.message).toContain('Unauthorized');
      expect(result.retryable).toBe(false);
    });

    it('should handle 404 not found', async () => {
      const response = new Response(JSON.stringify({ error: 'Not found' }), {
        status: 404,
      });
      const result = await handleApiError(null, response);

      expect(result.type).toBe('not_found');
      expect(result.message).toContain('not found');
      expect(result.retryable).toBe(false);
    });

    it('should handle 429 rate limiting with Retry-After header', async () => {
      const response = new Response(JSON.stringify({ message: 'Too many requests' }), {
        status: 429,
        headers: { 'Retry-After': '30' },
      });
      const result = await handleApiError(null, response);

      expect(result.type).toBe('rate_limit');
      expect(result.message).toContain('30 seconds');
      expect(result.retryable).toBe(true);
      expect(result.retryAfter).toBe(30);
    });

    it('should handle 500 server errors', async () => {
      const response = new Response(JSON.stringify({}), { status: 500 });
      const result = await handleApiError(null, response);

      expect(result.type).toBe('server_error');
      expect(result.message).toContain('Server error');
      expect(result.retryable).toBe(true);
    });

    it('should parse backend error messages for 4xx errors', async () => {
      const response = new Response(
        JSON.stringify({ error: 'Invalid input data' }),
        { status: 400 }
      );
      const result = await handleApiError(null, response);

      expect(result.type).toBe('client_error');
      expect(result.message).toBe('Invalid input data');
      expect(result.retryable).toBe(false);
    });

    it('should handle unknown errors gracefully', async () => {
      const result = await handleApiError({ weird: 'object' });

      expect(result.type).toBe('unknown_error');
      expect(result.message).toContain('unexpected error');
      expect(result.retryable).toBe(false);
    });
  });

  describe('handleSimpleError', () => {
    it('should handle TypeError', () => {
      const error = new TypeError('Some type error');
      const result = handleSimpleError(error);

      expect(result.type).toBe('network_error');
      expect(result.retryable).toBe(true);
    });

    it('should handle AbortError', () => {
      const error = new Error('Aborted');
      error.name = 'AbortError';
      const result = handleSimpleError(error);

      expect(result.type).toBe('timeout_error');
      expect(result.retryable).toBe(true);
    });

    it('should preserve error messages', () => {
      const error = new Error('Custom error message');
      const result = handleSimpleError(error);

      expect(result.message).toBe('Custom error message');
    });
  });
});
