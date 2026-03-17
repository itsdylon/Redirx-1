import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2, AlertTriangle, RefreshCw } from 'lucide-react';
import { Button } from './ui/button';
import { Progress } from './ui/progress';
import { Alert, AlertTitle, AlertDescription } from './ui/alert';
import { getSessionStatus, SessionStatus } from '../api/sessions';
import { useAuth } from '../contexts/AuthContext';
import { isEnterprisePlan } from '../lib/plans';
import { ROUTES, getRetryRouteForPlan } from '../routes';

interface LoadingScreenProps {
  sessionId?: string | null;
  tutorialMode?: boolean;
  minDisplayMs?: number;
  pipelineType?: 'content' | 'url_only';
  oldUrlCount?: number;
  newUrlCount?: number;
}

interface DeepProgressState {
  pagesScraped: number;
  pagesTotal: number;
  oldScraped: number;
  newScraped: number;
  embeddingsGenerated: number;
  embeddingsTotal: number;
  embeddingsFailed: number;
}

const POLL_INTERVAL = 3000; // 3 seconds
const WARNING_THRESHOLD = 3; // Show warning after 3 failures
const ERROR_THRESHOLD = 5; // Show error after 5 failures
const QUICK_TIP_ROTATION_MS = 3200;
const SCRAPE_PROGRESS_RE = /Scraping webpages \((\d+)\/(\d+) pages scraped(?:, old (\d+), new (\d+))?\)/i;
const EMBED_PROGRESS_RE = /Generating embeddings \((\d+)\/(\d+) generated(?:, (\d+) failed)?\)/i;
const QUICK_MATCH_TIPS = [
  'Matching URL patterns now. Deep Match also compares page titles and content for stronger accuracy.',
  'Quick Match handles straightforward path moves fast. Deep Match catches pages renamed to totally new slugs.',
  'When Quick Match confidence is medium or low, Deep Match can re-rank targets using content similarity.',
  'Clean URL taxonomy helps Quick Match. Deep Match adds semantic matching when path structures changed.',
  'Deep Match often recovers renamed destinations (for example, /pricing-plans -> /plans-and-pricing).',
];

function parseScrapeProgress(stageName: string | null | undefined) {
  if (!stageName) return null;
  const match = stageName.match(SCRAPE_PROGRESS_RE);
  if (!match) return null;
  return {
    pagesScraped: Number(match[1] || 0),
    pagesTotal: Number(match[2] || 0),
    oldScraped: Number(match[3] || 0),
    newScraped: Number(match[4] || 0),
  };
}

function parseEmbeddingProgress(stageName: string | null | undefined) {
  if (!stageName) return null;
  const match = stageName.match(EMBED_PROGRESS_RE);
  if (!match) return null;
  return {
    embeddingsGenerated: Number(match[1] || 0),
    embeddingsTotal: Number(match[2] || 0),
    embeddingsFailed: Number(match[3] || 0),
  };
}

export function LoadingScreen({
  sessionId,
  tutorialMode = false,
  minDisplayMs = 0,
  pipelineType = 'content',
  oldUrlCount = 0,
  newUrlCount = 0,
}: LoadingScreenProps) {
  const navigate = useNavigate();
  const { user } = useAuth();
  const enterpriseUser = isEnterprisePlan(user?.plan);
  const retryRoute = getRetryRouteForPlan(user?.plan);
  const fallbackRoute = enterpriseUser ? ROUTES.dashboard : ROUTES.urlMatch;
  const [progress, setProgress] = useState<{
    currentStage: number | null;
    stageName: string | null;
    totalStages: number | null;
  }>({ currentStage: null, stageName: null, totalStages: null });
  const [error, setError] = useState<string | null>(null);
  const [failureCount, setFailureCount] = useState(0);
  const [nextRetryIn, setNextRetryIn] = useState(0);
  const [pollingError, setPollingError] = useState(false);
  const [isDelayingCompletion, setIsDelayingCompletion] = useState(false);
  const [quickTipIndex, setQuickTipIndex] = useState(0);
  const [deepProgress, setDeepProgress] = useState<DeepProgressState>({
    pagesScraped: 0,
    pagesTotal: Math.max(0, oldUrlCount + newUrlCount),
    oldScraped: 0,
    newScraped: 0,
    embeddingsGenerated: 0,
    embeddingsTotal: 0,
    embeddingsFailed: 0,
  });
  const startedAtRef = useRef<number>(Date.now());
  const isQuickMatchFlow = pipelineType === 'url_only';
  const isDeepMatchFlow = pipelineType === 'content';

  useEffect(() => {
    startedAtRef.current = Date.now();
    setIsDelayingCompletion(false);
    setDeepProgress({
      pagesScraped: 0,
      pagesTotal: Math.max(0, oldUrlCount + newUrlCount),
      oldScraped: 0,
      newScraped: 0,
      embeddingsGenerated: 0,
      embeddingsTotal: 0,
      embeddingsFailed: 0,
    });
  }, [newUrlCount, oldUrlCount, pipelineType, sessionId, minDisplayMs]);

  useEffect(() => {
    if (!isQuickMatchFlow) return;
    const intervalId = window.setInterval(() => {
      setQuickTipIndex((prev) => (prev + 1) % QUICK_MATCH_TIPS.length);
    }, QUICK_TIP_ROTATION_MS);
    return () => window.clearInterval(intervalId);
  }, [isQuickMatchFlow]);

  // Calculate exponential backoff delay
  const getBackoffDelay = (failures: number): number => {
    return Math.min(POLL_INTERVAL * Math.pow(2, failures), 30000);
  };

  // Poll for job completion with failure handling
  useEffect(() => {
    if (!sessionId) return;

    let timeoutId: NodeJS.Timeout;
    let countdownIntervalId: NodeJS.Timeout | null = null;

    const poll = async () => {
      // Stop polling if we've hit the error threshold
      if (failureCount >= ERROR_THRESHOLD) {
        setPollingError(true);
        return;
      }

      try {
        const status: SessionStatus = await getSessionStatus(sessionId);

        // Successful poll - reset failure count
        setFailureCount(0);
        setNextRetryIn(0);

        const scrapeProgress = parseScrapeProgress(status.stage_name ?? null);
        if (scrapeProgress) {
          setDeepProgress((prev) => ({
            ...prev,
            pagesScraped: scrapeProgress.pagesScraped,
            pagesTotal: scrapeProgress.pagesTotal || prev.pagesTotal,
            oldScraped: scrapeProgress.oldScraped,
            newScraped: scrapeProgress.newScraped,
          }));
        }

        const embeddingProgress = parseEmbeddingProgress(status.stage_name ?? null);
        if (embeddingProgress) {
          setDeepProgress((prev) => ({
            ...prev,
            embeddingsGenerated: embeddingProgress.embeddingsGenerated,
            embeddingsTotal: embeddingProgress.embeddingsTotal || prev.embeddingsTotal,
            embeddingsFailed: embeddingProgress.embeddingsFailed,
          }));
        }

        // Update progress state
        setProgress({
          currentStage: status.current_stage ?? null,
          stageName: status.stage_name ?? null,
          totalStages: status.total_stages ?? null,
        });

        if (status.status === 'completed') {
          const elapsedMs = Date.now() - startedAtRef.current;
          const remainingMs = Math.max(0, minDisplayMs - elapsedMs);

          if (remainingMs > 0) {
            setIsDelayingCompletion(true);
            setProgress({
              currentStage: status.total_stages ?? status.current_stage ?? 4,
              stageName: tutorialMode ? 'Finalizing sample tutorial flow' : status.stage_name ?? 'Finalizing',
              totalStages: status.total_stages ?? 4,
            });
            timeoutId = setTimeout(poll, Math.min(remainingMs, 500));
            return;
          }

          setIsDelayingCompletion(false);
          navigate(tutorialMode ? `/review/${sessionId}?tutorial=1` : `/review/${sessionId}`);
          return;
        } else if (status.status === 'failed') {
          setError('The processing job failed. This could be due to invalid URLs, network issues, or an internal error. Please try uploading your files again.');
          console.error('Job failed');
          return;
        }

        // Schedule next poll with normal interval
        timeoutId = setTimeout(poll, POLL_INTERVAL);
      } catch (error) {
        console.error('Error polling status:', error);

        // Increment failure count
        const newFailureCount = failureCount + 1;
        setFailureCount(newFailureCount);

        // Calculate backoff delay
        const delay = getBackoffDelay(newFailureCount);
        setNextRetryIn(Math.ceil(delay / 1000)); // Convert to seconds

        // Start countdown timer
        if (countdownIntervalId) {
          clearInterval(countdownIntervalId);
        }
        countdownIntervalId = setInterval(() => {
          setNextRetryIn((prev) => Math.max(0, prev - 1));
        }, 1000);

        // Schedule next poll with backoff
        timeoutId = setTimeout(poll, delay);
      }
    };

    // Start polling
    poll();

    return () => {
      clearTimeout(timeoutId);
      if (countdownIntervalId) {
        clearInterval(countdownIntervalId);
      }
    };
  }, [failureCount, minDisplayMs, navigate, sessionId, tutorialMode]);

  const handleContinueInBackground = () => {
    navigate(fallbackRoute);
  };

  const handleRetry = () => {
    setFailureCount(0);
    setNextRetryIn(0);
    setPollingError(false);
  };

  const hasProgress = progress.currentStage != null && progress.totalStages != null;
  const progressPercent = hasProgress
    ? (progress.currentStage! / progress.totalStages!) * 100
    : 0;
  const quickTip = QUICK_MATCH_TIPS[quickTipIndex] || QUICK_MATCH_TIPS[0];
  const deepPageTotal = deepProgress.pagesTotal || Math.max(0, oldUrlCount + newUrlCount);
  const deepEmbeddingTotal = deepProgress.embeddingsTotal || deepPageTotal;
  const estimatedTotalMinutes = useMemo(() => {
    const billablePages = Math.max(1, oldUrlCount, newUrlCount);
    return Math.max(2, Math.ceil(billablePages / 140));
  }, [newUrlCount, oldUrlCount]);
  const estimatedRemainingMinutes = Math.max(
    1,
    Math.ceil(estimatedTotalMinutes * (1 - Math.min(100, progressPercent) / 100)),
  );

  // Show error UI if job failed
  if (error) {
    return (
      <div className="min-h-0 flex-1 flex items-center justify-center p-8">
        <div className="w-full max-w-xl">
          <div className="space-y-6">
            <Alert variant="destructive">
              <AlertTriangle className="h-5 w-5" />
              <AlertTitle>Processing Failed</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>

            <div className="flex flex-col sm:flex-row gap-3">
              <Button
                onClick={() => navigate(retryRoute)}
                className="flex-1"
              >
                Try Again
              </Button>
              <Button
                variant="outline"
                onClick={() => navigate(fallbackRoute)}
                className="flex-1"
              >
                {enterpriseUser ? 'View Dashboard' : 'Back to Quick Match'}
              </Button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // Show error UI if polling failed too many times
  if (pollingError) {
    return (
      <div className="min-h-0 flex-1 flex items-center justify-center p-8">
        <div className="w-full max-w-xl">
          <div className="space-y-6">
            <Alert variant="destructive">
              <AlertTriangle className="h-5 w-5" />
              <AlertTitle>Unable to Check Job Status</AlertTitle>
              <AlertDescription>
                Unable to check job status. The job may still be running. Please try refreshing or return to your previous workspace.
              </AlertDescription>
            </Alert>

            <div className="flex flex-col sm:flex-row gap-3">
              <Button
                onClick={handleRetry}
                className="flex-1"
              >
                Retry
              </Button>
              <Button
                variant="outline"
                onClick={() => navigate(fallbackRoute)}
                className="flex-1"
              >
                {enterpriseUser ? 'Go to Dashboard' : 'Back to Quick Match'}
              </Button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-0 flex-1 flex items-center justify-center p-8">
      <div className="w-full max-w-xl">
        <div className="text-center">
          {/* Warning banner for polling failures */}
          {failureCount >= WARNING_THRESHOLD && failureCount < ERROR_THRESHOLD && (
            <Alert variant="default" className="mb-6 text-left border-amber-500 dark:border-amber-600">
              <AlertTriangle className="h-5 w-5 text-amber-600 dark:text-amber-500" />
              <AlertTitle>Having Trouble Checking Status</AlertTitle>
              <AlertDescription className="space-y-3">
                <p>
                  Having trouble checking status. Retrying in {nextRetryIn} second{nextRetryIn !== 1 ? 's' : ''}...
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleRetry}
                  className="w-full sm:w-auto"
                >
                  <RefreshCw className="h-4 w-4 mr-2" />
                  Refresh Now
                </Button>
              </AlertDescription>
            </Alert>
          )}

          {isQuickMatchFlow && !tutorialMode && (
            <div className="mb-6 rounded-md border border-blue-500/30 bg-blue-500/5 p-4 text-left">
              <p className="text-xs font-semibold uppercase tracking-wide text-blue-700 dark:text-blue-300">Quick Match Tip</p>
              <p className="mt-2 text-sm text-muted-foreground">{quickTip}</p>
            </div>
          )}

          {isDeepMatchFlow && (
            <div className="mb-6 rounded-md border border-orange-500/30 bg-orange-500/10 p-4 text-left">
              <p className="text-sm font-medium text-foreground">
                Please be patient - <span className="line-through">greatness</span> content scraping takes time
                {' '}({`estimate: ~${estimatedRemainingMinutes} min`})
              </p>
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                <div className="rounded-md border border-border bg-card p-3">
                  <div className="text-xs uppercase tracking-wide text-muted-foreground">Pages Scraped</div>
                  <div className="mt-1 text-lg font-semibold text-foreground">
                    {deepProgress.pagesScraped}/{deepPageTotal}
                  </div>
                  <div className="text-xs text-muted-foreground mt-1">
                    Old: {deepProgress.oldScraped} | New: {deepProgress.newScraped}
                  </div>
                </div>
                <div className="rounded-md border border-border bg-card p-3">
                  <div className="text-xs uppercase tracking-wide text-muted-foreground">Embeddings Generated</div>
                  <div className="mt-1 text-lg font-semibold text-foreground">
                    {deepProgress.embeddingsGenerated}/{deepEmbeddingTotal}
                  </div>
                  <div className="text-xs text-muted-foreground mt-1">
                    Failed: {deepProgress.embeddingsFailed}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Progress heading */}
          <h1 className="text-foreground mb-2">
            {hasProgress
              ? `Step ${progress.currentStage} of ${progress.totalStages}`
              : isDeepMatchFlow
                ? 'Starting Deep Match...'
                : 'Preparing...'}
          </h1>

          {/* Stage name with spinner */}
          <div className="flex items-center justify-center gap-2 mb-6">
            <Loader2 className="h-4 w-4 text-muted-foreground animate-spin" />
            <p className="text-muted-foreground">
              {progress.stageName
                ? `${progress.stageName}...`
                : isQuickMatchFlow
                  ? 'Comparing URL patterns and confidence bands.'
                  : 'Analyzing content and generating semantic matches.'}
            </p>
          </div>

          {/* Progress bar */}
          <div className="mb-8">
            <Progress value={progressPercent} className="h-3" />
          </div>

          {/* Info box */}
          <div className="mb-6 bg-card border border-border p-4 text-left">
            <p className="text-sm text-muted-foreground">
              {tutorialMode
                ? (isDelayingCompletion
                    ? 'Holding for a brief tutorial pause before opening your sample results.'
                    : 'Tutorial mode: this sample run intentionally includes a short processing pause so the flow is easier to follow.')
                : isDeepMatchFlow
                  ? 'Deep Match is intentionally slower because we scrape real page content and run embeddings for higher-accuracy redirect decisions.'
                  : `Quick Match is running. All results remain visible in your project when this completes.`
              }
            </p>
          </div>

          {/* Continue in Background Button */}
          <Button
            variant="outline"
            onClick={handleContinueInBackground}
            className="w-full"
          >
            Continue in background
          </Button>
        </div>
      </div>
    </div>
  );
}
