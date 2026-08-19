/**
 * Pages with no recorded traffic collapse out of the review list.
 *
 * Once Search Console has spoken, a page with no clicks and no impressions
 * carries no measurable risk, so it should not compete for review attention
 * with the pages that do. It is hidden, never dropped — "no recorded traffic"
 * is not "no traffic", since Google withholds low-volume queries.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const mockUseAuth = vi.fn();
const mockGetResults = vi.fn();
const mockGetDeepPreview = vi.fn();
const mockGetProjectUnlockStatus = vi.fn();

vi.mock('@posthog/react', () => ({
  usePostHog: () => ({ capture: vi.fn() }),
}));

vi.mock('react-hotkeys-hook', () => ({ useHotkeys: vi.fn() }));

vi.mock('../contexts/AuthContext', () => ({ useAuth: () => mockUseAuth() }));

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

vi.mock('./StatsBar', () => ({ StatsBar: () => <div>Stats Bar</div> }));
vi.mock('./ReviewToolbar', () => ({ ReviewToolbar: () => <div>Review Toolbar</div> }));
vi.mock('./InlineEditDialog', () => ({ InlineEditDialog: () => null }));
vi.mock('./ExportModal', () => ({ ExportModal: () => null }));
vi.mock('./KeyboardShortcutsDialog', () => ({ KeyboardShortcutsDialog: () => null }));
vi.mock('./DeepMatchPreviewCard', () => ({ DeepMatchPreviewCard: () => null }));

// Unlike the shared harness, this mock reports which rows actually reached the
// table — that is the whole thing under test.
vi.mock('./RedirectTable', () => ({
  RedirectTable: ({ redirects }: { redirects: Array<{ oldUrl: string }> }) => (
    <ul data-testid="rows">
      {redirects.map((r) => (
        <li key={r.oldUrl}>{r.oldUrl}</li>
      ))}
    </ul>
  ),
}));

import { ReviewInterface } from './ReviewInterface';

function mapping(oldUrl: string, gscClicks = 0, gscImpressions = 0) {
  return {
    id: oldUrl,
    oldUrl,
    newUrl: `${oldUrl}-new`,
    confidence: 0.9,
    confidenceBand: 'high',
    matchScore: 90,
    approved: false,
    warnings: [],
    pathSimilarity: 0.9,
    titleSimilarity: 0.8,
    contentSimilarity: 0.85,
    gscClicks,
    gscImpressions,
  };
}

function renderReviewPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/review/session-1']}>
        <Routes>
          <Route path="/review/:sessionId" element={<ReviewInterface layoutVariant="tool" />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

function results(mappings: unknown[], synced: boolean) {
  return {
    success: true,
    session: { id: 'session-1', pipeline_type: 'content' },
    mappings,
    gsc: synced ? { synced: true, property: 'sc-domain:example.com' } : undefined,
  };
}

const rowText = () => screen.getByTestId('rows').textContent ?? '';

beforeEach(() => {
  vi.clearAllMocks();
  mockUseAuth.mockReturnValue({
    user: { id: 'u1', email: 'u@example.com', plan: 'free' },
    logout: vi.fn().mockResolvedValue(undefined),
  });
  mockGetDeepPreview.mockResolvedValue(null);
  mockGetProjectUnlockStatus.mockResolvedValue(null);
});

describe('zero-traffic collapse', () => {
  it('hides pages with no recorded traffic once Search Console data is in', async () => {
    mockGetResults.mockResolvedValue(
      results(
        [
          mapping('/earns-clicks', 120, 900),
          mapping('/only-impressions', 0, 40),
          mapping('/silent-a'),
          mapping('/silent-b'),
        ],
        true
      )
    );
    renderReviewPage();

    await waitFor(() => expect(rowText()).toContain('/earns-clicks'));
    // An impression is still evidence the page is reachable from search.
    expect(rowText()).toContain('/only-impressions');
    expect(rowText()).not.toContain('/silent-a');
    expect(rowText()).not.toContain('/silent-b');
  });

  it('says how many were set aside, and that they are still redirected', async () => {
    mockGetResults.mockResolvedValue(
      results([mapping('/earns-clicks', 120, 900), mapping('/silent-a'), mapping('/silent-b')], true)
    );
    renderReviewPage();

    const bar = await screen.findByRole('button', { name: /no recorded traffic/i });
    expect(bar).toHaveTextContent('2');
    expect(bar).toHaveTextContent(/still redirected/i);
    expect(bar).toHaveAttribute('aria-expanded', 'false');
  });

  it('reveals them on request', async () => {
    mockGetResults.mockResolvedValue(
      results([mapping('/earns-clicks', 120, 900), mapping('/silent-a')], true)
    );
    renderReviewPage();

    const bar = await screen.findByRole('button', { name: /no recorded traffic/i });
    await userEvent.click(bar);

    await waitFor(() => expect(rowText()).toContain('/silent-a'));
    expect(bar).toHaveAttribute('aria-expanded', 'true');
  });

  it('does not collapse anything before Search Console has been synced', async () => {
    mockGetResults.mockResolvedValue(
      results([mapping('/a'), mapping('/b'), mapping('/c')], false)
    );
    renderReviewPage();

    await waitFor(() => expect(rowText()).toContain('/a'));
    expect(rowText()).toContain('/b');
    expect(rowText()).toContain('/c');
    expect(screen.queryByRole('button', { name: /no recorded traffic/i })).toBeNull();
  });

  it('never empties the table when nothing has traffic', async () => {
    // A property that matched no URLs would otherwise hide every row and
    // explain it with a count equal to the whole list.
    mockGetResults.mockResolvedValue(
      results([mapping('/a'), mapping('/b')], true)
    );
    renderReviewPage();

    await waitFor(() => expect(rowText()).toContain('/a'));
    expect(rowText()).toContain('/b');
    expect(screen.queryByRole('button', { name: /no recorded traffic/i })).toBeNull();
  });
});
