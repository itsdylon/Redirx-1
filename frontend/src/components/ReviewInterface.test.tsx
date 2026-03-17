import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ReviewInterface } from './ReviewInterface';

const mockUseAuth = vi.fn();
const mockGetResults = vi.fn();
const mockGetProjectUnlockStatus = vi.fn();
const mockCapture = vi.fn();

vi.mock('@posthog/react', () => ({
  usePostHog: () => ({ capture: mockCapture }),
}));

vi.mock('react-hotkeys-hook', () => ({
  useHotkeys: vi.fn(),
}));

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => mockUseAuth(),
}));

vi.mock('../contexts/OnboardingContext', () => ({
  useOnboarding: () => ({
    onboarding: null,
    completeStep: vi.fn(),
    completeOnboarding: vi.fn(),
    isStepCompleted: vi.fn().mockReturnValue(false),
  }),
}));

vi.mock('../api/pipeline', () => ({
  getResults: (...args: unknown[]) => mockGetResults(...args),
}));

vi.mock('../api/billing', () => ({
  getProjectUnlockStatus: (...args: unknown[]) => mockGetProjectUnlockStatus(...args),
}));

vi.mock('./StatsBar', () => ({
  StatsBar: () => <div>Stats Bar</div>,
}));

vi.mock('./ReviewToolbar', () => ({
  ReviewToolbar: () => <div>Review Toolbar</div>,
}));

vi.mock('./RedirectTable', () => ({
  RedirectTable: () => <div>Redirect Table</div>,
}));

vi.mock('./InlineEditDialog', () => ({
  InlineEditDialog: () => null,
}));

vi.mock('./ExportModal', () => ({
  ExportModal: () => null,
}));

vi.mock('./KeyboardShortcutsDialog', () => ({
  KeyboardShortcutsDialog: () => null,
}));

vi.mock('./DeepMatchPrompt', () => ({
  DeepMatchPrompt: () => <div>Deep Match Prompt</div>,
}));

function renderReviewPage(initialPath = '/review/session-1') {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/review/:sessionId" element={<ReviewInterface layoutVariant="tool" />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('ReviewInterface regression', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseAuth.mockReturnValue({
      user: { id: 'tool-user', email: 'tool@example.com', plan: 'free' },
      logout: vi.fn().mockResolvedValue(undefined),
    });

    mockGetResults.mockResolvedValue({
      success: true,
      session: { id: 'session-1', pipeline_type: 'content' },
      mappings: [
        {
          id: 'row-1',
          oldUrl: '/old',
          newUrl: '/new',
          confidence: 0.9,
          confidenceBand: 'high',
          matchScore: 90,
          approved: true,
          warnings: [],
          pathSimilarity: 0.9,
          titleSimilarity: 0.8,
          contentSimilarity: 0.85,
        },
      ],
    });
    mockGetProjectUnlockStatus.mockResolvedValue(null);
  });

  it('does not render the deprecated in-content navigation helper box', async () => {
    renderReviewPage();

    await waitFor(() => {
      expect(mockGetResults).toHaveBeenCalledWith('session-1');
    });

    expect(screen.queryByText('Navigate back to previous projects or return to the main tool page.')).not.toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Primary' })).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Breadcrumb' })).toBeInTheDocument();
  });

  it('renders locked content paywall panel for unpaid direct deep sessions', async () => {
    mockGetResults.mockResolvedValue({
      success: true,
      locked: true,
      quote_status: 'draft',
      source_session_id: 'session-1',
      session: { id: 'session-1', pipeline_type: 'content', requires_payment_unlock: true },
      mappings: [],
      stats: { total: 0, high: 0, medium: 0, low: 0, approved: 0, approvalProgress: 0 },
    });

    renderReviewPage();

    await waitFor(() => {
      expect(mockGetResults).toHaveBeenCalledWith('session-1');
    });

    expect(
      await screen.findByText('Deep Match Prompt'),
    ).toBeInTheDocument();
  });

  it('tracks successful checkout return when unlock=success is present', async () => {
    renderReviewPage('/review/session-1?unlock=success');

    await waitFor(() => {
      expect(mockGetResults).toHaveBeenCalledWith('session-1');
    });

    expect(mockCapture).toHaveBeenCalledWith(
      'project_checkout_returned_success',
      expect.objectContaining({
        source_session_id: 'session-1',
        return_path: 'review',
      }),
    );
  });

  it('redirects checkout return from staged source session to paid deep session', async () => {
    mockGetResults.mockImplementation(async (requestedSessionId: string) => {
      if (requestedSessionId === 'session-1') {
        return {
          success: true,
          session: {
            id: 'session-1',
            pipeline_type: 'url_only',
            requires_payment_unlock: true,
          },
          mappings: [],
          stats: { total: 0, high: 0, medium: 0, low: 0, approved: 0, approvalProgress: 0 },
        };
      }

      return {
        success: true,
        session: {
          id: 'deep-session-1',
          pipeline_type: 'content',
          requires_payment_unlock: false,
        },
        mappings: [],
        stats: { total: 0, high: 0, medium: 0, low: 0, approved: 0, approvalProgress: 0 },
      };
    });

    mockGetProjectUnlockStatus.mockResolvedValue({
      source_session_id: 'session-1',
      has_quote: true,
      quote_id: 'quote-1',
      quote_status: 'paid',
      contact_required: false,
      billable_pages: 10,
      subtotal_cents: 1200,
      currency: 'usd',
      is_unlocked: true,
      deep_session_id: 'deep-session-1',
      deep_session_status: 'pending',
    });

    renderReviewPage('/review/session-1?unlock=success');

    await waitFor(() => {
      expect(mockGetResults).toHaveBeenCalledWith('deep-session-1');
    });
  });
});
