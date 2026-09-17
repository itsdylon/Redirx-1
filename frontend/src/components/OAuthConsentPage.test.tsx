import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { OAuthConsentPage } from './OAuthConsentPage';

const {
  mockGetAuthorizationDetails,
  mockApproveAuthorization,
  mockDenyAuthorization,
  mockUseAuth,
  mockNavigate,
} = vi.hoisted(() => ({
  mockGetAuthorizationDetails: vi.fn(),
  mockApproveAuthorization: vi.fn(),
  mockDenyAuthorization: vi.fn(),
  mockUseAuth: vi.fn(),
  mockNavigate: vi.fn(),
}));

vi.mock('../contexts/AuthContext', () => ({ useAuth: () => mockUseAuth() }));
vi.mock('../lib/supabase', () => ({
  supabase: {
    auth: {
      oauth: {
        getAuthorizationDetails: mockGetAuthorizationDetails,
        approveAuthorization: mockApproveAuthorization,
        denyAuthorization: mockDenyAuthorization,
      },
    },
  },
}));
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

const details = {
  authorization_id: 'request-1',
  client: { id: 'client-1', name: 'MCP Studio', uri: 'https://mcp.example.com', logo_uri: '' },
  user: { id: 'user-1', email: 'user@example.com' },
  scope: 'openid profile email',
};

function renderConsent(path = '/oauth/consent?authorization_id=request-1') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/oauth/consent" element={<OAuthConsentPage />} />
      </Routes>
    </MemoryRouter>
  );
}

describe('OAuthConsentPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseAuth.mockReturnValue({ user: { id: 'user-1', email: 'user@example.com' } });
    mockGetAuthorizationDetails.mockResolvedValue({ data: details, error: null });
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...window.location, origin: 'http://localhost', pathname: '/oauth/consent', assign: vi.fn() },
    });
  });

  it('loads requester and scopes, then approves with Supabase', async () => {
    mockApproveAuthorization.mockResolvedValue({ data: { redirect_url: 'https://mcp.example.com/callback?code=ok' }, error: null });
    const user = userEvent.setup();
    renderConsent();

    expect(await screen.findByRole('heading', { name: 'Connect MCP Studio' })).toBeInTheDocument();
    expect(screen.getByText(/openid/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Approve connection' }));

    await waitFor(() => expect(mockApproveAuthorization).toHaveBeenCalledWith('request-1', { skipBrowserRedirect: true }));
    expect(window.location.assign).toHaveBeenCalledWith('https://mcp.example.com/callback?code=ok');
  });

  it('denies and returns the Supabase response to the client', async () => {
    mockDenyAuthorization.mockResolvedValue({ data: { redirect_url: 'https://mcp.example.com/callback?error=access_denied' }, error: null });
    const user = userEvent.setup();
    renderConsent();
    await screen.findByRole('heading', { name: 'Connect MCP Studio' });
    await user.click(screen.getByRole('button', { name: 'Decline' }));

    await waitFor(() => expect(mockDenyAuthorization).toHaveBeenCalledWith('request-1', { skipBrowserRedirect: true }));
    expect(window.location.assign).toHaveBeenCalledWith('https://mcp.example.com/callback?error=access_denied');
  });

  it('immediately returns an already-consented request', async () => {
    const redirectUrl = 'https://mcp.example.com/callback?code=existing';
    mockGetAuthorizationDetails.mockResolvedValue({ data: { ...details, redirect_url: redirectUrl }, error: null });
    renderConsent();
    await waitFor(() => expect(window.location.assign).toHaveBeenCalledWith(redirectUrl));
  });

  it('shows expired and provider errors without offering a false approval', async () => {
    mockGetAuthorizationDetails.mockResolvedValue({ data: null, error: new Error('Authorization request expired') });
    renderConsent();
    expect(await screen.findByRole('heading', { name: 'Authorization request expired' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve connection' })).not.toBeInTheDocument();
  });

  it('preserves the full consent URI when a visitor needs to sign in', async () => {
    mockUseAuth.mockReturnValue({ user: null });
    renderConsent('/oauth/consent?authorization_id=request-1&state=abc');
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith(
      '/login?redirect=%2Foauth%2Fconsent%3Fauthorization_id%3Drequest-1%26state%3Dabc&source=oauth-consent',
      { replace: true },
    ));
    expect(localStorage.getItem('auth_redirect')).toBe('/oauth/consent?authorization_id=request-1&state=abc');
  });
});
