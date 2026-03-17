import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ComponentProps } from 'react';
import { DeepMatchPrompt } from './DeepMatchPrompt';

function makeProps(overrides?: Partial<ComponentProps<typeof DeepMatchPrompt>>): ComponentProps<typeof DeepMatchPrompt> {
  return {
    pipelineType: 'url_only',
    isLockedResults: false,
    lockedQuoteStatus: null,
    sourceSessionId: 'source-session-1',
    totalRedirects: 42,
    quickAverageConfidence: 71,
    lowConfidenceCount: 12,
    lowConfidenceSamples: [
      {
        oldUrl: 'https://old.example.com/pricing',
        quickTargetUrl: 'https://new.example.com/home',
        quickScore: 58,
      },
    ],
    unlockStatus: null,
    unlockLoading: false,
    onPricingClick: vi.fn(),
    onViewDeepResults: vi.fn(),
    ...overrides,
  };
}

describe('DeepMatchPrompt', () => {
  it('shows no-quote state with pricing CTA', async () => {
    const user = userEvent.setup();
    const onPricingClick = vi.fn();
    render(<DeepMatchPrompt {...makeProps({ onPricingClick })} />);

    expect(screen.getByText(/URL Based Matching found 42 redirects/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /See Content Match pricing for this project/i })).toBeInTheDocument();
    expect(screen.getByText(/What Content Match improves/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /See Content Match pricing for this project/i }));
    expect(onPricingClick).toHaveBeenCalledWith({
      sourceSessionId: 'source-session-1',
      state: 'no_quote',
    });
  });

  it('shows purchase CTA when quote is ready', () => {
    render(
      <DeepMatchPrompt
        {...makeProps({
          unlockStatus: {
            source_session_id: 'source-session-1',
            has_quote: true,
            quote_status: 'draft',
            contact_required: false,
            billable_pages: 1200,
            subtotal_cents: 9000,
            currency: 'usd',
            pricing_version: 'v1_2026_03',
            is_unlocked: false,
            deep_session_id: null,
            deep_session_status: null,
          },
        })}
      />,
    );

    expect(screen.getByRole('button', { name: 'Purchase Content Match — $90.00' })).toBeInTheDocument();
  });

  it('shows payment processing state with no CTA', () => {
    render(
      <DeepMatchPrompt
        {...makeProps({
          unlockStatus: {
            source_session_id: 'source-session-1',
            has_quote: true,
            quote_status: 'checkout_created',
            contact_required: false,
            billable_pages: 1200,
            subtotal_cents: 9000,
            currency: 'usd',
            pricing_version: 'v1_2026_03',
            is_unlocked: false,
            deep_session_id: null,
            deep_session_status: null,
          },
        })}
      />,
    );

    expect(screen.getByText('Payment processing...')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Purchase Content Match/i })).not.toBeInTheDocument();
  });

  it('shows deep results CTA when unlocked run is completed', async () => {
    const user = userEvent.setup();
    const onViewDeepResults = vi.fn();
    render(
      <DeepMatchPrompt
        {...makeProps({
          unlockStatus: {
            source_session_id: 'source-session-1',
            has_quote: true,
            quote_status: 'paid',
            contact_required: false,
            billable_pages: 1200,
            subtotal_cents: 9000,
            currency: 'usd',
            pricing_version: 'v1_2026_03',
            is_unlocked: true,
            deep_session_id: 'deep-session-1',
            deep_session_status: 'completed',
          },
          onViewDeepResults,
        })}
      />,
    );

    const button = screen.getByRole('button', { name: 'View Content Match results' });
    await user.click(button);
    expect(onViewDeepResults).toHaveBeenCalledWith('deep-session-1');
  });

  it('shows locked content state CTA copy', () => {
    render(
      <DeepMatchPrompt
        {...makeProps({
          pipelineType: 'content',
          isLockedResults: true,
          unlockStatus: {
            source_session_id: 'source-session-1',
            has_quote: true,
            quote_status: 'draft',
            contact_required: false,
            billable_pages: 1200,
            subtotal_cents: 9000,
            currency: 'usd',
            pricing_version: 'v1_2026_03',
            is_unlocked: false,
            deep_session_id: null,
            deep_session_status: null,
          },
        })}
      />,
    );

    expect(screen.getByText(/Content Match found 42 redirects at 71% average confidence/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Purchase full results — $90.00' })).toBeInTheDocument();
  });
});
