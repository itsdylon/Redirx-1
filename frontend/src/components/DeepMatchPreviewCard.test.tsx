import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { DeepPreviewResponse } from '../api/pipeline';
import { DeepMatchPreviewCard } from './DeepMatchPreviewCard';


const mockNavigate = vi.fn();
const mockCapture = vi.fn();

vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}));

vi.mock('@posthog/react', () => ({
  usePostHog: () => ({
    capture: mockCapture,
  }),
}));


function makeCompletedPreview(overrides?: Partial<DeepPreviewResponse>): DeepPreviewResponse {
  return {
    success: true,
    status: 'completed',
    source_session_id: 'session-123',
    free_unlock_count: 2,
    total_convincing_fixes: 4,
    visible_items: [
      {
        old_url: 'https://old.example.com/pricing',
        current_target_url: 'https://new.example.com/home',
        deep_target_url: 'https://new.example.com/pricing',
        quick_confidence: 0.63,
        deep_confidence: 0.92,
        confidence_gain: 0.29,
        confidence_gain_points: 29,
        deep_gap: 0.12,
        quick_match_type: 'semantic',
        deep_match_type: 'semantic',
        conviction_score: 0.48,
      },
      {
        old_url: 'https://old.example.com/contact',
        current_target_url: 'https://new.example.com/about',
        deep_target_url: 'https://new.example.com/contact',
        quick_confidence: 0.68,
        deep_confidence: 0.9,
        confidence_gain: 0.22,
        confidence_gain_points: 22,
        deep_gap: 0.1,
        quick_match_type: 'semantic',
        deep_match_type: 'semantic',
        conviction_score: 0.41,
      },
    ],
    locked_teasers: [
      {
        old_url: 'https://old.example.com/checkout',
        current_target_url: 'https://new.example.com/cart',
        confidence_gain_points: 17,
        deep_confidence: 0.88,
        teaser: 'Higher-confidence destination found',
      },
      {
        old_url: 'https://old.example.com/plans',
        current_target_url: 'https://new.example.com/pricing',
        confidence_gain_points: 15,
        deep_confidence: 0.87,
        teaser: 'Higher-confidence destination found',
      },
    ],
    headline: 'Deep Match found redirect mistakes Quick Match missed.',
    subheadline: 'We re-checked your riskiest URLs with content-level AI matching and found 4 higher-confidence fixes.',
    cta_primary: 'Unlock Every High-Confidence Fix',
    cta_secondary: 'Compare Plans',
    lock_overlay: 'Unlock 2 more high-confidence fixes and AI alternatives before launch.',
    ...overrides,
  };
}


describe('DeepMatchPreviewCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders completed preview with visible rows and locked overlay', async () => {
    const user = userEvent.setup();
    render(<DeepMatchPreviewCard preview={makeCompletedPreview()} />);

    expect(screen.getByText('Deep Match found redirect mistakes Quick Match missed.')).toBeInTheDocument();
    expect(screen.getAllByText('Mistake Prevented')).toHaveLength(2);
    expect(screen.getByText('Unlock 2 more high-confidence fixes and AI alternatives before launch.')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Unlock Every High-Confidence Fix' }));

    expect(mockNavigate).toHaveBeenCalledWith(
      '/pricing?source_session_id=session-123',
    );
    expect(mockCapture).toHaveBeenCalledWith(
      'deep_preview_cta_primary_clicked',
      expect.objectContaining({
        source_session_id: 'session-123',
      }),
    );
  });

  it('renders queued state and tracks queue visibility event', () => {
    render(
      <DeepMatchPreviewCard
        preview={makeCompletedPreview({
          status: 'queued',
          total_convincing_fixes: 0,
          visible_items: [],
          locked_teasers: [],
        })}
      />,
    );

    expect(screen.getByText('Running Deep Match Accuracy Check')).toBeInTheDocument();
    expect(mockCapture).toHaveBeenCalledWith(
      'deep_preview_queued',
      expect.objectContaining({
        source_session_id: 'session-123',
        status: 'queued',
      }),
    );
  });

  it('returns no UI when preview is not applicable', () => {
    const { container } = render(
      <DeepMatchPreviewCard preview={makeCompletedPreview({ status: 'not_applicable' })} />,
    );

    expect(container).toBeEmptyDOMElement();
  });
});
