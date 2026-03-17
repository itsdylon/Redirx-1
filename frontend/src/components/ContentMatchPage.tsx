import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { usePostHog } from '@posthog/react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Loader2 } from 'lucide-react';

import { ToolLayout } from './ToolLayout';
import { FileUploadZone } from './FileUploadZone';
import { LoadingScreen } from './LoadingScreen';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { useAuth } from '../contexts/AuthContext';
import { setAuthRedirect } from '../lib/authRedirect';
import { validateFile, type FileValidationResult } from '../utils/validation';
import { getPricingEstimate } from '../api/billing';
import { startContentMatch } from '../api/pipeline';
import { getSourceSessionFiles } from '../api/sessions';

interface FileData {
  name: string;
  rowCount: number;
  file: File;
}

function formatUsdFromCents(value: number | null | undefined): string {
  if (value == null) return 'TBD';
  return `$${(value / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function urlsToCsv(urls: string[]): string {
  return ['url', ...urls].join('\n');
}

export function ContentMatchPage() {
  type OAuthProvider = 'google' | 'github';

  const navigate = useNavigate();
  const posthog = usePostHog();
  const [searchParams] = useSearchParams();
  const sourceSessionId = searchParams.get('source_session_id') || '';
  const { user, startOAuth } = useAuth();

  const [oauthLoading, setOauthLoading] = useState<OAuthProvider | null>(null);
  const [oauthError, setOauthError] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [oldSiteFile, setOldSiteFile] = useState<FileData | null>(null);
  const [newSiteFile, setNewSiteFile] = useState<FileData | null>(null);
  const [oldCsvFile, setOldCsvFile] = useState<File | null>(null);
  const [newCsvFile, setNewCsvFile] = useState<File | null>(null);
  const [oldFileValidation, setOldFileValidation] = useState<FileValidationResult | null>(null);
  const [newFileValidation, setNewFileValidation] = useState<FileValidationResult | null>(null);
  const [pendingWarnings, setPendingWarnings] = useState<{ old: string[]; new: string[] } | null>(null);
  const [scrapingChecklistAccepted, setScrapingChecklistAccepted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [appliedSourceSessionId, setAppliedSourceSessionId] = useState<string | null>(null);

  const sourceFilesQuery = useQuery({
    queryKey: ['source-session-files', sourceSessionId],
    queryFn: () => getSourceSessionFiles(sourceSessionId),
    enabled: !!user && !!sourceSessionId,
    staleTime: 30_000,
  });

  useEffect(() => {
    posthog?.capture('content_match_tool_viewed', {
      source: sourceSessionId ? 'cross_link' : 'content-match',
      plan: user?.plan || 'anonymous',
      source_session_id: sourceSessionId || null,
    });
  }, [posthog, sourceSessionId, user?.plan]);

  useEffect(() => {
    if (!sourceSessionId || !sourceFilesQuery.data || appliedSourceSessionId === sourceSessionId) {
      return;
    }

    const source = sourceFilesQuery.data;
    const oldFile = new File(
      [urlsToCsv(source.old_urls || [])],
      'preloaded-old-site-urls.csv',
      { type: 'text/csv' },
    );
    const newFile = new File(
      [urlsToCsv(source.new_urls || [])],
      'preloaded-new-site-urls.csv',
      { type: 'text/csv' },
    );

    setOldCsvFile(oldFile);
    setNewCsvFile(newFile);
    setOldSiteFile({
      name: oldFile.name,
      rowCount: source.old_url_count,
      file: oldFile,
    });
    setNewSiteFile({
      name: newFile.name,
      rowCount: source.new_url_count,
      file: newFile,
    });
    setOldFileValidation({
      valid: true,
      warnings: [],
      errors: [],
      rowCount: source.old_url_count,
    });
    setNewFileValidation({
      valid: true,
      warnings: [],
      errors: [],
      rowCount: source.new_url_count,
    });
    setAppliedSourceSessionId(sourceSessionId);
    setError(null);
  }, [appliedSourceSessionId, sourceFilesQuery.data, sourceSessionId]);

  const handleFileUpload = async (file: File, type: 'old' | 'new') => {
    setError(null);
    setPendingWarnings(null);

    const validationResult = await validateFile(file);

    if (type === 'old') {
      setOldFileValidation(validationResult);
      if (!validationResult.valid) {
        setOldSiteFile(null);
        setOldCsvFile(null);
        return;
      }

      const fileToSend = validationResult.convertedFile || file;
      setOldCsvFile(fileToSend);
      setOldSiteFile({
        name: file.name,
        rowCount: validationResult.rowCount,
        file: fileToSend,
      });
      return;
    }

    setNewFileValidation(validationResult);
    if (!validationResult.valid) {
      setNewSiteFile(null);
      setNewCsvFile(null);
      return;
    }

    const fileToSend = validationResult.convertedFile || file;
    setNewCsvFile(fileToSend);
    setNewSiteFile({
      name: file.name,
      rowCount: validationResult.rowCount,
      file: fileToSend,
    });
  };

  const handleFileRemove = (type: 'old' | 'new') => {
    setError(null);
    setPendingWarnings(null);

    if (type === 'old') {
      setOldSiteFile(null);
      setOldCsvFile(null);
      setOldFileValidation(null);
      return;
    }

    setNewSiteFile(null);
    setNewCsvFile(null);
    setNewFileValidation(null);
  };

  const oldRowCount = oldFileValidation?.rowCount ?? oldSiteFile?.rowCount ?? 0;
  const newRowCount = newFileValidation?.rowCount ?? newSiteFile?.rowCount ?? 0;
  const billablePages = Math.max(oldRowCount, newRowCount);
  const bothFilesUploaded = !!oldCsvFile && !!newCsvFile;
  const hasValidationErrors =
    (oldFileValidation && !oldFileValidation.valid) ||
    (newFileValidation && !newFileValidation.valid);

  const pricingEstimateQuery = useQuery({
    queryKey: ['content-match-estimate', billablePages],
    queryFn: () => getPricingEstimate(billablePages),
    enabled: billablePages > 0,
    staleTime: 30_000,
  });

  const estimate = pricingEstimateQuery.data;

  const startRun = async (skipWarningCheck: boolean = false) => {
    if (!oldCsvFile || !newCsvFile) {
      setError('Upload both URL files before starting Content Match.');
      return;
    }

    if (!skipWarningCheck) {
      const oldWarnings = oldFileValidation?.warnings || [];
      const newWarnings = newFileValidation?.warnings || [];
      if (oldWarnings.length > 0 || newWarnings.length > 0) {
        setPendingWarnings({ old: oldWarnings, new: newWarnings });
        return;
      }
    }

    if (!scrapingChecklistAccepted) {
      setError('Confirm scraping readiness before starting Content Match.');
      return;
    }

    setError(null);
    setPendingWarnings(null);
    setIsUploading(true);
    setIsLoading(true);

    try {
      const result = await startContentMatch(oldCsvFile, newCsvFile);
      if (result.session_id) {
        setCurrentSessionId(result.session_id);
      }
    } catch (err) {
      setIsLoading(false);
      setCurrentSessionId(null);
      setError(err instanceof Error ? err.message : 'Unable to start Content Match right now.');
    } finally {
      setIsUploading(false);
    }
  };

  const handleProceedWithWarnings = () => {
    void startRun(true);
  };

  const handleCancelWarnings = () => {
    setPendingWarnings(null);
  };

  const goToAuth = (target: 'signup' | 'login') => {
    const redirectTarget = sourceSessionId
      ? `/content-match?source_session_id=${encodeURIComponent(sourceSessionId)}`
      : '/content-match';
    setAuthRedirect(redirectTarget);
    navigate(`/${target}?redirect=${encodeURIComponent(redirectTarget)}&source=content-match`);
  };

  const startContentMatchOAuth = async (provider: OAuthProvider) => {
    setOauthError('');
    setOauthLoading(provider);
    const redirectTarget = sourceSessionId
      ? `/content-match?source_session_id=${encodeURIComponent(sourceSessionId)}`
      : '/content-match';
    setAuthRedirect(redirectTarget);

    try {
      await startOAuth(provider, redirectTarget, 'content-match');
    } catch (oauthErr) {
      if (oauthErr instanceof Error && oauthErr.message) {
        setOauthError(oauthErr.message);
      } else {
        setOauthError(`Unable to continue with ${provider === 'google' ? 'Google' : 'GitHub'} right now.`);
      }
      setOauthLoading(null);
    }
  };

  const pricingDetails = useMemo(() => {
    if (!estimate || estimate.contact_required) {
      return null;
    }

    return {
      subtotal: formatUsdFromCents(estimate.subtotal_cents),
      rate: estimate.effective_rate_usd,
    };
  }, [estimate]);

  if (isLoading) {
    return (
      <ToolLayout title="Processing">
        <LoadingScreen
          sessionId={currentSessionId}
          pipelineType="content"
          oldUrlCount={oldRowCount}
          newUrlCount={newRowCount}
        />
      </ToolLayout>
    );
  }

  if (!user) {
    return (
      <ToolLayout title="Content Based Matching">
        <section className="mx-auto max-w-3xl space-y-6">
          <div className="text-center">
            <h1 className="text-3xl font-semibold tracking-tight text-foreground md:text-4xl">
              Content Based Redirect Matching
            </h1>
            <p className="mt-3 text-muted-foreground">
              We scrape and analyze your pages to match by actual content, then show a preview before purchase.
            </p>
          </div>

          <Card className="p-6 space-y-4">
            <p className="text-sm text-muted-foreground">
              Sign in to upload files, run Content Match, and review preview results.
            </p>
            <div className="flex flex-col gap-2 sm:flex-row">
              <Button
                variant="outline"
                onClick={() => startContentMatchOAuth('google')}
                disabled={!!oauthLoading}
              >
                {oauthLoading === 'google' ? 'Connecting...' : 'Continue with Google'}
              </Button>
              <Button
                variant="outline"
                onClick={() => startContentMatchOAuth('github')}
                disabled={!!oauthLoading}
              >
                {oauthLoading === 'github' ? 'Connecting...' : 'Continue with GitHub'}
              </Button>
              <Button onClick={() => goToAuth('signup')} disabled={!!oauthLoading}>Sign up with email</Button>
            </div>
            <Button variant="ghost" onClick={() => goToAuth('login')} disabled={!!oauthLoading}>
              Already have an account? Log in
            </Button>
            {oauthError && <p className="text-sm text-destructive">{oauthError}</p>}
          </Card>

          <p className="text-sm text-muted-foreground">
            Just need URL pattern matching?{' '}
            <button
              className="underline underline-offset-4 hover:text-foreground"
              onClick={() => navigate('/url-match')}
            >
              Try our free URL based matching tool →
            </button>
          </p>
        </section>
      </ToolLayout>
    );
  }

  return (
    <ToolLayout title="Content Based Matching">
      <div className="max-w-5xl space-y-6">
        <div className="space-y-2">
          <h1 className="text-3xl font-semibold tracking-tight text-foreground md:text-4xl">
            Content Based Redirect Matching
          </h1>
          <p className="text-muted-foreground">
            We scrape and analyze your pages to match by actual content, not just URLs.
          </p>
          <p className="text-sm text-muted-foreground">
            Just need URL pattern matching?{' '}
            <button
              className="underline underline-offset-4 hover:text-foreground"
              onClick={() => navigate('/url-match')}
            >
              Try our free URL based matching tool →
            </button>
          </p>
        </div>

        {sourceSessionId && (
          <div className="border border-border bg-card p-4 text-sm text-muted-foreground">
            {sourceFilesQuery.isLoading ? (
              'Loading files from your URL Based Matching session...'
            ) : sourceFilesQuery.isError ? (
              'Could not pre-load files from the previous session. Upload files manually to continue.'
            ) : (
              <>
                Files pre-loaded from your previous URL Based Matching session. You can replace either file before starting.
              </>
            )}
          </div>
        )}

        {error && (
          <div className="border border-destructive bg-destructive/10 p-4 text-sm text-destructive">
            {error}
          </div>
        )}

        {pendingWarnings && (
          <div className="border border-yellow-500 bg-yellow-500/10 p-4 flex items-start gap-3">
            <AlertTriangle className="h-5 w-5 text-yellow-500 flex-shrink-0 mt-0.5" />
            <div className="flex-1">
              <div className="font-medium text-yellow-600 dark:text-yellow-400">File warnings detected</div>
              <p className="text-sm text-muted-foreground mt-1">
                These are non-blocking. Continue if the files are correct.
              </p>
              {pendingWarnings.old.length > 0 && (
                <p className="text-sm text-muted-foreground mt-2">Old Site: {pendingWarnings.old.join(' | ')}</p>
              )}
              {pendingWarnings.new.length > 0 && (
                <p className="text-sm text-muted-foreground mt-1">New Site: {pendingWarnings.new.join(' | ')}</p>
              )}
              <div className="flex gap-3 mt-3">
                <Button variant="outline" size="sm" onClick={handleCancelWarnings}>Cancel</Button>
                <Button size="sm" onClick={handleProceedWithWarnings}>Proceed Anyway</Button>
              </div>
            </div>
          </div>
        )}

        <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
          <FileUploadZone
            label="Old Site URLs"
            onFileUpload={(file) => void handleFileUpload(file, 'old')}
            onFileRemove={() => handleFileRemove('old')}
            file={oldSiteFile}
            validationError={oldFileValidation && !oldFileValidation.valid ? oldFileValidation.errors.join(', ') : null}
          />
          <FileUploadZone
            label="New Site URLs"
            onFileUpload={(file) => void handleFileUpload(file, 'new')}
            onFileRemove={() => handleFileRemove('new')}
            file={newSiteFile}
            validationError={newFileValidation && !newFileValidation.valid ? newFileValidation.errors.join(', ') : null}
          />
        </div>

        <Card className="p-5 space-y-3">
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm font-medium text-foreground">Project pricing</p>
            {billablePages > 0 && <p className="text-sm text-muted-foreground">{billablePages.toLocaleString()} billable pages</p>}
          </div>

          {billablePages === 0 ? (
            <p className="text-sm text-muted-foreground">Upload both files to calculate price instantly.</p>
          ) : pricingEstimateQuery.isLoading ? (
            <p className="text-sm text-muted-foreground">Calculating price...</p>
          ) : estimate?.contact_required ? (
            <p className="text-sm text-muted-foreground">Project is above 100,000 pages. Contact sales for enterprise pricing.</p>
          ) : pricingDetails ? (
            <div>
              <p className="text-2xl font-semibold text-foreground">{pricingDetails.subtotal}</p>
              <p className="text-sm text-muted-foreground">Effective rate: ${pricingDetails.rate}/page</p>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">Unable to calculate pricing right now.</p>
          )}

          <div className="rounded-md border border-orange-500/40 bg-orange-500/10 p-4 space-y-2">
            <p className="text-sm font-medium text-foreground">
              Scraping consent required before starting
            </p>
            <p className="text-xs text-muted-foreground">
              Disable active bot/spam protection (Wordfence, Cloudflare challenge mode, etc.) or whitelist RedirX crawler traffic during this run.
            </p>
            <label className="flex items-start gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={scrapingChecklistAccepted}
                onChange={(e) => setScrapingChecklistAccepted(e.target.checked)}
                className="h-4 w-4 rounded border-border accent-primary mt-0.5"
              />
              <span className="text-sm text-foreground">
                I confirm scraping protections are disabled or RedirX crawler traffic is whitelisted.
              </span>
            </label>
          </div>

          <Button
            size="lg"
            className="w-full"
            onClick={() => void startRun(false)}
            disabled={!bothFilesUploaded || !!hasValidationErrors || isUploading || !scrapingChecklistAccepted}
          >
            {isUploading && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
            {isUploading ? 'Starting Content Match...' : 'Start Content Match →'}
          </Button>

          <p className="text-xs text-muted-foreground text-center">
            Results are free to preview. Payment is required to view full URLs and export.
          </p>
        </Card>
      </div>
    </ToolLayout>
  );
}
