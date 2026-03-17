import { Button } from './ui/button';
import type { ProjectUnlockStatus } from '../api/billing';

interface LowConfidenceSample {
  oldUrl: string;
  quickTargetUrl: string;
  quickScore: number;
}

interface PricingClickContext {
  sourceSessionId: string;
  state: string;
}

interface PreviewSummary {
  match_count: number;
  average_confidence: number;
}

interface DeepMatchPromptProps {
  pipelineType: string;
  isLockedResults: boolean;
  lockedQuoteStatus: string | null;
  sourceSessionId: string;
  totalRedirects: number;
  quickAverageConfidence: number;
  lowConfidenceCount: number;
  lowConfidenceSamples: LowConfidenceSample[];
  previewSummary?: PreviewSummary | null;
  unlockStatus: ProjectUnlockStatus | null;
  unlockLoading: boolean;
  onPricingClick: (context: PricingClickContext) => void;
  onViewDeepResults: (deepSessionId: string) => void;
}

function formatUsdFromCents(value: number | null | undefined): string {
  if (value == null) return '';
  return `$${(value / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function buildPurchaseLabel(prefix: string, subtotalCents: number | null | undefined): string {
  const amount = formatUsdFromCents(subtotalCents);
  if (!amount) return prefix;
  return `${prefix} — ${amount}`;
}

export function DeepMatchPrompt({
  pipelineType,
  isLockedResults,
  lockedQuoteStatus,
  sourceSessionId,
  totalRedirects,
  quickAverageConfidence,
  lowConfidenceCount,
  lowConfidenceSamples,
  previewSummary,
  unlockStatus,
  unlockLoading,
  onPricingClick,
  onViewDeepResults,
}: DeepMatchPromptProps) {
  const renderStaticComparison = pipelineType === 'url_only';

  let state = 'idle';
  let message = '';
  let ctaLabel: string | null = null;
  let ctaAction: (() => void) | null = null;

  if (pipelineType === 'url_only') {
    if (unlockLoading) {
      state = 'loading';
      message = 'Loading Content Match pricing status for this project...';
    } else if (!unlockStatus || !unlockStatus.has_quote) {
      state = 'no_quote';
      message = `URL Based Matching found ${totalRedirects} redirects at ~${quickAverageConfidence}% average confidence. Content Based Matching can improve low-confidence rows with page analysis.`;
      ctaLabel = 'See Content Match pricing for this project →';
      ctaAction = () => onPricingClick({ sourceSessionId, state });
    } else if (!unlockStatus.is_unlocked) {
      if (unlockStatus.quote_status === 'checkout_created') {
        state = 'payment_processing';
        message = 'Payment processing...';
      } else if (unlockStatus.contact_required) {
        state = 'contact_required';
        message = 'This project exceeds self-serve limits. Contact sales from pricing to continue.';
        ctaLabel = 'See Content Match pricing for this project →';
        ctaAction = () => onPricingClick({ sourceSessionId, state });
      } else {
        state = 'quote_ready';
        message = 'Content Based Matching is ready to purchase for this project.';
        ctaLabel = buildPurchaseLabel('Purchase Content Match', unlockStatus.subtotal_cents);
        ctaAction = () => onPricingClick({ sourceSessionId, state });
      }
    } else if (
      unlockStatus.deep_session_status === 'queued' ||
      unlockStatus.deep_session_status === 'pending' ||
      unlockStatus.deep_session_status === 'processing'
    ) {
      state = 'deep_processing';
      message = 'Payment received. Content Match is running...';
    } else if (unlockStatus.deep_session_status === 'completed' && unlockStatus.deep_session_id) {
      state = 'deep_completed';
      message = 'Content Match results are ready.';
      ctaLabel = 'View Content Match results';
      ctaAction = () => onViewDeepResults(unlockStatus.deep_session_id!);
    } else {
      state = 'unlocked_waiting';
      message = 'Payment received. Content Match queueing in progress...';
    }
  } else if (isLockedResults) {
    if (lockedQuoteStatus === 'checkout_created') {
      state = 'locked_payment_processing';
      message = 'Payment processing...';
    } else if (
      unlockStatus?.is_unlocked &&
      unlockStatus.deep_session_status === 'completed' &&
      unlockStatus.deep_session_id
    ) {
      state = 'locked_completed';
      message = 'Content Match results are ready.';
      ctaLabel = 'View Content Match results';
      ctaAction = () => onViewDeepResults(unlockStatus.deep_session_id);
    } else {
      state = 'locked_unpaid';
      const previewCount = previewSummary?.match_count ?? totalRedirects;
      const previewAvg = previewSummary?.average_confidence ?? quickAverageConfidence;
      message = `Content Match found ${previewCount} redirects at ${previewAvg}% average confidence. Purchase to view and export full results.`;
      ctaLabel = buildPurchaseLabel('Purchase full results', unlockStatus?.subtotal_cents);
      ctaAction = () => onPricingClick({ sourceSessionId, state });
    }
  } else {
    return null;
  }

  return (
    <div className="mb-4 border border-emerald-500/30 bg-emerald-500/5 p-4 space-y-3">
      <p className="text-sm font-semibold text-foreground">Content Match</p>
      <p className="text-sm text-muted-foreground">{message}</p>

      {renderStaticComparison && (
        <div className="rounded-md border border-border bg-card p-3 space-y-3">
          <p className="text-sm font-medium text-foreground">What Content Match improves</p>
          <p className="text-xs text-muted-foreground">
            For your {lowConfidenceCount} medium/low-confidence rows, Content Match can validate targets with page content and recover renamed destinations.
          </p>
          <ul className="list-disc list-inside text-xs text-muted-foreground space-y-1">
            <li>Analyzes actual page content, not just URL structure.</li>
            <li>Finds likely targets when paths changed completely.</li>
            <li>Ranks alternatives by semantic similarity.</li>
          </ul>
          {lowConfidenceSamples.length > 0 && (
            <div className="space-y-1">
              <p className="text-xs font-medium text-foreground">Sample low-confidence rows from this URL based run:</p>
              {lowConfidenceSamples.map((sample) => (
                <div key={`${sample.oldUrl}:${sample.quickTargetUrl}`} className="text-xs text-muted-foreground font-mono break-all">
                  {sample.oldUrl} {'->'} {sample.quickTargetUrl} (~{sample.quickScore}%)
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {ctaLabel && ctaAction && (
        <div>
          <Button onClick={ctaAction}>{ctaLabel}</Button>
        </div>
      )}
    </div>
  );
}
