import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { ApiKeysPanel } from './ApiKeysPanel';

const listApiKeys = vi.fn();
const createApiKey = vi.fn();
const revokeApiKey = vi.fn();

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('../api/keys', async () => {
  const actual = await vi.importActual<typeof import('../api/keys')>('../api/keys');
  return {
    ...actual,
    listApiKeys: (...a: unknown[]) => listApiKeys(...a),
    createApiKey: (...a: unknown[]) => createApiKey(...a),
    revokeApiKey: (...a: unknown[]) => revokeApiKey(...a),
  };
});

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ApiKeysPanel />
    </QueryClientProvider>
  );
}

const KEY = {
  id: 'k1',
  name: 'Claude Code',
  key_prefix: 'rdx_abc12345',
  created_at: '2026-08-01T00:00:00Z',
  last_used_at: null,
  revoked_at: null,
};

describe('ApiKeysPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listApiKeys.mockResolvedValue([]);
  });

  it('invites a first key when there are none', async () => {
    renderPanel();
    expect(await screen.findByText(/no keys yet/i)).toBeTruthy();
  });

  it('shows the plaintext exactly once, after creating', async () => {
    createApiKey.mockResolvedValue({
      id: 'k2',
      name: 'Agent',
      key_prefix: 'rdx_zzz',
      created_at: '2026-08-20T00:00:00Z',
      key: 'rdx_the_only_copy_of_this_secret',
    });
    renderPanel();

    await userEvent.type(await screen.findByLabelText(/name/i), 'Agent');
    await userEvent.click(screen.getByRole('button', { name: /create key/i }));

    expect(await screen.findByText('rdx_the_only_copy_of_this_secret')).toBeTruthy();
    // The warning is the whole point: the server keeps a hash, so a key that
    // is not copied here is unrecoverable.
    expect(screen.getByText(/only time it will be shown/i)).toBeTruthy();
  });

  it('hides the plaintext once acknowledged', async () => {
    createApiKey.mockResolvedValue({
      id: 'k2',
      name: 'Agent',
      key_prefix: 'rdx_zzz',
      created_at: '2026-08-20T00:00:00Z',
      key: 'rdx_secret_value',
    });
    renderPanel();

    await userEvent.click(await screen.findByRole('button', { name: /create key/i }));
    expect(await screen.findByText('rdx_secret_value')).toBeTruthy();

    await userEvent.click(screen.getByRole('button', { name: /saved it/i }));
    await waitFor(() => expect(screen.queryByText('rdx_secret_value')).toBeNull());
  });

  it('never renders a full key for an existing one — only its prefix', async () => {
    listApiKeys.mockResolvedValue([KEY]);
    renderPanel();

    expect(await screen.findByText('Claude Code')).toBeTruthy();
    expect(screen.getByText(/rdx_abc12345/)).toBeTruthy();
    expect(screen.getByText(/never used/i)).toBeTruthy();
  });

  it('requires confirmation before deleting, and says what breaks', async () => {
    listApiKeys.mockResolvedValue([KEY]);
    renderPanel();

    await userEvent.click(await screen.findByRole('button', { name: /delete/i }));

    expect(await screen.findByText(/stops working immediately/i)).toBeTruthy();
    expect(revokeApiKey).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole('button', { name: /delete key/i }));
    await waitFor(() => expect(revokeApiKey).toHaveBeenCalledWith('k1'));
  });

  it('cancelling the confirmation deletes nothing', async () => {
    listApiKeys.mockResolvedValue([KEY]);
    renderPanel();

    await userEvent.click(await screen.findByRole('button', { name: /delete/i }));
    await userEvent.click(await screen.findByRole('button', { name: /cancel/i }));

    expect(revokeApiKey).not.toHaveBeenCalled();
  });

  it('offers no delete action on an already-revoked key', async () => {
    listApiKeys.mockResolvedValue([{ ...KEY, revoked_at: '2026-08-10T00:00:00Z' }]);
    renderPanel();

    expect(await screen.findByText(/revoked/i)).toBeTruthy();
    expect(screen.queryByRole('button', { name: /^delete$/i })).toBeNull();
  });

  it('falls back to a default name when none is given', async () => {
    createApiKey.mockResolvedValue({
      id: 'k3',
      name: 'API key',
      key_prefix: 'rdx_q',
      created_at: '2026-08-20T00:00:00Z',
      key: 'rdx_x',
    });
    renderPanel();

    await userEvent.click(await screen.findByRole('button', { name: /create key/i }));
    await waitFor(() => expect(createApiKey).toHaveBeenCalledWith('API key'));
  });
});
