import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ReviewInterface } from './ReviewInterface';

const mockUseAuth = vi.fn();
const mockGetResults = vi.fn();
const mockGetDeepPreview = vi.fn();
const mockGetProjectUnlockStatus = vi.fn();

vi.mock('@posthog/react', () => ({
  usePostHog: () => ({ capture: vi.fn() }),
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
  getDeepPreview: (...args: unknown[]) => mockGetDeepPreview(...args),
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

vi.mock('./DeepMatchPreviewCard', () => ({
  DeepMatchPreviewCard: () => null,
}));

function renderReviewPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/review/session-1']}>
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
    mockGetDeepPreview.mockResolvedValue(null);
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
});
