import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal('ResizeObserver', ResizeObserverMock);

const mockNavigate = vi.fn();
const mockSearchParams = new URLSearchParams();
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    useSearchParams: () => [mockSearchParams, vi.fn()],
  };
});

vi.mock('./DashboardLayout', () => ({
  DashboardLayout: ({ children, title }: { children: React.ReactNode; title: string }) => (
    <div data-testid="dashboard-layout" data-title={title}>{children}</div>
  ),
}));
vi.mock('./ToolLayout', () => ({
  ToolLayout: ({ children, title }: { children: React.ReactNode; title: string }) => (
    <div data-testid="tool-layout" data-title={title}>{children}</div>
  ),
}));

const mockUseAuth = vi.fn();
const mockCapture = vi.fn();
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => mockUseAuth(),
}));

vi.mock('@posthog/react', () => ({
  usePostHog: () => ({
    capture: mockCapture,
  }),
}));

const mockGetPricingEstimate = vi.fn();
const mockCreateProjectQuote = vi.fn();
const mockCreateProjectCheckout = vi.fn();
const mockCreateAgencyCheckout = vi.fn();
const mockGetBillingStatus = vi.fn();
vi.mock('../api/billing', () => ({
  getPricingEstimate: (...args: unknown[]) => mockGetPricingEstimate(...args),
  createProjectQuote: (...args: unknown[]) => mockCreateProjectQuote(...args),
  createProjectCheckout: (...args: unknown[]) => mockCreateProjectCheckout(...args),
  createAgencyCheckout: (...args: unknown[]) => mockCreateAgencyCheckout(...args),
  getBillingStatus: (...args: unknown[]) => mockGetBillingStatus(...args),
}));

import { PricingPage } from './PricingPage';

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  render(
    <QueryClientProvider client={queryClient}>
      <PricingPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockUseAuth.mockReturnValue({
    user: { id: 'agency-1', email: 'agency@example.com', plan: 'agency' },
  });
  mockSearchParams.forEach((_v, k) => mockSearchParams.delete(k));

  mockGetPricingEstimate.mockResolvedValue({
    pricing_version: 'v1_2026_03',
    currency: 'usd',
    contact_required: false,
    billable_pages: 5000,
    line_items: [
      { from_page: 1, to_page: 500, pages: 500, unit_price_usd: '0.100', amount_usd: '50.00', amount_cents: 5000 },
      { from_page: 501, to_page: 2000, pages: 1500, unit_price_usd: '0.050', amount_usd: '75.00', amount_cents: 7500 },
      { from_page: 2001, to_page: 5000, pages: 3000, unit_price_usd: '0.035', amount_usd: '105.00', amount_cents: 10500 },
    ],
    subtotal_usd: '230.00',
    subtotal_cents: 23000,
    effective_rate_usd: '0.046',
  });

  mockGetBillingStatus.mockResolvedValue({
    plan: 'free',
    pricing_version: 'v1_2026_03',
    manage_portal_available: false,
    agency: {
      has_subscription: false,
      subscription_id: null,
      status: null,
      current_period_start: null,
      current_period_end: null,
      cancel_at_period_end: false,
      usage_pages: 0,
      overage_enabled: false,
    },
  });

  mockCreateAgencyCheckout.mockResolvedValue({
    success: true,
    url: '',
    checkout_session_id: 'cs_agency',
  });

  mockCreateProjectCheckout.mockResolvedValue({
    success: true,
    already_paid: true,
    quote_id: 'quote-1',
    deep_session_id: '22222222-2222-2222-2222-222222222222',
  });
});

describe('PricingPage', () => {
  it('renders estimator mode without source_session_id', async () => {
    renderPage();

    expect(await screen.findByText('Project Pricing Estimator')).toBeInTheDocument();
    expect(await screen.findByText('$230.00')).toBeInTheDocument();
    expect(screen.getByText('Agency Plan')).toBeInTheDocument();

    const copy = (screen.getByTestId('dashboard-layout').textContent || '').toLowerCase();
    for (const legacyTerm of ['credits', 'starter', 'growth', 'scale']) {
      expect(copy).not.toContain(legacyTerm);
    }
  });

  it('renders quote mode and starts project checkout', async () => {
    mockSearchParams.set('source_session_id', '11111111-1111-1111-1111-111111111111');
    mockCreateProjectQuote.mockResolvedValue({
      id: 'quote-1',
      source_session_id: '11111111-1111-1111-1111-111111111111',
      user_id: 'user-1',
      old_url_count: 5000,
      new_url_count: 4500,
      billable_pages: 5000,
      pricing_version: 'v1_2026_03',
      currency: 'usd',
      line_items: [],
      subtotal_cents: 23000,
      status: 'draft',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    });

    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText('Content Match Pricing For This Project')).toBeInTheDocument();
    const purchaseButton = await screen.findByRole('button', { name: /Purchase Content Match/i });
    expect(purchaseButton).toBeDisabled();

    const checklist = screen.getByRole('checkbox', {
      name: /disabled or whitelisted protections for this content match run/i,
    });
    await user.click(checklist);
    expect(purchaseButton).not.toBeDisabled();

    await user.click(purchaseButton);

    await waitFor(() => {
      expect(mockCreateProjectCheckout).toHaveBeenCalled();
      expect(mockNavigate).toHaveBeenCalledWith('/review/22222222-2222-2222-2222-222222222222');
    });
    expect(mockCapture).toHaveBeenCalledWith(
      'project_checkout_started',
      expect.objectContaining({
        source_session_id: '11111111-1111-1111-1111-111111111111',
      }),
    );
  });

  it('renders tool unlock-only mode for non-enterprise users', async () => {
    mockUseAuth.mockReturnValue({
      user: { id: 'tool-1', email: 'tool@example.com', plan: 'free' },
    });
    mockSearchParams.set('source_session_id', '11111111-1111-1111-1111-111111111111');
    mockCreateProjectQuote.mockResolvedValue({
      id: 'quote-1',
      source_session_id: '11111111-1111-1111-1111-111111111111',
      user_id: 'tool-1',
      old_url_count: 1200,
      new_url_count: 1100,
      billable_pages: 1200,
      pricing_version: 'v1_2026_03',
      currency: 'usd',
      line_items: [],
      subtotal_cents: 9000,
      status: 'draft',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    });

    renderPage();

    expect(await screen.findByTestId('tool-layout')).toBeInTheDocument();
    expect(await screen.findByText('Content Match Pricing For This Project')).toBeInTheDocument();
    expect(screen.getByText('Agency Plan')).toBeInTheDocument();
  });

  it('renders estimator mode for logged-in tool users without source session id', async () => {
    mockUseAuth.mockReturnValue({
      user: { id: 'tool-1', email: 'tool@example.com', plan: 'free' },
    });

    renderPage();

    expect(await screen.findByText('Project Pricing Estimator')).toBeInTheDocument();
    expect(screen.getByTestId('tool-layout')).toBeInTheDocument();
    expect(screen.getByText('Agency Plan')).toBeInTheDocument();
  });

  it('tracks cancelled project checkout return state from query param', async () => {
    mockSearchParams.set('source_session_id', '11111111-1111-1111-1111-111111111111');
    mockSearchParams.set('status', 'cancelled');
    mockCreateProjectQuote.mockResolvedValue({
      id: 'quote-1',
      source_session_id: '11111111-1111-1111-1111-111111111111',
      user_id: 'user-1',
      old_url_count: 5000,
      new_url_count: 4500,
      billable_pages: 5000,
      pricing_version: 'v1_2026_03',
      currency: 'usd',
      line_items: [],
      subtotal_cents: 23000,
      status: 'draft',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    });

    renderPage();

    expect(await screen.findByText('Content Match Pricing For This Project')).toBeInTheDocument();
    expect(mockCapture).toHaveBeenCalledWith(
      'project_checkout_returned_cancelled',
      expect.objectContaining({
        source_session_id: '11111111-1111-1111-1111-111111111111',
        return_path: 'pricing',
      }),
    );
  });
});
