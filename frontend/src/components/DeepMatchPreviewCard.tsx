import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { usePostHog } from '@posthog/react';
import { Loader2, Lock, ShieldAlert } from 'lucide-react';
import { Button } from './ui/button';
import { Card } from './ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { type DeepPreviewResponse } from '../api/pipeline';

interface DeepMatchPreviewCardProps {
  preview: DeepPreviewResponse;
  onOptIn?: (sourceSessionId: string) => Promise<void> | void;
  optInPending?: boolean;
  context?: {
    quickAverageConfidence: number;
    mediumRiskCount: number;
    lowRiskCount: number;
    totalRows: number;
  };
}

function confidencePct(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function derivedLockedCount(preview: DeepPreviewResponse): number {
  const inferred = Math.max(0, preview.total_convincing_fixes - preview.visible_items.length);
  return Math.max(preview.locked_teasers.length, inferred);
}

export function DeepMatchPreviewCard({
  preview,
  onOptIn,
  optInPending = false,
  context,
}: DeepMatchPreviewCardProps) {
  const navigate = useNavigate();
  const posthog = usePostHog();
  const [optInChecked, setOptInChecked] = useState(false);
  const [consentOpen, setConsentOpen] = useState(false);
  const queuedTracked = useRef(false);
  const readyTracked = useRef(false);
  const visibleTracked = useRef(false);

  useEffect(() => {
    if ((preview.status === 'queued' || preview.status === 'processing') && !queuedTracked.current) {
      queuedTracked.current = true;
      posthog?.capture('deep_preview_queued', {
        source_session_id: preview.source_session_id,
        status: preview.status,
      });
    }
  }, [posthog, preview.source_session_id, preview.status]);

  useEffect(() => {
    if (preview.status === 'completed' && !readyTracked.current) {
      readyTracked.current = true;
      posthog?.capture('deep_preview_ready', {
        source_session_id: preview.source_session_id,
        total_convincing_fixes: preview.total_convincing_fixes,
        locked_count: preview.locked_teasers.length,
      });
    }
  }, [posthog, preview]);

  useEffect(() => {
    if (preview.status === 'completed' && preview.total_convincing_fixes > 0 && !visibleTracked.current) {
      visibleTracked.current = true;
      posthog?.capture('deep_preview_visible', {
        source_session_id: preview.source_session_id,
        total_convincing_fixes: preview.total_convincing_fixes,
        free_unlock_count: preview.free_unlock_count,
        locked_count: preview.locked_teasers.length,
      });
    }
  }, [posthog, preview]);

  useEffect(() => {
    if (!consentOpen) {
      setOptInChecked(false);
    }
  }, [consentOpen, preview.source_session_id]);

  const pricingHref = `/pricing?source_session_id=${encodeURIComponent(preview.source_session_id)}`;

  const goToUpgrade = (eventName: 'deep_preview_cta_primary_clicked' | 'deep_preview_cta_secondary_clicked') => {
    posthog?.capture(eventName, {
      source_session_id: preview.source_session_id,
      total_convincing_fixes: preview.total_convincing_fixes,
      locked_count: preview.locked_teasers.length,
    });
    posthog?.capture('deep_unlock_clicked_from_review', {
      session_id: preview.source_session_id,
      pipeline_type: 'url_only',
      source: 'deep_preview_card',
    });
    navigate(pricingHref);
  };

  const handleConfirmOptIn = async () => {
    await onOptIn?.(preview.source_session_id);
    setConsentOpen(false);
  };

  if (preview.status === 'not_applicable') {
    return null;
  }

  if (preview.status === 'queued' || preview.status === 'processing') {
    return (
      <Card className="mt-4 border-blue-500/30 bg-blue-500/5 p-4">
        <div className="flex items-start gap-3">
          <Loader2 className="h-5 w-5 text-blue-500 animate-spin shrink-0 mt-0.5" />
          <div>
            <h3 className="font-semibold text-foreground">Running Deep Match Accuracy Check</h3>
            <p className="text-sm text-muted-foreground mt-1">
              We are re-checking your riskiest Quick Match redirects in the background so you can compare confidence before launch.
            </p>
          </div>
        </div>
      </Card>
    );
  }

  if (preview.status === 'awaiting_opt_in') {
    const mediumRisk = context?.mediumRiskCount ?? 0;
    const lowRisk = context?.lowRiskCount ?? 0;
    const riskyTotal = mediumRisk + lowRisk;
    const avgConfidence = Math.round(context?.quickAverageConfidence ?? 70);

    return (
      <>
        <Card className="mt-4 border-orange-500/40 bg-orange-500/10 p-4">
          <h3 className="font-semibold text-foreground">{preview.headline}</h3>
          <p className="text-sm text-muted-foreground mt-1">
            {preview.subheadline}
          </p>
          <p className="text-sm text-muted-foreground mt-3">
            Your Quick Match run is fully visible below ({context?.totalRows ?? 0} rows). Current average confidence is
            {' '}~{avgConfidence}% with {riskyTotal} medium/low-confidence mappings that Deep Match can re-evaluate against page content.
          </p>

          <div className="mt-4 flex flex-wrap gap-2">
            <Button onClick={() => setConsentOpen(true)}>
              {preview.cta_primary || 'Start Deep Match Accuracy Check'}
            </Button>
            <Button variant="outline" onClick={() => goToUpgrade('deep_preview_cta_secondary_clicked')}>
              {preview.cta_secondary}
            </Button>
          </div>
        </Card>

        <Dialog open={consentOpen} onOpenChange={setConsentOpen}>
          <DialogContent className="sm:max-w-xl">
            <DialogHeader>
              <DialogTitle>Deep Match Consent</DialogTitle>
              <DialogDescription>
                Confirm scraping access and trust details before we run content-aware matching.
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-4">
              <div className="border border-destructive/60 bg-destructive/10 p-3 rounded-sm">
                <div className="flex items-start gap-2">
                  <ShieldAlert className="h-5 w-5 text-destructive mt-0.5 shrink-0" />
                  <div>
                    <p className="text-sm font-semibold text-destructive">
                      Critical setup required: disable spam/bot protection or whitelist Redirx scraper traffic before Deep Match.
                    </p>
                    <p className="text-xs text-destructive/90 mt-1">
                      Firewalls like Wordfence or Cloudflare challenge mode can block the scraper and may blacklist Redirx server IPs if protections stay enabled.
                    </p>
                  </div>
                </div>
              </div>

              <div className="rounded-sm border border-border bg-card p-3">
                <p className="text-sm font-medium text-foreground">Trust signals</p>
                <ul className="mt-2 list-disc list-inside space-y-1 text-sm text-muted-foreground">
                  <li>Scanned page data is used for matching and deleted after processing.</li>
                  <li>Crawler behavior respects `robots.txt` directives.</li>
                  <li>Protections can be re-enabled immediately after the run completes.</li>
                </ul>
              </div>

              <label className="flex items-start gap-2 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={optInChecked}
                  onChange={(e) => setOptInChecked(e.target.checked)}
                  className="h-4 w-4 rounded border-border accent-primary mt-0.5"
                />
                <span className="text-sm text-foreground">
                  I confirm protections are disabled or Redirx traffic is whitelisted for this scan window.
                </span>
              </label>
            </div>

            <DialogFooter className="gap-2 sm:gap-0">
              <Button variant="outline" onClick={() => setConsentOpen(false)}>
                Cancel
              </Button>
              <Button
                disabled={!optInChecked || optInPending}
                onClick={handleConfirmOptIn}
              >
                {optInPending && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
                Proceed with Deep Match
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </>
    );
  }

  if (preview.status === 'failed' || preview.status === 'skipped') {
    return (
      <Card className="mt-4 border-border bg-card p-4">
        <h3 className="font-semibold text-foreground">Deep Match Preview Unavailable for This Run</h3>
        <p className="text-sm text-muted-foreground mt-1">
          {preview.error_message || 'This run did not produce enough high-confidence differences to show a trustworthy preview.'}
        </p>
      </Card>
    );
  }

  return (
    <Card className="mt-4 border-emerald-500/30 bg-emerald-500/5 p-4">
      <h3 className="text-foreground font-semibold">{preview.headline}</h3>
      <p className="text-sm text-muted-foreground mt-1">{preview.subheadline}</p>
      <div className="mt-3 rounded-sm border border-emerald-500/40 bg-emerald-500/10 p-3">
        <p className="text-sm font-medium text-foreground">Quick Match vs Deep Match (side-by-side)</p>
        <p className="text-xs text-muted-foreground mt-1">
          Left shows the current Quick Match target and confidence. Right shows what Deep Match found with content-level analysis.
        </p>
      </div>

      <div className="mt-4 space-y-2">
        {preview.visible_items.map((item) => (
          <div key={`${item.old_url}:${item.deep_target_url}`} className="rounded-md border border-border bg-card p-3">
            <div className="text-xs uppercase tracking-wide text-muted-foreground">Mistake Prevented</div>
            <div className="mt-1 text-sm text-foreground font-mono break-all">{item.old_url}</div>
            <div className="mt-2 grid gap-2 sm:grid-cols-2">
              <div className="rounded bg-muted/50 p-2">
                <div className="text-xs text-muted-foreground">Quick Match Target</div>
                <div className="text-xs font-mono break-all text-foreground">{item.current_target_url}</div>
                <div className="text-xs text-muted-foreground mt-1">~{confidencePct(item.quick_confidence)} confidence</div>
              </div>
              <div className="rounded bg-emerald-500/10 p-2 border border-emerald-500/30">
                <div className="text-xs text-muted-foreground">Deep Match Target</div>
                <div className="text-xs font-mono break-all text-foreground">{item.deep_target_url}</div>
                <div className="text-xs text-emerald-700 dark:text-emerald-400 mt-1">~{confidencePct(item.deep_confidence)} confidence</div>
              </div>
            </div>
            <div className="mt-2 inline-flex items-center rounded-full border border-emerald-500/40 bg-emerald-500/10 px-2 py-1 text-xs font-semibold text-emerald-700 dark:text-emerald-400">
              Confidence gain: +{item.confidence_gain_points} pts
            </div>
          </div>
        ))}
      </div>

      {preview.locked_teasers.length > 0 && (
        <div className="mt-3 relative">
          <div className="space-y-2 blur-[2px] select-none">
            {preview.locked_teasers.map((teaser) => (
              <div key={teaser.old_url} className="rounded-md border border-border bg-card p-3">
                <div className="text-xs uppercase tracking-wide text-muted-foreground">Locked fix</div>
                <div className="text-sm font-mono break-all text-foreground mt-1">{teaser.old_url}</div>
                <div className="text-xs text-muted-foreground mt-1">{teaser.teaser}</div>
              </div>
            ))}
          </div>
          <div className="absolute inset-0 flex items-center justify-center rounded-md bg-background/80 backdrop-blur-[1px] p-4 text-center">
            <div>
              <div className="inline-flex items-center justify-center rounded-full bg-foreground/10 p-2 mb-2">
                <Lock className="h-4 w-4 text-foreground" />
              </div>
              <p className="text-sm font-medium text-foreground">
                {preview.lock_overlay || `Unlock ${derivedLockedCount(preview)} more high-confidence fixes and AI alternatives before launch.`}
              </p>
            </div>
          </div>
        </div>
      )}

      <div className="mt-4 flex flex-wrap gap-2">
        <Button onClick={() => goToUpgrade('deep_preview_cta_primary_clicked')}>
          {preview.cta_primary}
        </Button>
        <Button variant="outline" onClick={() => goToUpgrade('deep_preview_cta_secondary_clicked')}>
          {preview.cta_secondary}
        </Button>
      </div>
    </Card>
  );
}
