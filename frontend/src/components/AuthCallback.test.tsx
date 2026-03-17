import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AuthCallback } from './AuthCallback';

const mockCompleteOAuthCallback = vi.fn();
const mockCapture = vi.fn();

vi.mock('@posthog/react', () => ({
  usePostHog: () => ({
    capture: mockCapture,
  }),
}));

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({
    user: null,
    completeOAuthCallback: mockCompleteOAuthCallback,
  }),
}));

function renderCallback(initialPath = '/auth/callback') {
  window.history.pushState({}, '', initialPath);
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/auth/callback" element={<AuthCallback />} />
        <Route path="/quick-match" element={<div>Quick Match Route</div>} />
        <Route path="/login" element={<div>Login Route</div>} />
      </Routes>
    </MemoryRouter>
  );
}

describe('AuthCallback', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('completes callback and navigates to resolved redirect', async () => {
    mockCompleteOAuthCallback.mockResolvedValueOnce('/quick-match');
    renderCallback();

    await waitFor(() => {
      expect(mockCompleteOAuthCallback).toHaveBeenCalledTimes(1);
    });
    expect(screen.queryByText('Sign-in Failed')).not.toBeInTheDocument();
    expect(mockCapture).toHaveBeenCalledWith(
      'auth_callback_success',
      expect.objectContaining({
        authenticated: true,
        redirect: '/quick-match',
      }),
    );
  });

  it('renders provider error returned in callback URL', async () => {
    renderCallback('/auth/callback?error=access_denied&error_description=The+user+denied+access');

    expect(await screen.findByText('Sign-in Failed')).toBeInTheDocument();
    expect(screen.getByText('The user denied access')).toBeInTheDocument();
    expect(mockCompleteOAuthCallback).not.toHaveBeenCalled();
    expect(mockCapture).toHaveBeenCalledWith(
      'auth_callback_failed',
      expect.objectContaining({
        authenticated: false,
      }),
    );
  });

  it('renders callback completion errors', async () => {
    mockCompleteOAuthCallback.mockRejectedValueOnce(new Error('Unable to establish session.'));
    renderCallback();

    expect(await screen.findByText('Sign-in Failed')).toBeInTheDocument();
    expect(screen.getByText('Unable to establish session.')).toBeInTheDocument();
    expect(mockCapture).toHaveBeenCalledWith(
      'auth_callback_failed',
      expect.objectContaining({
        error_message: 'Unable to establish session.',
      }),
    );
  });
});
