import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { uploadCSVs } from './pipeline';

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

beforeEach(() => {
  localStorage.setItem('access_token', 'test-token-123');
});

afterEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
    headers: { get: () => null },
  });
}

describe('uploadCSVs', () => {
  it('throws structured content URL cap error from 422 responses', async () => {
    mockFetch.mockReturnValueOnce(
      jsonResponse(
        {
          reason_code: 'content_old_url_cap_exceeded',
          user_message: 'Deep Match has a per-file limit of 5,000 URLs. Split your CSV or switch to Quick Match.',
          old_url_count: 5001,
          new_url_count: 40,
          max_old_urls: 5000,
          max_new_urls: 5000,
          affected_file: 'old',
          next_action: 'reduce_csv_rows_or_switch_pipeline',
          retryable: false,
        },
        422
      )
    );

    const oldFile = new File(['https://old.example.com/a'], 'old.csv', { type: 'text/csv' });
    const newFile = new File(['https://new.example.com/a'], 'new.csv', { type: 'text/csv' });

    await expect(uploadCSVs(oldFile, newFile)).rejects.toMatchObject({
      type: 'content_url_cap_exceeded',
      reason_code: 'content_old_url_cap_exceeded',
      old_url_count: 5001,
      max_old_urls: 5000,
      affected_file: 'old',
    });
  });

  it('maps non-cap API errors through centralized handler', async () => {
    mockFetch.mockReturnValueOnce(
      jsonResponse(
        {
          user_message: 'Free accounts can only start Quick Match from upload.',
          code: 'deep_match_requires_project_checkout',
        },
        403
      )
    );

    const oldFile = new File(['https://old.example.com/a'], 'old.csv', { type: 'text/csv' });
    const newFile = new File(['https://new.example.com/a'], 'new.csv', { type: 'text/csv' });

    await expect(uploadCSVs(oldFile, newFile)).rejects.toThrow(
      'Free accounts can only start Quick Match from upload.'
    );
  });
});
