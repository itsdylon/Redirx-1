import { uploadCSVs, type ContentUrlCapError } from "../api/pipeline";
import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { usePostHog } from '@posthog/react';
import { useAuth } from '../contexts/AuthContext';
import { useOnboarding } from '../contexts/OnboardingContext';
import { DashboardLayout } from './DashboardLayout';
import { ToolLayout } from './ToolLayout';
import { FileUploadZone } from './FileUploadZone';
import { DomainDiscoveryPanel } from './DomainDiscoveryPanel';
import type { DiscoveryResponse } from '../api/discovery';
import { LoadingScreen } from './LoadingScreen';
import { Button } from './ui/button';
import { AlertTriangle, Loader2, Zap, Search, Info, ShieldAlert } from 'lucide-react';
import { validateFile, FileValidationResult } from '../utils/validation';
import { appQueryClient } from '../queries/queryClient';
import { queryKeys } from '../queries/queryKeys';
import { CONTENT_MAX_URLS_PER_SITE } from '../api/config';

interface FileData {
  name: string;
  rowCount: number;
  file: File;
}

interface BeginMatchingOptions {
  force?: boolean;
  skipWarningCheck?: boolean;
  skipScrapingWarning?: boolean;
}

const SAMPLE_OLD_URLS = [
  "https://legacy-example.com/about",
  "https://legacy-example.com/services/seo-audit",
  "https://legacy-example.com/case-studies/retail-growth",
  "https://legacy-example.com/resources/redirect-guide",
  "https://legacy-example.com/blog/platform-migration-checklist",
  "https://legacy-example.com/pricing/agency",
  "https://legacy-example.com/integrations/hubspot",
  "https://legacy-example.com/docs/import",
];

const SAMPLE_NEW_URLS = [
  "https://new-example.com/about",
  "https://new-example.com/services/technical-seo-audit",
  "https://new-example.com/case-studies/retail-traffic-growth",
  "https://new-example.com/resources/301-redirect-guide",
  "https://new-example.com/blog/website-migration-checklist",
  "https://new-example.com/pricing/partners",
  "https://new-example.com/integrations/crm",
  "https://new-example.com/help/uploading-csvs",
];

function buildSampleCsv(urls: string[]): string {
  return ["url", ...urls].join("\n");
}

const LARGE_DATASET_WARNING_PREFIX = 'Large dataset detected';

interface UploadPageProps {
  quickOnly?: boolean;
  flowSource?: string;
  layoutVariant?: 'dashboard' | 'tool';
}

export function UploadPage({
  quickOnly = false,
  flowSource = 'upload',
  layoutVariant = 'dashboard',
}: UploadPageProps = {}) {
  const navigate = useNavigate();
  const posthog = usePostHog();
  const [searchParams] = useSearchParams();
  const { user } = useAuth();
  const {
    onboarding,
    startOnboarding,
    completeStep,
    createSampleSession,
  } = useOnboarding();
  const userPlan = user?.plan || 'free';
  const isFreeUser = userPlan === 'free';
  const isQuickOnlyMode = quickOnly;
  const [pipelineType, setPipelineType] = useState<'content' | 'url_only'>((
    isFreeUser || isQuickOnlyMode
  ) ? 'url_only' : 'content');
  const [isLoading, setIsLoading] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [oldSiteFile, setOldSiteFile] = useState<FileData | null>(null);
  const [newSiteFile, setNewSiteFile] = useState<FileData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [contentCapApiError, setContentCapApiError] = useState<ContentUrlCapError | null>(null);
  const [duplicateSessionId, setDuplicateSessionId] = useState<string | null>(null);

  // Raw file objects needed for API
  const [oldCsvFile, setOldCsvFile] = useState<File | null>(null);
  const [newCsvFile, setNewCsvFile] = useState<File | null>(null);

  // Validation state
  const [oldFileValidation, setOldFileValidation] = useState<FileValidationResult | null>(null);
  const [newFileValidation, setNewFileValidation] = useState<FileValidationResult | null>(null);
  const [pendingWarnings, setPendingWarnings] = useState<{ old: string[], new: string[] } | null>(null);
  const [showCoachmarks, setShowCoachmarks] = useState(false);
  const [ingestMode, setIngestMode] = useState<'domains' | 'csv'>('domains');

  const tutorialFromQuery = searchParams.get('tutorial') === '1';
  const sampleFromQuery = searchParams.get('sample') === '1';
  const tutorialPath = onboarding?.onboarding_state.path;
  const tutorialIntent = tutorialFromQuery || tutorialPath === 'real' || tutorialPath === 'sample';
  const tutorialActive = !!onboarding &&
    tutorialIntent &&
    (onboarding.onboarding_status === 'in_progress' || onboarding.onboarding_status === 'not_started');
  const sampleTutorialActive = tutorialActive && (sampleFromQuery || tutorialPath === 'sample');
  const effectivePipelineType = (isFreeUser || isQuickOnlyMode) ? 'url_only' : pipelineType;
  const Layout = layoutVariant === 'tool' ? ToolLayout : DashboardLayout;
  const layoutTitle = isQuickOnlyMode ? 'Quick Match' : 'Upload CSV Files';
  const oldRowCount = oldFileValidation?.rowCount ?? oldSiteFile?.rowCount ?? 0;
  const newRowCount = newFileValidation?.rowCount ?? newSiteFile?.rowCount ?? 0;
  const oldContentCapExceeded = effectivePipelineType === 'content' && oldRowCount > CONTENT_MAX_URLS_PER_SITE;
  const newContentCapExceeded = effectivePipelineType === 'content' && newRowCount > CONTENT_MAX_URLS_PER_SITE;
  const hasLocalContentCapViolation = oldContentCapExceeded || newContentCapExceeded;

  useEffect(() => {
    if (isFreeUser || isQuickOnlyMode) {
      setPipelineType('url_only');
    }
  }, [isFreeUser, isQuickOnlyMode]);

  useEffect(() => {
    if (!tutorialActive) {
      setShowCoachmarks(false);
      return;
    }
    setShowCoachmarks(true);
  }, [tutorialActive]);

  // The tutorial walks through the CSV flow, so keep its copy accurate.
  useEffect(() => {
    if (tutorialActive) {
      setIngestMode('csv');
    }
  }, [tutorialActive]);

  useEffect(() => {
    if (!tutorialActive || onboarding?.onboarding_status !== 'not_started') return;
    startOnboarding();
  }, [onboarding?.onboarding_status, startOnboarding, tutorialActive]);

  useEffect(() => {
    if (!tutorialActive || !showCoachmarks) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setShowCoachmarks(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [showCoachmarks, tutorialActive]);

  useEffect(() => {
    if (!sampleTutorialActive) return;
    if (oldCsvFile && newCsvFile && oldSiteFile && newSiteFile) return;

    const oldSampleFile = new File(
      [buildSampleCsv(SAMPLE_OLD_URLS)],
      "sample-old-urls.csv",
      { type: "text/csv" }
    );
    const newSampleFile = new File(
      [buildSampleCsv(SAMPLE_NEW_URLS)],
      "sample-new-urls.csv",
      { type: "text/csv" }
    );

    setOldCsvFile(oldSampleFile);
    setNewCsvFile(newSampleFile);
    setOldSiteFile({
      name: oldSampleFile.name,
      rowCount: SAMPLE_OLD_URLS.length,
      file: oldSampleFile,
    });
    setNewSiteFile({
      name: newSampleFile.name,
      rowCount: SAMPLE_NEW_URLS.length,
      file: newSampleFile,
    });
    setOldFileValidation({
      valid: true,
      warnings: [],
      errors: [],
      rowCount: SAMPLE_OLD_URLS.length,
    });
    setNewFileValidation({
      valid: true,
      warnings: [],
      errors: [],
      rowCount: SAMPLE_NEW_URLS.length,
    });
  }, [newCsvFile, newSiteFile, oldCsvFile, oldSiteFile, sampleTutorialActive]);

  // Scraping warning state (Deep Match only)
  const [showScrapingWarning, setShowScrapingWarning] = useState(false);
  const [scrapingWarningAcknowledged, setScrapingWarningAcknowledged] = useState(false);

  const handleFileUpload = async (file: File, type: 'old' | 'new') => {
    setContentCapApiError(null);
    setPendingWarnings(null);
    setError(null);

    // Clear previous errors for this file type
    if (type === 'old') {
      setOldFileValidation(null);
      setOldSiteFile(null);
      setOldCsvFile(null);
    } else {
      setNewFileValidation(null);
      setNewSiteFile(null);
      setNewCsvFile(null);
    }

    // Validate the file (CSV, TXT, XML, or XLSX)
    const validationResult = await validateFile(file);

    if (type === 'old') {
      setOldFileValidation(validationResult);
    } else {
      setNewFileValidation(validationResult);
    }

    // If validation failed, don't proceed with file upload
    if (!validationResult.valid) {
      return;
    }

    // Use the converted file for XML/XLSX, otherwise use the original
    const fileToSend = validationResult.convertedFile || file;

    const fileData: FileData = {
      name: file.name,
      rowCount: validationResult.rowCount,
      file: fileToSend,
    };

    if (type === 'old') {
      setOldCsvFile(fileToSend);
      setOldSiteFile(fileData);
    } else {
      setNewCsvFile(fileToSend);
      setNewSiteFile(fileData);
    }
  };

  const handleDomainDiscovered = (side: 'old' | 'new', result: DiscoveryResponse | null) => {
    setContentCapApiError(null);
    setPendingWarnings(null);
    setError(null);
    setDuplicateSessionId(null);

    if (!result) {
      handleFileRemove(side);
      return;
    }

    // Bridge discovered URLs into the existing file-based submit flow.
    const host = new URL(result.root_url).hostname;
    const file = new File([result.urls.join('\n')], `${host}-discovered.csv`, {
      type: 'text/csv',
    });
    const fileData: FileData = {
      name: `${host} — ${result.count.toLocaleString()} pages`,
      rowCount: result.count,
      file,
    };
    const validation: FileValidationResult = {
      valid: true,
      warnings: [],
      errors: [],
      rowCount: result.count,
    };

    if (side === 'old') {
      setOldCsvFile(file);
      setOldSiteFile(fileData);
      setOldFileValidation(validation);
    } else {
      setNewCsvFile(file);
      setNewSiteFile(fileData);
      setNewFileValidation(validation);
    }
  };

  const handleIngestModeChange = (mode: 'domains' | 'csv') => {
    if (mode === ingestMode) return;
    setIngestMode(mode);
    handleFileRemove('old');
    handleFileRemove('new');
  };

  const handleFileRemove = (type: 'old' | 'new') => {
    setContentCapApiError(null);
    setPendingWarnings(null);
    setError(null);
    setDuplicateSessionId(null);
    setShowScrapingWarning(false);
    setScrapingWarningAcknowledged(false);

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

  const handleBeginMatching = async ({
    force = false,
    skipWarningCheck = false,
    skipScrapingWarning = false,
  }: BeginMatchingOptions = {}) => {
    if (sampleTutorialActive) {
      setError(null);
      setDuplicateSessionId(null);
      setPendingWarnings(null);
      setIsUploading(true);
      setIsLoading(true);

      try {
        const sample = await createSampleSession();
        if (sample.session_id) {
          setCurrentSessionId(sample.session_id);
        }
        void appQueryClient.invalidateQueries({ queryKey: queryKeys.dashboard.summary });
        void appQueryClient.invalidateQueries({ queryKey: queryKeys.sessions.all });
        await completeStep('generate_mappings');
        setIsUploading(false);
      } catch (err) {
        console.error(err);
        setIsUploading(false);
        setIsLoading(false);
        setCurrentSessionId(null);
        setError(err instanceof Error ? err.message : "Failed to prepare sample tutorial session.");
      }
      return;
    }
    if (!oldCsvFile || !newCsvFile) {
      setError("Upload both CSV files first.");
      return;
    }

    if (effectivePipelineType === 'content' && hasLocalContentCapViolation) {
      return;
    }

    // Check for warnings if not already confirmed
    if (!skipWarningCheck) {
      const suppressLargeDatasetWarnings = effectivePipelineType === 'content' && hasLocalContentCapViolation;
      const oldWarnings = suppressLargeDatasetWarnings
        ? (oldFileValidation?.warnings || []).filter((w) => !w.startsWith(LARGE_DATASET_WARNING_PREFIX))
        : (oldFileValidation?.warnings || []);
      const newWarnings = suppressLargeDatasetWarnings
        ? (newFileValidation?.warnings || []).filter((w) => !w.startsWith(LARGE_DATASET_WARNING_PREFIX))
        : (newFileValidation?.warnings || []);

      if (oldWarnings.length > 0 || newWarnings.length > 0) {
        setPendingWarnings({ old: oldWarnings, new: newWarnings });
        return;
      }
    }

    // Show scraping warning for Deep Match (content pipeline)
    if (effectivePipelineType === 'content' && !skipScrapingWarning) {
      setShowScrapingWarning(true);
      setScrapingWarningAcknowledged(false);
      return;
    }

    // Clear previous errors
    setError(null);
    setContentCapApiError(null);
    setDuplicateSessionId(null);
    setPendingWarnings(null);
    setIsUploading(true);
    setIsLoading(true);

    if (effectivePipelineType === 'url_only') {
      posthog?.capture('quick_match_upload_started', {
        plan: userPlan,
        source: flowSource,
        pipeline_type: effectivePipelineType,
        old_url_count: oldRowCount,
        new_url_count: newRowCount,
      });
    }

    try {
      const result = await uploadCSVs(oldCsvFile, newCsvFile, force, effectivePipelineType);

      console.log("Pipeline Response:", result);

      // Check if this is a duplicate run
      if (result.is_duplicate && !force) {
        console.log("Duplicate detected, showing warning");
        setIsUploading(false);
        setIsLoading(false);
        setDuplicateSessionId(result.session_id);
        return;
      }

      // Store session ID - LoadingScreen will poll and navigate when complete
      if (result.session_id) {
        setCurrentSessionId(result.session_id);
      }
      void appQueryClient.invalidateQueries({ queryKey: queryKeys.dashboard.summary });
      void appQueryClient.invalidateQueries({ queryKey: queryKeys.sessions.all });
      if (tutorialActive) {
        await completeStep('generate_mappings');
      }
      setIsUploading(false);
    } catch (err) {
      console.error(err);
      setIsUploading(false);
      setIsLoading(false);
      setCurrentSessionId(null);

      if (err && typeof err === 'object' && 'type' in err && (err as ContentUrlCapError).type === 'content_url_cap_exceeded') {
        setContentCapApiError(err as ContentUrlCapError);
        setError(null);
      } else if (err instanceof Error) {
        setError(err.message);
      } else {
        setError("An unexpected error occurred.");
      }
    }
  };

  const handleProceedAnyway = () => {
    handleBeginMatching({ force: true, skipWarningCheck: true, skipScrapingWarning: true });
  };

  const handleProceedWithWarnings = () => {
    handleBeginMatching({ skipWarningCheck: true });
  };

  const handleCancelWarnings = () => {
    setPendingWarnings(null);
  };

  const handleConfirmScrapingWarning = () => {
    setShowScrapingWarning(false);
    handleBeginMatching({ skipWarningCheck: true, skipScrapingWarning: true });
  };

  const handleCancelScrapingWarning = () => {
    setShowScrapingWarning(false);
    setScrapingWarningAcknowledged(false);
  };

  const handlePipelineTypeChange = (nextType: 'content' | 'url_only') => {
    if (isQuickOnlyMode) {
      return;
    }
    setPipelineType(nextType);
    setPendingWarnings(null);
    setContentCapApiError(null);
    setError(null);
  };

  const handleSwitchToQuickMatch = () => {
    if (!isFreeUser) {
      setPipelineType('url_only');
    }
    setPendingWarnings(null);
    setContentCapApiError(null);
    setError(null);
  };

  const bothFilesUploaded = oldSiteFile && newSiteFile;
  const hasValidationErrors =
    (oldFileValidation && !oldFileValidation.valid) ||
    (newFileValidation && !newFileValidation.valid) ||
    hasLocalContentCapViolation;
  const defaultContentCapMessage =
    `Deep Match has a per-file limit of ${CONTENT_MAX_URLS_PER_SITE.toLocaleString()} URLs. ` +
    'Split your CSV or switch to Quick Match.';
  const contentCapPanelData = hasLocalContentCapViolation
    ? {
        message: defaultContentCapMessage,
        oldCount: oldRowCount,
        newCount: newRowCount,
        maxOld: CONTENT_MAX_URLS_PER_SITE,
        maxNew: CONTENT_MAX_URLS_PER_SITE,
        showOld: oldContentCapExceeded,
        showNew: newContentCapExceeded,
      }
    : contentCapApiError
      ? {
          message: contentCapApiError.message || defaultContentCapMessage,
          oldCount: contentCapApiError.old_url_count,
          newCount: contentCapApiError.new_url_count,
          maxOld: contentCapApiError.max_old_urls,
          maxNew: contentCapApiError.max_new_urls,
          showOld: contentCapApiError.affected_file === 'old' || contentCapApiError.affected_file === 'both',
          showNew: contentCapApiError.affected_file === 'new' || contentCapApiError.affected_file === 'both',
        }
      : null;

  // Show loading screen when processing
  if (isLoading) {
    return (
      <Layout title="Processing">
        <LoadingScreen
          sessionId={currentSessionId}
          tutorialMode={sampleTutorialActive}
          minDisplayMs={sampleTutorialActive ? 4500 : 0}
        />
      </Layout>
    );
  }

  return (
    <Layout title={layoutTitle}>
      <div className="max-w-5xl">
          {/* Headline / Subtitle */}
          {isQuickOnlyMode ? (
            <div className="mb-8">
              <h1 className="text-3xl font-semibold tracking-tight text-foreground md:text-4xl">
                Free Redirect Map Generator
              </h1>
              <p className="mt-3 text-muted-foreground">
                Paste two domains. Get matched redirects in seconds.
              </p>
            </div>
          ) : (
            <div className="mb-8">
              <p className="text-muted-foreground">
                {sampleTutorialActive
                  ? 'Sample CSV files are preloaded. Click Begin Matching to run the tutorial sample and continue to review.'
                  : 'Upload URL lists from your old and new site to begin the redirect mapping process.'}
              </p>
            </div>
          )}

          {/* Pipeline Type Selector / Tier Banner */}
          {isQuickOnlyMode ? (
            <div className="mb-6 border border-blue-500/30 bg-blue-500/5 p-4 space-y-4">
              <div>
                <div>
                  <div className="font-medium text-blue-600 dark:text-blue-400">Quick Match Workflow</div>
                  <p className="text-sm text-muted-foreground mt-1">
                    Run URL-only matching now, then unlock Deep Match from results only if you need stronger fixes.
                  </p>
                </div>
              </div>
              <div className="grid grid-cols-1 gap-2 text-sm md:grid-cols-3">
                <div className="border border-border bg-card p-3">
                  <div className="font-medium text-foreground">1. Upload</div>
                  <p className="text-xs text-muted-foreground mt-1">Drop old and new URL files.</p>
                </div>
                <div className="border border-border bg-card p-3">
                  <div className="font-medium text-foreground">2. Review</div>
                  <p className="text-xs text-muted-foreground mt-1">Validate confidence and approve mappings.</p>
                </div>
                <div className="border border-border bg-card p-3">
                  <div className="font-medium text-foreground">3. Export</div>
                  <p className="text-xs text-muted-foreground mt-1">Download redirect rules in your format.</p>
                </div>
              </div>
            </div>
          ) : isFreeUser ? (
            <div className="mb-6 border border-blue-500/30 bg-blue-500/5 p-4 flex items-start gap-3">
              <Info className="h-5 w-5 text-blue-500 flex-shrink-0 mt-0.5" />
              <div>
                <div className="font-medium text-blue-600 dark:text-blue-400">Free Plan: Quick Match</div>
                <p className="text-sm text-muted-foreground mt-1">
                  Quick Match is always free. Unlock Deep Match per project or choose the Agency plan for recurring migrations.
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-3"
                  onClick={() => navigate('/pricing')}
                >
                  View Pricing
                </Button>
              </div>
            </div>
          ) : (
            <div className="mb-6 grid grid-cols-2 gap-4">
              <button
                onClick={() => handlePipelineTypeChange('content')}
                className={`border p-4 text-left transition-colors ${
                  pipelineType === 'content'
                    ? 'border-primary bg-primary/5'
                    : 'border-border bg-card hover:bg-accent'
                }`}
              >
                <div className="flex items-center gap-2 mb-1">
                  <Zap className="h-4 w-4 text-primary" />
                  <span className="font-medium text-foreground">Deep Match</span>
                </div>
                <p className="text-sm text-muted-foreground">
                  Scrapes page content and uses AI embeddings for semantic matching. Most accurate.
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  Available for Agency plans and unlocked project runs.
                </p>
              </button>
              <button
                onClick={() => handlePipelineTypeChange('url_only')}
                className={`border p-4 text-left transition-colors ${
                  pipelineType === 'url_only'
                    ? 'border-primary bg-primary/5'
                    : 'border-border bg-card hover:bg-accent'
                }`}
              >
                <div className="flex items-center gap-2 mb-1">
                  <Search className="h-4 w-4 text-primary" />
                  <span className="font-medium text-foreground">Quick Match</span>
                  <span className="text-xs bg-green-500/10 text-green-600 dark:text-green-400 px-1.5 py-0.5 rounded font-medium">Free</span>
                </div>
                <p className="text-sm text-muted-foreground">
                  URL pattern matching only. Fastest, no scraping required.
                </p>
              </button>
            </div>
          )}

          {/* Content URL Cap Error */}
          {contentCapPanelData && (
            <div className="mb-6 border border-destructive bg-destructive/10 p-4 flex items-start gap-3">
              <AlertTriangle className="h-5 w-5 text-destructive flex-shrink-0 mt-0.5" />
              <div className="flex-1">
                <div className="font-medium text-destructive">Deep Match URL Limit Reached</div>
                <p className="text-sm text-muted-foreground mt-1">{contentCapPanelData.message}</p>

                {contentCapPanelData.showOld && (
                  <p className="text-sm text-foreground mt-2">
                    Old Site CSV has {contentCapPanelData.oldCount.toLocaleString()} URLs; Deep Match allows up to {contentCapPanelData.maxOld.toLocaleString()}.
                  </p>
                )}
                {contentCapPanelData.showNew && (
                  <p className="text-sm text-foreground mt-1">
                    New Site CSV has {contentCapPanelData.newCount.toLocaleString()} URLs; Deep Match allows up to {contentCapPanelData.maxNew.toLocaleString()}.
                  </p>
                )}

                {!isFreeUser && effectivePipelineType === 'content' && (
                  <div className="flex gap-3 mt-3">
                    <Button
                      variant="default"
                      size="sm"
                      onClick={handleSwitchToQuickMatch}
                    >
                      Switch to Quick Match
                    </Button>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* General Error */}
          {error && (
            <div className="mb-6 border border-destructive bg-destructive/10 p-4 text-destructive">
              {error}
            </div>
          )}

          {/* Duplicate Warning */}
          {duplicateSessionId && (
            <div className="mb-6 border border-yellow-500 bg-yellow-500/10 p-4 flex items-start gap-3">
              <AlertTriangle className="h-5 w-5 text-yellow-500 flex-shrink-0 mt-0.5" />
              <div className="flex-1">
                <div className="font-medium text-yellow-600 dark:text-yellow-400">Duplicate Request Detected</div>
                <p className="text-sm text-muted-foreground mt-1">
                  You've already uploaded these exact files before. You can view the existing results or force a new run.
                </p>
                <div className="flex gap-3 mt-3">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => navigate(`/review/${duplicateSessionId}`)}
                  >
                    View Existing Results
                  </Button>
                  <Button
                    variant="default"
                    size="sm"
                    onClick={handleProceedAnyway}
                  >
                    Proceed Anyway
                  </Button>
                </div>
              </div>
            </div>
          )}

          {/* Validation Warnings */}
          {pendingWarnings && (
            <div className="mb-6 border border-yellow-500 bg-yellow-500/10 p-4 flex items-start gap-3">
              <AlertTriangle className="h-5 w-5 text-yellow-500 flex-shrink-0 mt-0.5" />
              <div className="flex-1">
                <div className="font-medium text-yellow-600 dark:text-yellow-400">File Warnings Detected</div>
                <p className="text-sm text-muted-foreground mt-1">
                  The following non-blocking warnings were found in your files. You can continue, but processing may take longer or encounter issues.
                </p>
                {pendingWarnings.old.length > 0 && (
                  <div className="mt-3">
                    <div className="text-sm font-medium text-yellow-600 dark:text-yellow-400">Old Site CSV:</div>
                    <ul className="list-disc list-inside text-sm text-muted-foreground mt-1">
                      {pendingWarnings.old.map((warning, idx) => (
                        <li key={idx}>{warning}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {pendingWarnings.new.length > 0 && (
                  <div className="mt-3">
                    <div className="text-sm font-medium text-yellow-600 dark:text-yellow-400">New Site CSV:</div>
                    <ul className="list-disc list-inside text-sm text-muted-foreground mt-1">
                      {pendingWarnings.new.map((warning, idx) => (
                        <li key={idx}>{warning}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <div className="flex gap-3 mt-3">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleCancelWarnings}
                  >
                    Cancel
                  </Button>
                  <Button
                    variant="default"
                    size="sm"
                    onClick={handleProceedWithWarnings}
                  >
                    Proceed Anyway
                  </Button>
                </div>
              </div>
            </div>
          )}

          {/* Scraping Warning (Deep Match only) */}
          {showScrapingWarning && (
            <div className="mb-6 border border-orange-500 bg-orange-500/10 p-4 flex items-start gap-3">
              <ShieldAlert className="h-5 w-5 text-orange-500 flex-shrink-0 mt-0.5" />
              <div className="flex-1">
                <div className="font-medium text-orange-600 dark:text-orange-400">Disable Rate Limiting Before Scanning</div>
                <p className="text-sm text-muted-foreground mt-1">
                  Deep Match will send many requests to scrape pages on your old and new sites. If your sites have anti-spam, rate limiting, or bot protection enabled, the requests may be blocked — which can cause incomplete or failed results.
                </p>
                <div className="mt-3 text-sm text-muted-foreground">
                  <p className="font-medium text-foreground mb-2">Before proceeding, make sure you have:</p>
                  <ul className="list-disc list-inside space-y-1">
                    <li>Disabled rate limiting or WAF rules on both sites</li>
                    <li>Whitelisted automated traffic (e.g., Cloudflare Bot Fight Mode, Sucuri, Wordfence)</li>
                  </ul>
                  <p className="mt-2 text-xs text-muted-foreground">
                    Pages behind CAPTCHAs or challenge screens will not be scraped unless these protections are temporarily disabled. You can re-enable all protections after the scan completes.
                  </p>
                </div>
                <label className="flex items-center gap-2 mt-4 cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={scrapingWarningAcknowledged}
                    onChange={(e) => setScrapingWarningAcknowledged(e.target.checked)}
                    className="h-4 w-4 rounded border-border accent-primary"
                  />
                  <span className="text-sm text-foreground">
                    I've disabled rate limiting and bot protection on my sites
                  </span>
                </label>
                <div className="flex gap-3 mt-3">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleCancelScrapingWarning}
                  >
                    Cancel
                  </Button>
                  <Button
                    variant="default"
                    size="sm"
                    disabled={!scrapingWarningAcknowledged}
                    onClick={handleConfirmScrapingWarning}
                  >
                    Proceed with Deep Match
                  </Button>
                </div>
              </div>
            </div>
          )}

          {/* Upload Zones */}
          {tutorialActive && showCoachmarks && (
            <div className="mb-4 border border-[#26D99D] bg-[#26D99D]/16 dark:bg-[#26D99D]/24 p-3 text-sm text-[#064731] dark:text-[#E9FFF8] font-medium">
              {sampleTutorialActive
                ? <>Step 2 of 4: Sample CSV files are ready. Click Begin Matching to run the sample job. Press <kbd className="mx-1 rounded border border-[#26D99D]/70 px-1 py-0.5 text-xs font-semibold">Esc</kbd> to hide these hints.</>
                : <>Step 2 of 4: Upload both CSV files, then run matching. Press <kbd className="mx-1 rounded border border-[#26D99D]/70 px-1 py-0.5 text-xs font-semibold">Esc</kbd> to hide these hints.</>
              }
            </div>
          )}
          {/* Ingestion mode tabs */}
          <div className="mb-4 flex items-center gap-1 border border-border bg-muted/40 p-1 rounded-lg w-fit">
            <button
              type="button"
              onClick={() => handleIngestModeChange('domains')}
              className={`px-4 py-1.5 text-sm rounded-md transition-colors ${
                ingestMode === 'domains'
                  ? 'bg-background text-foreground font-medium shadow-sm'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              Paste Domains
            </button>
            <button
              type="button"
              onClick={() => handleIngestModeChange('csv')}
              className={`px-4 py-1.5 text-sm rounded-md transition-colors ${
                ingestMode === 'csv'
                  ? 'bg-background text-foreground font-medium shadow-sm'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              Upload Files
            </button>
          </div>

          {ingestMode === 'domains' && (
            <div className="mb-8">
              <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
                <DomainDiscoveryPanel
                  side="old"
                  label="Old Site Domain"
                  onDiscovered={handleDomainDiscovered}
                />
                <DomainDiscoveryPanel
                  side="new"
                  label="New Site Domain"
                  onDiscovered={handleDomainDiscovered}
                />
              </div>
              <p className="mt-3 text-xs text-muted-foreground">
                We find pages via your sitemap, platform APIs (WordPress, Shopify), or a
                site crawl. Have an export from Screaming Frog or your CMS? Switch to
                Upload Files.
              </p>
            </div>
          )}

          {ingestMode === 'csv' && (
          <div className="grid grid-cols-2 gap-6 mb-8">
            <div className={tutorialActive && showCoachmarks ? 'rounded-md ring-2 ring-[#26D99D]/75 p-2 bg-[#26D99D]/8 dark:bg-[#26D99D]/14' : ''}>
              <FileUploadZone
                label="Old Site CSV"
                onFileUpload={(file) => handleFileUpload(file, 'old')}
                onFileRemove={() => handleFileRemove('old')}
                file={oldSiteFile}
                validationError={oldFileValidation && !oldFileValidation.valid ? oldFileValidation.errors.join(', ') : null}
              />
              {contentCapPanelData?.showOld && effectivePipelineType === 'content' && (
                <p className="mt-2 text-xs text-destructive">
                  Old Site CSV has {contentCapPanelData.oldCount.toLocaleString()} URLs; Deep Match allows up to {contentCapPanelData.maxOld.toLocaleString()}.
                </p>
              )}
            </div>
            <div className={tutorialActive && showCoachmarks ? 'rounded-md ring-2 ring-[#26D99D]/75 p-2 bg-[#26D99D]/8 dark:bg-[#26D99D]/14' : ''}>
              <FileUploadZone
                label="New Site CSV"
                onFileUpload={(file) => handleFileUpload(file, 'new')}
                onFileRemove={() => handleFileRemove('new')}
                file={newSiteFile}
                validationError={newFileValidation && !newFileValidation.valid ? newFileValidation.errors.join(', ') : null}
              />
              {contentCapPanelData?.showNew && effectivePipelineType === 'content' && (
                <p className="mt-2 text-xs text-destructive">
                  New Site CSV has {contentCapPanelData.newCount.toLocaleString()} URLs; Deep Match allows up to {contentCapPanelData.maxNew.toLocaleString()}.
                </p>
              )}
            </div>
          </div>
          )}

          {/* File Status */}
          {bothFilesUploaded && (
            <div className="mb-8 border border-border bg-card p-6">
              <div className="grid grid-cols-2 gap-6">
                <div>
                  <div className="text-sm text-muted-foreground mb-1">Old Site</div>
                  <div className="text-foreground">{oldSiteFile.name}</div>
                  <div className="text-sm text-muted-foreground">{oldSiteFile.rowCount} rows</div>
                </div>
                <div>
                  <div className="text-sm text-muted-foreground mb-1">New Site</div>
                  <div className="text-foreground">{newSiteFile.name}</div>
                  <div className="text-sm text-muted-foreground">{newSiteFile.rowCount} rows</div>
                </div>
              </div>
            </div>
          )}

          {/* Begin Matching Button */}
          <div>
            {tutorialActive && showCoachmarks && (
              <p className="text-xs text-[#0B6B4C] dark:text-[#D8FFF2] mb-2 font-medium">
                {sampleTutorialActive
                  ? 'This runs the preloaded sample files and moves you into a short processing step before review.'
                  : 'This button moves you to processing and marks the “Generate mappings” tutorial step complete.'}
              </p>
            )}
            <Button
              onClick={() => handleBeginMatching()}
              disabled={!bothFilesUploaded || hasValidationErrors || isUploading}
              size="lg"
              className={`w-full ${tutorialActive && showCoachmarks ? 'ring-2 ring-[#26D99D]/85 ring-offset-2 ring-offset-background' : ''}`}
            >
              {isUploading && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
              {isUploading
                ? 'Processing...'
                : (isFreeUser || isQuickOnlyMode)
                  ? 'Begin Quick Match →'
                  : pipelineType === 'content'
                    ? 'Begin Deep Match →'
                    : 'Begin Quick Match →'
              }
            </Button>
            {hasValidationErrors && (
              <p className="text-sm text-destructive mt-2 text-center">
                {hasLocalContentCapViolation && effectivePipelineType === 'content'
                  ? 'Deep Match URL limit exceeded. Split your CSVs or switch to Quick Match.'
                  : 'Please fix validation errors before proceeding'}
              </p>
            )}
          </div>
      </div>
    </Layout>
  );
}
