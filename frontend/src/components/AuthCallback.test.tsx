import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AuthCallback } from './AuthCallback';

const mockCompleteOAuthCallback = vi.fn();

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({
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
  });

  it('completes callback and navigates to resolved redirect', async () => {
    mockCompleteOAuthCallback.mockResolvedValueOnce('/quick-match');
    renderCallback();

    expect(await screen.findByText('Quick Match Route')).toBeInTheDocument();
    expect(mockCompleteOAuthCallback).toHaveBeenCalledTimes(1);
  });

  it('renders provider error returned in callback URL', async () => {
    renderCallback('/auth/callback?error=access_denied&error_description=The+user+denied+access');

    expect(await screen.findByText('Sign-in Failed')).toBeInTheDocument();
    expect(screen.getByText('The user denied access')).toBeInTheDocument();
    expect(mockCompleteOAuthCallback).not.toHaveBeenCalled();
  });

  it('renders callback completion errors', async () => {
    mockCompleteOAuthCallback.mockRejectedValueOnce(new Error('Unable to establish session.'));
    renderCallback();

    expect(await screen.findByText('Sign-in Failed')).toBeInTheDocument();
    expect(screen.getByText('Unable to establish session.')).toBeInTheDocument();
  });
});
