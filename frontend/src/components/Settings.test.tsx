import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// ---------------------------------------------------------------------------
// Mocks — must come before importing the component
// ---------------------------------------------------------------------------

// Global fetch mock
const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

// Toast mock
const mockToastSuccess = vi.fn();
const mockToastError = vi.fn();
vi.mock('sonner', () => ({
  toast: {
    success: (...args: unknown[]) => mockToastSuccess(...args),
    error: (...args: unknown[]) => mockToastError(...args),
    info: vi.fn(),
  },
}));

// Auth context mock
const mockRefreshSession = vi.fn();
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({
    user: {
      id: 'user-1',
      email: 'jane@example.com',
      full_name: 'Jane Doe',
      plan: 'launch',
    },
    loading: false,
    refreshSession: mockRefreshSession,
  }),
}));

// Onboarding context mock (Settings depends on replay tutorial actions)
const mockResetOnboarding = vi.fn();
const mockStartOnboarding = vi.fn();
const mockSetEntryModalOpen = vi.fn();
vi.mock('../contexts/OnboardingContext', () => ({
  useOnboarding: () => ({
    resetOnboarding: mockResetOnboarding,
    startOnboarding: mockStartOnboarding,
    setEntryModalOpen: mockSetEntryModalOpen,
  }),
}));

// React Router mock
const mockSearchParams = new URLSearchParams();
const mockSetSearchParams = vi.fn();
vi.mock('react-router-dom', () => ({
  useSearchParams: () => [mockSearchParams, mockSetSearchParams],
  useNavigate: () => vi.fn(),
}));

// DashboardLayout — lightweight pass-through
vi.mock('./DashboardLayout', () => ({
  DashboardLayout: ({ children, title }: { children: React.ReactNode; title: string }) => (
    <div data-testid="dashboard-layout" data-title={title}>
      {children}
    </div>
  ),
}));

// Billing API — prevent real calls; subscription tab is out of scope
vi.mock('../api/billing', () => ({
  getSubscriptionStatus: vi.fn().mockResolvedValue({ plan: 'launch', credits_limit: 0, credits_used: 0, credits_remaining: 0, lifetime_credits_total: 0, lifetime_credits_used: 0, lifetime_credits_remaining: 0, quick_match_limit: 5, quick_match_used: 0, quick_match_unlimited: false, max_concurrent_projects: 1, has_subscription: false, is_lifetime: false }),
  getPlans: vi.fn().mockResolvedValue([]),
  createCheckoutSession: vi.fn(),
  createPortalSession: vi.fn(),
}));

// Email preferences API mock
const mockGetEmailPreferences = vi.fn();
const mockUpdateEmailPreference = vi.fn();
vi.mock('../api/email', () => ({
  getEmailPreferences: (...args: unknown[]) => mockGetEmailPreferences(...args),
  updateEmailPreference: (...args: unknown[]) => mockUpdateEmailPreference(...args),
}));

import { Settings } from './Settings';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  });
}

/** Render Settings and wait for initial async effects to settle. */
async function renderSettings(tab?: string) {
  if (tab) {
    mockSearchParams.set('tab', tab);
  }
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  const result = render(
    <QueryClientProvider client={queryClient}>
      <Settings />
    </QueryClientProvider>
  );
  // Wait for billing + email prefs fetches to complete
  await waitFor(() => {
    // The component should have finished at least one render cycle
    expect(screen.getByTestId('dashboard-layout')).toBeInTheDocument();
  });
  return result;
}

// ---------------------------------------------------------------------------
// Setup / Teardown
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  mockSearchParams.delete('tab');
  mockSearchParams.delete('status');

  // Default happy-path mocks
  mockGetEmailPreferences.mockResolvedValue([]);
  mockUpdateEmailPreference.mockResolvedValue(undefined);
  mockRefreshSession.mockResolvedValue(undefined);

  // Profile save endpoint (default success)
  mockFetch.mockImplementation((url: string) => {
    if (typeof url === 'string' && url.includes('/api/user/profile')) {
      return jsonResponse({ success: true });
    }
    // Fallback for any other fetch (shouldn't happen in these tests)
    return jsonResponse({}, 404);
  });
});

afterEach(() => {
  localStorage.clear();
  mockSearchParams.delete('tab');
});

// ===========================================================================
// PROFILE TAB
// ===========================================================================

describe('Settings — Profile Tab', () => {
  it('renders user name and email', async () => {
    await renderSettings('profile');

    expect(screen.getByText('Jane Doe')).toBeInTheDocument();
    expect(screen.getByText('jane@example.com')).toBeInTheDocument();
  });

  it('renders user initials in avatar', async () => {
    await renderSettings('profile');

    expect(screen.getByText('JD')).toBeInTheDocument();
  });

  it('pre-fills the full name input with user name', async () => {
    await renderSettings('profile');

    const input = screen.getByLabelText('Full Name') as HTMLInputElement;
    expect(input.value).toBe('Jane Doe');
  });

  it('email input is disabled and read-only', async () => {
    await renderSettings('profile');

    const input = screen.getByLabelText('Email') as HTMLInputElement;
    expect(input).toBeDisabled();
    expect(input.value).toBe('jane@example.com');
  });

  it('calls PUT /api/user/profile on save and refreshes session', async () => {
    const user = userEvent.setup();
    await renderSettings('profile');

    const input = screen.getByLabelText('Full Name');
    await user.clear(input);
    await user.type(input, 'Jane Smith');

    await user.click(screen.getByRole('button', { name: /Save Changes/i }));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/user/profile'),
        expect.objectContaining({
          method: 'PUT',
          body: JSON.stringify({ full_name: 'Jane Smith' }),
        })
      );
    });

    await waitFor(() => {
      expect(mockRefreshSession).toHaveBeenCalledTimes(1);
    });

    expect(mockToastSuccess).toHaveBeenCalledWith('Profile saved successfully.');
  });

  it('shows error toast when profile save fails', async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/api/user/profile')) {
        return jsonResponse({ error: 'Name too long' }, 400);
      }
      return jsonResponse({}, 404);
    });

    const user = userEvent.setup();
    await renderSettings('profile');

    await user.click(screen.getByRole('button', { name: /Save Changes/i }));

    await waitFor(() => {
      expect(mockToastError).toHaveBeenCalledWith('Name too long');
    });

    // refreshSession should NOT be called on failure
    expect(mockRefreshSession).not.toHaveBeenCalled();
  });

  it('disables save button while saving', async () => {
    // Make fetch hang to observe the loading state
    let resolveProfile!: (value: unknown) => void;
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/api/user/profile')) {
        return new Promise((r) => { resolveProfile = r; });
      }
      return jsonResponse({}, 404);
    });

    const user = userEvent.setup();
    await renderSettings('profile');

    await user.click(screen.getByRole('button', { name: /Save Changes/i }));

    // Button should be disabled while request is in flight
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Save Changes/i })).toBeDisabled();
    });

    // Resolve the request
    resolveProfile({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ success: true }),
    });

    // Button should re-enable after save completes
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Save Changes/i })).not.toBeDisabled();
    });
  });
});

// ===========================================================================
// DEFAULTS TAB
// ===========================================================================

describe('Settings — Defaults Tab', () => {
  it('loads default values when localStorage is empty', async () => {
    const user = userEvent.setup();
    await renderSettings();

    // Switch to Defaults tab
    await user.click(screen.getByRole('tab', { name: /Defaults/i }));

    // Default export format should be "htaccess" (rendered as "Apache .htaccess")
    expect(screen.getByText('Apache .htaccess')).toBeInTheDocument();
    // Default URL format should be "paths" (rendered as "Paths only")
    expect(screen.getByText('Paths only')).toBeInTheDocument();
  });

  it('loads saved values from localStorage', async () => {
    localStorage.setItem('redirx_default_export_format', 'nginx');
    localStorage.setItem('redirx_default_url_format', 'full');
    localStorage.setItem('redirx_default_confidence_high', 'false');
    localStorage.setItem('redirx_default_confidence_medium', 'true');
    localStorage.setItem('redirx_default_confidence_low', 'true');

    const user = userEvent.setup();
    await renderSettings();
    await user.click(screen.getByRole('tab', { name: /Defaults/i }));

    expect(screen.getByText('Nginx map')).toBeInTheDocument();
    expect(screen.getByText('Full URLs')).toBeInTheDocument();
  });

  it('saves values to localStorage on Save Defaults click', async () => {
    const user = userEvent.setup();
    await renderSettings();
    await user.click(screen.getByRole('tab', { name: /Defaults/i }));

    await user.click(screen.getByRole('button', { name: /Save Defaults/i }));

    expect(localStorage.getItem('redirx_default_export_format')).toBe('htaccess');
    expect(localStorage.getItem('redirx_default_url_format')).toBe('paths');
    expect(localStorage.getItem('redirx_default_confidence_high')).toBe('true');
    expect(localStorage.getItem('redirx_default_confidence_medium')).toBe('true');
    expect(localStorage.getItem('redirx_default_confidence_low')).toBe('false');
    expect(mockToastSuccess).toHaveBeenCalledWith('Default settings saved successfully.');
  });

  it('does not render auto-approve toggle', async () => {
    const user = userEvent.setup();
    await renderSettings();
    await user.click(screen.getByRole('tab', { name: /Defaults/i }));

    expect(screen.queryByText('Auto-approve High Confidence')).not.toBeInTheDocument();
  });

  it('persists changed confidence levels to localStorage', async () => {
    const user = userEvent.setup();
    await renderSettings();
    await user.click(screen.getByRole('tab', { name: /Defaults/i }));

    // Low confidence starts OFF — click the toggle text area to toggle it
    const lowToggle = screen.getByText('Include low-confidence matches by default')
      .closest('.flex')!
      .querySelector('button')!;
    await user.click(lowToggle);

    await user.click(screen.getByRole('button', { name: /Save Defaults/i }));

    expect(localStorage.getItem('redirx_default_confidence_low')).toBe('true');
  });
});

// ===========================================================================
// NOTIFICATIONS TAB
// ===========================================================================

describe('Settings — Notifications Tab', () => {
  it('shows loading state while fetching preferences', async () => {
    // Make getEmailPreferences hang
    mockGetEmailPreferences.mockReturnValue(new Promise(() => {}));

    const user = userEvent.setup();
    await renderSettings();
    await user.click(screen.getByRole('tab', { name: /Notifications/i }));

    expect(screen.getByText('Loading preferences...')).toBeInTheDocument();
  });

  it('renders email toggles after preferences load', async () => {
    mockGetEmailPreferences.mockResolvedValue([
      { email_type: 'mapping_complete', opted_out: false },
      { email_type: 'mapping_failed', opted_out: false },
    ]);

    const user = userEvent.setup();
    await renderSettings();
    await user.click(screen.getByRole('tab', { name: /Notifications/i }));

    await waitFor(() => {
      expect(screen.queryByText('Loading preferences...')).not.toBeInTheDocument();
    });

    expect(screen.getByText('Job completed')).toBeInTheDocument();
    expect(screen.getByText('Job failed')).toBeInTheDocument();
  });

  it('maps opted_out=true to toggle OFF', async () => {
    mockGetEmailPreferences.mockResolvedValue([
      { email_type: 'mapping_complete', opted_out: true },
      { email_type: 'mapping_failed', opted_out: false },
    ]);

    const user = userEvent.setup();
    await renderSettings();
    await user.click(screen.getByRole('tab', { name: /Notifications/i }));

    await waitFor(() => {
      expect(screen.queryByText('Loading preferences...')).not.toBeInTheDocument();
    });

    // Find the switch buttons by their proximity to label text
    const completedToggle = screen.getByText('Job completed')
      .closest('.flex')!
      .querySelector('button[role="switch"]') as HTMLButtonElement;
    const failedToggle = screen.getByText('Job failed')
      .closest('.flex')!
      .querySelector('button[role="switch"]') as HTMLButtonElement;

    // opted_out=true → toggle should be unchecked (aria-checked="false")
    expect(completedToggle.getAttribute('aria-checked')).toBe('false');
    // opted_out=false → toggle should be checked (aria-checked="true")
    expect(failedToggle.getAttribute('aria-checked')).toBe('true');
  });

  it('defaults to opted-in when preferences fetch fails', async () => {
    mockGetEmailPreferences.mockRejectedValue(new Error('Network error'));

    const user = userEvent.setup();
    await renderSettings();
    await user.click(screen.getByRole('tab', { name: /Notifications/i }));

    await waitFor(() => {
      expect(screen.queryByText('Loading preferences...')).not.toBeInTheDocument();
    });

    const completedToggle = screen.getByText('Job completed')
      .closest('.flex')!
      .querySelector('button[role="switch"]') as HTMLButtonElement;
    const failedToggle = screen.getByText('Job failed')
      .closest('.flex')!
      .querySelector('button[role="switch"]') as HTMLButtonElement;

    // Both should default to ON (opted-in)
    expect(completedToggle.getAttribute('aria-checked')).toBe('true');
    expect(failedToggle.getAttribute('aria-checked')).toBe('true');
  });

  it('calls updateEmailPreference for each toggle on save', async () => {
    mockGetEmailPreferences.mockResolvedValue([]);

    const user = userEvent.setup();
    await renderSettings();
    await user.click(screen.getByRole('tab', { name: /Notifications/i }));

    await waitFor(() => {
      expect(screen.queryByText('Loading preferences...')).not.toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: /Save Preferences/i }));

    await waitFor(() => {
      expect(mockUpdateEmailPreference).toHaveBeenCalledTimes(2);
    });

    // Both default to ON → opted_out should be false
    expect(mockUpdateEmailPreference).toHaveBeenCalledWith('mapping_complete', false);
    expect(mockUpdateEmailPreference).toHaveBeenCalledWith('mapping_failed', false);
    expect(mockToastSuccess).toHaveBeenCalledWith(
      'Notification preferences saved successfully.'
    );
  });

  it('sends opted_out=true when user toggles a notification OFF and saves', async () => {
    mockGetEmailPreferences.mockResolvedValue([
      { email_type: 'mapping_complete', opted_out: false },
      { email_type: 'mapping_failed', opted_out: false },
    ]);

    const user = userEvent.setup();
    await renderSettings();
    await user.click(screen.getByRole('tab', { name: /Notifications/i }));

    await waitFor(() => {
      expect(screen.queryByText('Loading preferences...')).not.toBeInTheDocument();
    });

    // Toggle "Job completed" OFF
    const completedToggle = screen.getByText('Job completed')
      .closest('.flex')!
      .querySelector('button[role="switch"]')!;
    await user.click(completedToggle);

    await user.click(screen.getByRole('button', { name: /Save Preferences/i }));

    await waitFor(() => {
      expect(mockUpdateEmailPreference).toHaveBeenCalledWith('mapping_complete', true);
      expect(mockUpdateEmailPreference).toHaveBeenCalledWith('mapping_failed', false);
    });
  });

  it('shows error toast when saving preferences fails', async () => {
    mockGetEmailPreferences.mockResolvedValue([]);
    mockUpdateEmailPreference.mockRejectedValue(new Error('Server error'));

    const user = userEvent.setup();
    await renderSettings();
    await user.click(screen.getByRole('tab', { name: /Notifications/i }));

    await waitFor(() => {
      expect(screen.queryByText('Loading preferences...')).not.toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: /Save Preferences/i }));

    await waitFor(() => {
      expect(mockToastError).toHaveBeenCalledWith('Server error');
    });
  });

  it('disables save button while saving', async () => {
    mockGetEmailPreferences.mockResolvedValue([]);
    const resolvers: Array<() => void> = [];
    mockUpdateEmailPreference.mockImplementation(
      () => new Promise<void>((r) => { resolvers.push(r); })
    );

    const user = userEvent.setup();
    await renderSettings();
    await user.click(screen.getByRole('tab', { name: /Notifications/i }));

    await waitFor(() => {
      expect(screen.queryByText('Loading preferences...')).not.toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: /Save Preferences/i }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Save Preferences/i })).toBeDisabled();
    });

    // Resolve all pending update calls
    resolvers.forEach((r) => r());

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Save Preferences/i })).not.toBeDisabled();
    });
  });

  it('does not render weekly summary toggle', async () => {
    mockGetEmailPreferences.mockResolvedValue([]);
    const user = userEvent.setup();
    await renderSettings();
    await user.click(screen.getByRole('tab', { name: /Notifications/i }));

    await waitFor(() => {
      expect(screen.queryByText('Loading preferences...')).not.toBeInTheDocument();
    });

    expect(screen.queryByText('Weekly summary')).not.toBeInTheDocument();
  });

  it('does not render in-app notifications section', async () => {
    mockGetEmailPreferences.mockResolvedValue([]);
    const user = userEvent.setup();
    await renderSettings();
    await user.click(screen.getByRole('tab', { name: /Notifications/i }));

    await waitFor(() => {
      expect(screen.queryByText('Loading preferences...')).not.toBeInTheDocument();
    });

    expect(screen.queryByText('In-App Notifications')).not.toBeInTheDocument();
    expect(screen.queryByText('Show desktop notifications')).not.toBeInTheDocument();
    expect(screen.queryByText('Play sound on completion')).not.toBeInTheDocument();
  });
});
