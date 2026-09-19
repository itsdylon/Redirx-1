import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import App from './App';

const mockUseAuth = vi.fn();
const mockReviewInterface = vi.fn(({ layoutVariant }: { layoutVariant?: string }) => (
  <div>Review Page ({layoutVariant || 'dashboard'})</div>
));

vi.mock('./contexts/AuthContext', () => ({
  useAuth: () => mockUseAuth(),
}));

vi.mock('./components/LoginPage', () => ({ LoginPage: () => <div>Login Page</div> }));
vi.mock('./components/SignupPage', () => ({ SignupPage: () => <div>Signup Page</div> }));
vi.mock('./components/AuthCallback', () => ({ AuthCallback: () => <div>Auth Callback</div> }));
vi.mock('./components/OAuthConsentPage', () => ({ OAuthConsentPage: () => <div>OAuth Consent Page</div> }));
vi.mock('./components/Dashboard', () => ({ Dashboard: () => <div>Dashboard Page</div> }));
vi.mock('./components/AllProjects', () => ({ AllProjects: () => <div>Projects Page</div> }));
vi.mock('./components/UploadPage', () => ({ UploadPage: () => <div>Upload Page</div> }));
vi.mock('./components/ReviewInterface', () => ({ ReviewInterface: (props: { layoutVariant?: string }) => mockReviewInterface(props) }));
vi.mock('./components/AccountPage', () => ({ AccountPage: () => <div>Account Page</div> }));
vi.mock('./components/Settings', () => ({ Settings: () => <div>Settings Page</div> }));
vi.mock('./components/PricingPage', () => ({ PricingPage: () => <div>Pricing Page</div> }));
vi.mock('./components/DemoPage', () => ({ DemoPage: () => <div>Demo Page</div> }));
vi.mock('./components/QuickMatchLandingPage', () => ({
  QuickMatchLandingPage: () => <div>Quick Match Route</div>,
}));
vi.mock('./components/ui/sonner', () => ({ Toaster: () => null }));

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>
  );
}

describe('App routing', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockReviewInterface.mockClear();
  });

  it('redirects unauthenticated root traffic to quick-match landing', async () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });
    renderAt('/');
    expect(await screen.findByText('Quick Match Route')).toBeInTheDocument();
  });

  it('routes authenticated free users to /quick-match from root', async () => {
    mockUseAuth.mockReturnValue({
      loading: false,
      user: { id: 'u1', email: 'user@example.com', plan: 'free' },
    });
    renderAt('/');
    expect(await screen.findByText('Quick Match Route')).toBeInTheDocument();
  });

  it('routes authenticated agency users to dashboard from root', async () => {
    mockUseAuth.mockReturnValue({
      loading: false,
      user: { id: 'u1', email: 'user@example.com', plan: 'agency' },
    });
    renderAt('/');
    expect(await screen.findByText('Dashboard Page')).toBeInTheDocument();
  });

  it('routes /quick-match for unauthenticated users', async () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });
    renderAt('/quick-match');
    expect(await screen.findByText('Quick Match Route')).toBeInTheDocument();
  });

  it('routes OAuth consent without sending it through the normal home redirect', async () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });
    renderAt('/oauth/consent?authorization_id=request-1');
    expect(await screen.findByText('OAuth Consent Page')).toBeInTheDocument();
  });

  it.each(['/login', '/signup'])('preserves consent for a signed-in visitor to %s', async (path) => {
    mockUseAuth.mockReturnValue({ user: { id: 'user-1', plan: 'free' }, loading: false });
    renderAt(`${path}?redirect=${encodeURIComponent('/oauth/consent?authorization_id=request-1')}`);
    expect(await screen.findByText('OAuth Consent Page')).toBeInTheDocument();
  });

  it('routes /quick-match for authenticated tool users', async () => {
    mockUseAuth.mockReturnValue({
      loading: false,
      user: { id: 'u1', email: 'user@example.com', plan: 'free' },
    });
    renderAt('/quick-match');
    expect(await screen.findByText('Quick Match Route')).toBeInTheDocument();
  });

  it('redirects enterprise users away from /quick-match', async () => {
    mockUseAuth.mockReturnValue({
      loading: false,
      user: { id: 'u1', email: 'user@example.com', plan: 'agency' },
    });
    renderAt('/quick-match');
    expect(await screen.findByText('Upload Page')).toBeInTheDocument();
  });

  it('redirects non-agency users away from dashboard', async () => {
    mockUseAuth.mockReturnValue({
      loading: false,
      user: { id: 'u1', email: 'user@example.com', plan: 'free' },
    });
    renderAt('/dashboard');
    expect(await screen.findByText('Quick Match Route')).toBeInTheDocument();
  });

  it('does not expose /start route', () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });
    const { container } = renderAt('/start');
    expect(container).toBeEmptyDOMElement();
  });

  it('redirects tool users away from /upload', async () => {
    mockUseAuth.mockReturnValue({
      loading: false,
      user: { id: 'u1', email: 'tool@example.com', plan: 'free' },
    });
    renderAt('/upload');
    expect(await screen.findByText('Quick Match Route')).toBeInTheDocument();
  });

  it('uses tool layout for tool-user review routes', async () => {
    mockUseAuth.mockReturnValue({
      loading: false,
      user: { id: 'u1', email: 'tool@example.com', plan: 'free' },
    });
    renderAt('/review/session-1');
    expect(await screen.findByText('Review Page (tool)')).toBeInTheDocument();
  });

  it('uses dashboard layout for enterprise review routes', async () => {
    mockUseAuth.mockReturnValue({
      loading: false,
      user: { id: 'u1', email: 'agency@example.com', plan: 'agency' },
    });
    renderAt('/review/session-1');
    expect(await screen.findByText('Review Page (dashboard)')).toBeInTheDocument();
  });

  it('redirects tool users from /pricing without source session id', async () => {
    mockUseAuth.mockReturnValue({
      loading: false,
      user: { id: 'u1', email: 'tool@example.com', plan: 'free' },
    });
    renderAt('/pricing');
    expect(await screen.findByText('Quick Match Route')).toBeInTheDocument();
  });

  it('allows tool users on /pricing with source session id', async () => {
    mockUseAuth.mockReturnValue({
      loading: false,
      user: { id: 'u1', email: 'tool@example.com', plan: 'free' },
    });
    renderAt('/pricing?source_session_id=session-1');
    expect(await screen.findByText('Pricing Page')).toBeInTheDocument();
  });

  it('redirects tool users away from /settings and /account', async () => {
    mockUseAuth.mockReturnValue({
      loading: false,
      user: { id: 'u1', email: 'tool@example.com', plan: 'free' },
    });
    const settingsRender = renderAt('/settings');
    expect(await screen.findByText('Quick Match Route')).toBeInTheDocument();
    settingsRender.unmount();

    renderAt('/account');
    expect(await screen.findByText('Quick Match Route')).toBeInTheDocument();
  });
});

function CurrentRoute() {
  const location = useLocation();
  return <output aria-label="Current route">{location.pathname + location.search + location.hash}</output>;
}

describe('unauthenticated pivot deep links', () => {
  it.each([
    '/companion?tab=history',
    '/migrations/migration-1?monitoring_id=monitor-1&checkout_id=oneoff-1',
    '/billing/subscriptions/return?checkout_id=subscription-1',
  ])('preserves %s through the actual App login redirect', async target => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });
    render(<MemoryRouter initialEntries={[target]}><App /><CurrentRoute /></MemoryRouter>);
    expect(await screen.findByText('Login Page')).toBeInTheDocument();
    const current = new URL(screen.getByLabelText('Current route').textContent!, 'https://fixture.invalid');
    expect(current.pathname).toBe('/login');
    expect(current.searchParams.get('redirect')).toBe(target);
  });
});
