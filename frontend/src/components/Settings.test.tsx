import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const mockToastSuccess = vi.fn();
const mockToastError = vi.fn();
const mockToastInfo = vi.fn();
vi.mock('sonner', () => ({
  toast: {
    success: (...args: unknown[]) => mockToastSuccess(...args),
    error: (...args: unknown[]) => mockToastError(...args),
    info: (...args: unknown[]) => mockToastInfo(...args),
  },
}));

const mockRefreshSession = vi.fn();
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({
    user: {
      id: 'user-1',
      email: 'jane@example.com',
      full_name: 'Jane Doe',
      plan: 'free',
    },
    loading: false,
    refreshSession: mockRefreshSession,
  }),
}));

const mockNavigate = vi.fn();
const mockSearchParams = new URLSearchParams();
const mockSetSearchParams = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    useSearchParams: () => [mockSearchParams, mockSetSearchParams],
  };
});

vi.mock('./DashboardLayout', () => ({
  DashboardLayout: ({ children, title }: { children: React.ReactNode; title: string }) => (
    <div data-testid="dashboard-layout" data-title={title}>{children}</div>
  ),
}));

const mockGetBillingStatus = vi.fn();
const mockCreateAgencyCheckout = vi.fn();
const mockCreatePortalSession = vi.fn();
vi.mock('../api/billing', () => ({
  getBillingStatus: (...args: unknown[]) => mockGetBillingStatus(...args),
  createAgencyCheckout: (...args: unknown[]) => mockCreateAgencyCheckout(...args),
  createPortalSession: (...args: unknown[]) => mockCreatePortalSession(...args),
}));

const mockGetEmailPreferences = vi.fn();
const mockUpdateEmailPreference = vi.fn();
vi.mock('../api/email', () => ({
  getEmailPreferences: (...args: unknown[]) => mockGetEmailPreferences(...args),
  updateEmailPreference: (...args: unknown[]) => mockUpdateEmailPreference(...args),
}));

const mockUpdateUserProfile = vi.fn();
vi.mock('../api/user', () => ({
  updateUserProfile: (...args: unknown[]) => mockUpdateUserProfile(...args),
}));

import { Settings } from './Settings';

function renderSettings(initialTab = 'profile') {
  mockSearchParams.set('tab', initialTab);
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  render(
    <QueryClientProvider client={queryClient}>
      <Settings />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockSearchParams.delete('tab');
  mockSearchParams.delete('status');
  localStorage.clear();

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
    checkout_session_id: 'cs_test_123',
  });
  mockCreatePortalSession.mockResolvedValue('https://billing.example.com');

  mockGetEmailPreferences.mockResolvedValue([
    { email_type: 'mapping_complete', opted_out: false },
    { email_type: 'mapping_failed', opted_out: false },
  ]);
  mockUpdateEmailPreference.mockResolvedValue(undefined);

  mockUpdateUserProfile.mockResolvedValue({ success: true, profile: { full_name: 'Jane Doe' } });
  mockRefreshSession.mockResolvedValue(undefined);

});

describe('Settings', () => {
  it('renders profile data and saves profile changes', async () => {
    const user = userEvent.setup();
    renderSettings('profile');

    expect(await screen.findByText('Jane Doe')).toBeInTheDocument();
    expect(screen.getByDisplayValue('jane@example.com')).toBeDisabled();

    const input = screen.getByLabelText('Full Name');
    await user.clear(input);
    await user.type(input, 'Jane Smith');
    await user.click(screen.getByRole('button', { name: 'Save Changes' }));

    await waitFor(() => {
      expect(mockUpdateUserProfile).toHaveBeenCalledWith({ full_name: 'Jane Smith' });
      expect(mockRefreshSession).toHaveBeenCalled();
    });
  });

  it('saves default settings to localStorage', async () => {
    const user = userEvent.setup();
    renderSettings('defaults');

    await user.click(screen.getByRole('button', { name: 'Save Defaults' }));

    expect(localStorage.getItem('redirx_default_export_format')).toBeTruthy();
    expect(localStorage.getItem('redirx_default_url_format')).toBeTruthy();
    expect(mockToastSuccess).toHaveBeenCalledWith('Default settings saved successfully.');
  });

  it('saves notification preferences', async () => {
    const user = userEvent.setup();
    renderSettings('notifications');

    await user.click(await screen.findByRole('button', { name: 'Save Notification Preferences' }));

    await waitFor(() => {
      expect(mockUpdateEmailPreference).toHaveBeenCalledTimes(2);
    });
  });

  it('starts agency checkout from subscription tab', async () => {
    const user = userEvent.setup();
    renderSettings('subscription');

    await user.click(await screen.findByRole('button', { name: 'Start Agency Checkout' }));

    await waitFor(() => {
      expect(mockCreateAgencyCheckout).toHaveBeenCalled();
      expect(mockToastError).toHaveBeenCalledWith('Checkout URL was not returned by billing service.');
    });
  });

  it('does not render legacy billing terminology in subscription UI', async () => {
    renderSettings('subscription');
    await screen.findByText('Agency Plan');

    const copy = (screen.getByTestId('dashboard-layout').textContent || '').toLowerCase();
    for (const legacyTerm of ['credits', 'starter', 'growth', 'scale']) {
      expect(copy).not.toContain(legacyTerm);
    }
  });
});
