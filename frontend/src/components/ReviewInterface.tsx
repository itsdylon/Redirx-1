import { useState, useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import { usePostHog } from '@posthog/react';
import { useHotkeys } from 'react-hotkeys-hook';
import { DashboardLayout } from './DashboardLayout';
import { ToolLayout } from './ToolLayout';
import { StatsBar } from './StatsBar';
import { ReviewToolbar } from './ReviewToolbar';
import { RedirectTable } from './RedirectTable';
import { InlineEditDialog } from './InlineEditDialog';
import { ExportModal } from './ExportModal';
import { KeyboardShortcutsDialog } from './KeyboardShortcutsDialog';
import { Button } from './ui/button';
import { Keyboard, Loader2 } from 'lucide-react';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from './ui/tooltip';
import { toast } from 'sonner';
import {
  getResults,
  type ResultsResponse,
} from '../api/pipeline';
import { getProjectUnlockStatus, type ProjectUnlockStatus } from '../api/billing';
import { isMac } from '../lib/keyboard';
import { isEnterprisePlan } from '../lib/plans';
import { useOnboarding } from '../contexts/OnboardingContext';
import { useAuth } from '../contexts/AuthContext';
import { DeepMatchPrompt } from './DeepMatchPrompt';
import { queryKeys } from '../queries/queryKeys';
import { handleUnauthorizedAndRedirect } from '../queries/auth';
import { buildConversionEventProps } from '../lib/analyticsAttribution';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import {
  Pagination,
  PaginationContent,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from './ui/pagination';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';

export interface RedirectMapping {
  id: string;
  oldUrl: string;
  newUrl: string;
  confidence: number;
  confidenceBand: 'high' | 'medium' | 'low';
  matchScore: number;
  matchType?: string;
  approved: boolean;
  warnings: string[];
  pathSimilarity: number;
  titleSimilarity: number;
  contentSimilarity: number;
  previewRedacted?: boolean;
}

interface ReviewInterfaceProps {
  layoutVariant?: 'dashboard' | 'tool';
}

export function ReviewInterface({ layoutVariant = 'dashboard' }: ReviewInterfaceProps = {}) {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const posthog = usePostHog();
  const { user } = useAuth();
  const hasUser = !!user;
  const [searchParams] = useSearchParams();
  const {
    onboarding,
    completeStep,
    completeOnboarding,
    isStepCompleted,
  } = useOnboarding();
  const [redirects, setRedirects] = useState<RedirectMapping[]>([]);
  const [selectedRows, setSelectedRows] = useState<Set<string>>(new Set());
  const [expandedRow, setExpandedRow] = useState<string | null>(null);
  const [editingRow, setEditingRow] = useState<RedirectMapping | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [confidenceFilter, setConfidenceFilter] = useState<string>('all');
  const [currentPage, setCurrentPage] = useState(1);
  const [exportModalOpen, setExportModalOpen] = useState(false);
  const [keyboardShortcutsOpen, setKeyboardShortcutsOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sortOption, setSortOption] = useState<string>('confidence-desc');
  const [showExactMatches, setShowExactMatches] = useState(true);
  const [pipelineType, setPipelineType] = useState<string>('content');
  const [showTutorialSuccess, setShowTutorialSuccess] = useState(false);
  const [showCoachmarks, setShowCoachmarks] = useState(false);
  const [samplePreparationActive, setSamplePreparationActive] = useState(false);
  const PAGE_SIZE = 25;
  const Layout = layoutVariant === 'tool' ? ToolLayout : DashboardLayout;
  const layoutTitle = pipelineType === 'content' ? 'Content Match Results' : 'URL Based Matching Results';
  const enterpriseUser = isEnterprisePlan(user?.plan);

  // Refs for keyboard shortcuts
  const searchInputRef = useRef<HTMLInputElement>(null);
  const sampleGenerateMarkedRef = useRef(false);
  const tutorialReviewMarkedRef = useRef(false);
  const hydratedSessionRef = useRef<string | null>(null);
  const resultsViewedTrackedRef = useRef(false);
  const quickMatchCompletedTrackedRef = useRef(false);
  const checkoutSuccessTrackedRef = useRef(false);
  const checkoutDeepRedirectedRef = useRef(false);
  const tutorialFromQuery = searchParams.get('tutorial') === '1';
  const tutorialActive = !!onboarding &&
    onboarding.onboarding_status === 'in_progress' &&
    (tutorialFromQuery || onboarding.onboarding_state.path !== null);
  const isSampleTutorial = tutorialActive && onboarding?.onboarding_state.path === 'sample';
  const sampleTutorialBanner = tutorialFromQuery && onboarding?.onboarding_state.path === 'sample';

  const resultsQuery = useQuery({
    queryKey: queryKeys.results.bySession(sessionId || ''),
    queryFn: () => getResults(sessionId!),
    enabled: !!sessionId,
    refetchInterval: (query) => {
      const data = query.state.data as ResultsResponse | undefined;
      if (!data?.locked) return false;
      return 4000;
    },
  });

  const isLockedResults = !!resultsQuery.data?.locked;
  const lockedQuoteStatus = resultsQuery.data?.quote_status || null;
  const lockedSourceSessionId = resultsQuery.data?.source_session_id || sessionId || '';
  const unlockStatusSourceId = pipelineType === 'url_only'
    ? (sessionId || '')
    : (isLockedResults ? lockedSourceSessionId : '');
  const unlockStatusEnabled = !!unlockStatusSourceId;
  const unlockStatusQuery = useQuery({
    queryKey: queryKeys.billing.unlockStatus(unlockStatusSourceId || ''),
    queryFn: () => getProjectUnlockStatus(unlockStatusSourceId),
    enabled: unlockStatusEnabled,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return false;
      if (data.quote_status === 'checkout_created') return 4000;
      if (data.is_unlocked && !data.deep_session_id) return 4000;
      if (
        data.is_unlocked &&
        (
          data.deep_session_status === 'pending' ||
          data.deep_session_status === 'processing' ||
          data.deep_session_status === 'queued'
        )
      ) {
        return 4000;
      }
      return false;
    },
  });
  const unlockStatus = unlockStatusQuery.data as ProjectUnlockStatus | null;
  const previewSummary = resultsQuery.data?.preview_summary ?? null;
  const isContentPreviewMode =
    pipelineType === 'content' &&
    isLockedResults &&
    !!resultsQuery.data?.preview_mode;

  const isLoading =
    resultsQuery.isLoading ||
    (resultsQuery.isFetching && hydratedSessionRef.current !== sessionId);

  useEffect(() => {
    hydratedSessionRef.current = null;
    resultsViewedTrackedRef.current = false;
    quickMatchCompletedTrackedRef.current = false;
    checkoutSuccessTrackedRef.current = false;
    checkoutDeepRedirectedRef.current = false;
    setRedirects([]);
    setPipelineType('content');
    setError(null);
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId) {
      setError('No session ID provided');
      return;
    }

    if (!resultsQuery.data || hydratedSessionRef.current === sessionId) {
      return;
    }

    if (resultsQuery.data.success && resultsQuery.data.mappings) {
      setRedirects(resultsQuery.data.mappings);
      if (resultsQuery.data.session?.pipeline_type) {
        setPipelineType(resultsQuery.data.session.pipeline_type);
      }
      setError(null);
    } else {
      setError('Failed to load results');
    }

    hydratedSessionRef.current = sessionId;

    if (!resultsViewedTrackedRef.current) {
      resultsViewedTrackedRef.current = true;
      posthog?.capture('results_page_viewed', {
        session_id: sessionId,
        pipeline_type: resultsQuery.data.session?.pipeline_type || 'content',
        total_mappings: resultsQuery.data.mappings?.length ?? 0,
      });
    }
  }, [resultsQuery.data, sessionId, posthog]);

  useEffect(() => {
    if (!resultsQuery.error) return;
    if (handleUnauthorizedAndRedirect(resultsQuery.error, navigate)) return;
    setError(
      resultsQuery.error instanceof Error
        ? resultsQuery.error.message
        : 'Failed to fetch results'
    );
  }, [resultsQuery.error, navigate]);

  useEffect(() => {
    const isDirectDeepSource = !!resultsQuery.data?.session?.requires_payment_unlock;
    if (!sessionId || pipelineType !== 'url_only' || quickMatchCompletedTrackedRef.current || isDirectDeepSource) {
      return;
    }

    quickMatchCompletedTrackedRef.current = true;
    posthog?.capture('quick_match_completed', {
      ...buildConversionEventProps({
        plan: user?.plan,
        authenticated: hasUser,
      }),
      session_id: sessionId,
      pipeline_type: pipelineType,
      source: searchParams.get('source') || 'review',
    });
  }, [hasUser, pipelineType, posthog, resultsQuery.data?.session?.requires_payment_unlock, searchParams, sessionId, user?.plan]);

  useEffect(() => {
    const checkoutReturnSuccess = searchParams.get('unlock') === 'success';
    if (!checkoutReturnSuccess || checkoutSuccessTrackedRef.current) {
      return;
    }

    checkoutSuccessTrackedRef.current = true;
    posthog?.capture('project_checkout_returned_success', {
      ...buildConversionEventProps({
        plan: user?.plan,
        authenticated: hasUser,
      }),
      source_session_id: sessionId || null,
      return_path: 'review',
    });
  }, [hasUser, posthog, searchParams, sessionId, user?.plan]);

  useEffect(() => {
    const checkoutReturnSuccess = searchParams.get('unlock') === 'success';
    if (!checkoutReturnSuccess || checkoutDeepRedirectedRef.current) {
      return;
    }
    if (!sessionId || pipelineType !== 'url_only') {
      return;
    }
    if (!resultsQuery.data?.session?.requires_payment_unlock) {
      return;
    }
    if (!unlockStatus?.is_unlocked || !unlockStatus.deep_session_id) {
      return;
    }
    if (unlockStatus.deep_session_id === sessionId) {
      return;
    }

    checkoutDeepRedirectedRef.current = true;
    navigate(`/review/${unlockStatus.deep_session_id}?unlock=success`, { replace: true });
  }, [
    navigate,
    pipelineType,
    resultsQuery.data?.session?.requires_payment_unlock,
    searchParams,
    sessionId,
    unlockStatus?.deep_session_id,
    unlockStatus?.is_unlocked,
  ]);

  useEffect(() => {
    if (!unlockStatusQuery.error) return;
    handleUnauthorizedAndRedirect(unlockStatusQuery.error, navigate);
  }, [unlockStatusQuery.error, navigate]);

  useEffect(() => {
    if (!tutorialActive) {
      setShowCoachmarks(false);
      return;
    }
    setShowCoachmarks(true);
  }, [tutorialActive]);

  useEffect(() => {
    if (!isSampleTutorial || sampleGenerateMarkedRef.current || isStepCompleted('generate_mappings')) return;
    sampleGenerateMarkedRef.current = true;
    setSamplePreparationActive(true);

    let cancelled = false;
    const timer = window.setTimeout(async () => {
      const updated = await completeStep('generate_mappings');
      if (cancelled) return;
      if (!updated) {
        sampleGenerateMarkedRef.current = false;
      }
      setSamplePreparationActive(false);
    }, 1200);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [completeStep, isSampleTutorial, isStepCompleted]);

  useEffect(() => {
    if (!tutorialActive || tutorialReviewMarkedRef.current) return;

    // For sample-path users, wait until mappings are marked generated so step
    // transitions feel sequential instead of instant.
    if (isSampleTutorial && !isStepCompleted('generate_mappings')) return;

    let cancelled = false;
    const delayMs = isSampleTutorial ? 900 : 0;
    const timer = window.setTimeout(async () => {
      if (cancelled) return;
      tutorialReviewMarkedRef.current = true;
      await completeStep('open_review');
    }, delayMs);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [completeStep, isSampleTutorial, isStepCompleted, tutorialActive]);

  useEffect(() => {
    if (!tutorialActive || !showCoachmarks) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setShowCoachmarks(false);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [showCoachmarks, tutorialActive]);

  const handleToggleSelect = (id: string) => {
    if (isContentPreviewMode) return;
    const newSelected = new Set(selectedRows);
    if (newSelected.has(id)) {
      newSelected.delete(id);
    } else {
      newSelected.add(id);
    }
    setSelectedRows(newSelected);
  };

  const handleToggleExpand = (id: string) => {
    if (isContentPreviewMode) return;
    setExpandedRow(expandedRow === id ? null : id);
  };

  const handleEdit = (redirect: RedirectMapping) => {
    if (isContentPreviewMode) return;
    setEditingRow(redirect);
  };

  const handleSaveEdit = (updatedRedirect: RedirectMapping) => {
    if (isContentPreviewMode) return;
    setRedirects(redirects.map(r => r.id === updatedRedirect.id ? updatedRedirect : r));
    setEditingRow(null);
    toast.success('Redirect updated');
  };

  const handleBulkAction = (action: string) => {
    if (isContentPreviewMode) return;
    if (action === 'approve-all-high') {
      const highConfidenceCount = redirects.filter(r => r.confidenceBand === 'high').length;
      setRedirects(redirects.map((r) =>
        r.confidenceBand === 'high' ? { ...r, approved: true } : r
      ));
      toast.success(`${highConfidenceCount} redirect${highConfidenceCount !== 1 ? 's' : ''} approved`);
    } else if (action === 'approve-selected') {
      const count = selectedRows.size;
      setRedirects(redirects.map((r) =>
        selectedRows.has(r.id) ? { ...r, approved: true } : r
      ));
      setSelectedRows(new Set());
      toast.success(`${count} redirect${count !== 1 ? 's' : ''} approved`);
    } else if (action === 'reject-selected') {
      const count = selectedRows.size;
      setRedirects(redirects.map((r) =>
        selectedRows.has(r.id) ? { ...r, approved: false } : r
      ));
      setSelectedRows(new Set());
      toast.success(`${count} redirect${count !== 1 ? 's' : ''} rejected`);
    }
  };


  const handleExport = async (format: string, confidenceLevels: string[]) => {
    if (isContentPreviewMode) {
      toast.error('Purchase full results before exporting.');
      return;
    }
    // Generate filename
    const formatExtensions: Record<string, string> = {
      apache: '.htaccess',
      nginx: '_nginx.conf',
      wordpress: '_wordpress.csv',
      cloudflare: '',
      shopify: '.csv',
    };

    const formatNames: Record<string, string> = {
      apache: 'redirects_apache',
      nginx: 'redirects_nginx',
      wordpress: 'redirects_wordpress',
      cloudflare: '_redirects',
      shopify: 'shopify_redirects',
    };

    const today = new Date().toISOString().split('T')[0];
    const filename = `${formatNames[format]}_${today}${formatExtensions[format]}`;

    // Show success toast
    toast.success(`${filename} downloaded successfully`, {
      duration: 3000,
    });

    if (tutorialActive && !isStepCompleted('export_redirects')) {
      const updated = await completeStep('export_redirects');
      if (updated?.onboarding_status === 'completed') {
        setShowTutorialSuccess(true);
      }
    }
  };

  const handleApproveRow = (id: string) => {
    if (isContentPreviewMode) return;
    const redirect = redirects.find(r => r.id === id);
    const newApproved = !redirect?.approved;
    setRedirects(redirects.map((r) =>
      r.id === id ? { ...r, approved: newApproved } : r
    ));
    toast.success(newApproved ? 'Redirect approved' : 'Approval removed');
  };

  const handlePricingPromptClick = ({
    sourceSessionId,
    state,
  }: {
    sourceSessionId: string;
    state: string;
  }) => {
    if (!sourceSessionId) return;
    posthog?.capture('pricing_prompt_clicked_from_review', {
      source_session_id: sourceSessionId,
      pipeline_type: pipelineType,
      state,
      plan: user?.plan || 'free',
    });
    posthog?.capture('deep_unlock_clicked_from_review', {
      session_id: sourceSessionId,
      pipeline_type: pipelineType,
      source: 'review',
      plan: user?.plan || 'free',
    });
    navigate(`/pricing?source_session_id=${sourceSessionId}`);
  };

  const filteredRedirects = redirects.filter((r) => {
    // Hide exact URL matches when toggle is off
    if (!showExactMatches && r.matchType === 'exact_url') return false;

    const q = searchQuery.trim().toLowerCase();

    const matchesSearch =
      q.length === 0 ||
      r.oldUrl.toLowerCase().includes(q) ||
      r.newUrl.toLowerCase().includes(q);

    const matchesConfidence =
      confidenceFilter === 'all' || r.confidenceBand === confidenceFilter;

    return matchesSearch && matchesConfidence;
  });

  // Determine if filters are active
  const hasActiveFilters = searchQuery.trim().length > 0 || confidenceFilter !== 'all' || !showExactMatches;

  // Handler to clear all filters
  const handleClearFilters = () => {
    setSearchQuery('');
    setConfidenceFilter('all');
    setShowExactMatches(true);
    setCurrentPage(1);
  };

  // Sort according to toolbar selection
  const sortedRedirects = [...filteredRedirects].sort((a, b) => {
    switch (sortOption) {
      case 'confidence-asc':
        return a.matchScore - b.matchScore;
      case 'url-asc':
        return a.oldUrl.localeCompare(b.oldUrl);
      case 'warnings':
        return (b.warnings?.length ?? 0) - (a.warnings?.length ?? 0);
      case 'confidence-desc':
      default:
        return b.matchScore - a.matchScore;
    }
  });

  // Pagination
  const totalPages = Math.max(1, Math.ceil(sortedRedirects.length / PAGE_SIZE));
  const currentPageSafe = Math.min(currentPage, totalPages);
  const startIndex = (currentPageSafe - 1) * PAGE_SIZE;
  const pageRedirects = sortedRedirects.slice(startIndex, startIndex + PAGE_SIZE);

  const stats = {
    total: redirects.length,
    exact: redirects.filter(r => r.matchType === 'exact_url').length,
    high: redirects.filter(r => r.confidenceBand === 'high' && r.matchType !== 'exact_url').length,
    medium: redirects.filter(r => r.confidenceBand === 'medium').length,
    low: redirects.filter(r => r.confidenceBand === 'low').length,
    approved: redirects.filter(r => r.approved).length,
    approvalProgress: redirects.length > 0 ? Math.round((redirects.filter(r => r.approved).length / redirects.length) * 100) : 0,
  };
  const quickComparableRedirects = redirects.filter((r) => r.matchType !== 'exact_url');
  const quickAverageConfidence = quickComparableRedirects.length > 0
    ? Math.round(quickComparableRedirects.reduce((acc, row) => acc + row.matchScore, 0) / quickComparableRedirects.length)
    : (previewSummary?.average_confidence ?? 100);
  const lowConfidenceRows = quickComparableRedirects
    .filter((r) => r.confidenceBand === 'low' || r.confidenceBand === 'medium')
    .sort((a, b) => a.matchScore - b.matchScore);
  const lowConfidenceSamples = lowConfidenceRows.slice(0, 3).map((row) => ({
    oldUrl: row.oldUrl,
    quickTargetUrl: row.newUrl,
    quickScore: Math.round(row.matchScore),
  }));

  // Keyboard shortcuts
  const modKey = isMac() ? 'meta' : 'ctrl';

  // Ctrl/Cmd+K: Focus search
  useHotkeys(`${modKey}+k`, (e) => {
    e.preventDefault();
    searchInputRef.current?.focus();
  }, { enableOnFormTags: true, preventDefault: true });

  // Ctrl/Cmd+E: Open export modal
  useHotkeys(`${modKey}+e`, (e) => {
    e.preventDefault();
    if (!exportModalOpen && !isContentPreviewMode) {
      setExportModalOpen(true);
    }
  }, [exportModalOpen, isContentPreviewMode]);

  // Escape: Close modals
  useHotkeys('escape', () => {
    if (exportModalOpen) {
      setExportModalOpen(false);
    } else if (keyboardShortcutsOpen) {
      setKeyboardShortcutsOpen(false);
    } else if (editingRow) {
      setEditingRow(null);
    }
  }, [exportModalOpen, keyboardShortcutsOpen, editingRow]);

  // Ctrl/Cmd+A: Select all visible redirects
  useHotkeys(`${modKey}+a`, (e) => {
    e.preventDefault();
    if (isContentPreviewMode) return;
    const allVisibleIds = new Set(pageRedirects.map(r => r.id));
    setSelectedRows(allVisibleIds);
    toast.success(`Selected ${allVisibleIds.size} redirect${allVisibleIds.size !== 1 ? 's' : ''}`);
  }, [isContentPreviewMode, pageRedirects]);

  // ?: Show keyboard shortcuts
  useHotkeys('shift+/', () => {
    setKeyboardShortcutsOpen(true);
  }, []);

  // Show error state
  if (error) {
    return (
      <Layout title={layoutTitle}>
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center">
            <div className="text-lg font-medium text-destructive mb-2">Error Loading Results</div>
            <div className="text-sm text-muted-foreground mb-4">{error}</div>
            <Button onClick={() => navigate('/')}>
              Go to Dashboard
            </Button>
          </div>
        </div>
      </Layout>
    );
  }

  return (
      <Layout title={layoutTitle}>
          {isSampleTutorial && samplePreparationActive && (
            <div className="mb-4 border border-[#26D99D] bg-[#26D99D]/16 dark:bg-[#26D99D]/24 p-3">
              <div className="flex items-center gap-2 text-sm font-medium text-[#064731] dark:text-[#E9FFF8]">
                <Loader2 className="h-4 w-4 animate-spin" />
                Generating mappings from sample CSVs...
              </div>
            </div>
          )}

          {sampleTutorialBanner && (
            <div className="mb-4 border border-[#26D99D] bg-[#26D99D]/16 dark:bg-[#26D99D]/24 p-3">
              <p className="text-sm font-medium text-[#064731] dark:text-[#E9FFF8]">
                Sample data for learning. Start a real job anytime.
              </p>
            </div>
          )}

          <DeepMatchPrompt
            pipelineType={pipelineType}
            isLockedResults={isLockedResults}
            lockedQuoteStatus={lockedQuoteStatus}
            sourceSessionId={lockedSourceSessionId}
            totalRedirects={stats.total}
            quickAverageConfidence={quickAverageConfidence}
            lowConfidenceCount={lowConfidenceRows.length}
            lowConfidenceSamples={lowConfidenceSamples}
            previewSummary={previewSummary}
            unlockStatus={unlockStatus}
            unlockLoading={unlockStatusQuery.isLoading}
            onPricingClick={handlePricingPromptClick}
            onViewDeepResults={(deepSessionId) => navigate(`/review/${deepSessionId}`)}
          />

          {/* Stats Bar */}
          <StatsBar stats={stats} />

          {/* Toolbar */}
          {tutorialActive && showCoachmarks && (
            <div className="mt-4 mb-2 border border-[#26D99D] bg-[#26D99D]/16 dark:bg-[#26D99D]/24 p-3 text-xs font-medium text-[#064731] dark:text-[#E9FFF8]">
              Step 3-4 of 4: Review results, then export redirect rules. Press <kbd className="mx-1 rounded border border-[#26D99D]/70 px-1 py-0.5 font-semibold">Esc</kbd> to hide hints.
            </div>
          )}
          <ReviewToolbar
            searchQuery={searchQuery}
            onSearchChange={setSearchQuery}
            confidenceFilter={confidenceFilter}
            onConfidenceFilterChange={setConfidenceFilter}
            sortOption={sortOption}
            onSortChange={setSortOption}
            showExactMatches={showExactMatches}
            onShowExactMatchesChange={setShowExactMatches}
            onExportClick={() => setExportModalOpen(true)}
            totalCount={redirects.length}
            filteredCount={sortedRedirects.length}
            searchInputRef={searchInputRef}
            tutorialExportHighlight={tutorialActive && showCoachmarks && !isStepCompleted('export_redirects')}
            exportDisabled={isContentPreviewMode}
          />

          {/* Table */}
          <div className={`mt-6 ${isContentPreviewMode ? 'relative' : ''}`}>
          <RedirectTable
            redirects={pageRedirects}
            selectedRows={selectedRows}
            expandedRow={expandedRow}
            onToggleSelect={handleToggleSelect}
            onToggleExpand={handleToggleExpand}
            onEdit={handleEdit}
            onApprove={handleApproveRow}
            hasActiveFilters={hasActiveFilters}
            onClearFilters={handleClearFilters}
            totalRedirectsCount={redirects.length}
            isLoading={isLoading}
            pipelineType={pipelineType}
            readOnly={isContentPreviewMode}
            previewMode={isContentPreviewMode}
          />

          {isContentPreviewMode && (
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-background/45 backdrop-blur-[1.5px]">
              <div className="rounded-md border border-border bg-card/95 px-4 py-3 text-sm text-foreground shadow-sm">
                Full URLs and export unlock after purchase.
              </div>
            </div>
          )}

          </div>

          {pipelineType === 'url_only' && !isLockedResults && sessionId && (
            <div className="mt-4 text-sm text-muted-foreground">
              <button
                type="button"
                className="underline underline-offset-4 hover:text-foreground"
                onClick={() => navigate(`/content-match?source_session_id=${encodeURIComponent(sessionId)}`)}
              >
                Want better results? Try content based matching →
              </button>
            </div>
          )}

          {/* Bottom Controls */}
          <div className="mt-6 flex items-center justify-between border-t border-border pt-6 bg-card px-6 py-4">
            <div className="flex items-center gap-4">
              <Select onValueChange={handleBulkAction} disabled={isContentPreviewMode}>
                <SelectTrigger className="w-[200px]">
                  <SelectValue placeholder="Bulk Actions" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="approve-all-high">Approve All High</SelectItem>
                  <SelectItem value="approve-selected">Approve Selected</SelectItem>
                  <SelectItem value="reject-selected">Reject Selected</SelectItem>
                  <SelectItem value="clear-selection">Clear Selection</SelectItem>
                </SelectContent>
              </Select>
              {selectedRows.size > 0 && (
                <span className="text-sm text-muted-foreground">
                  {selectedRows.size} row{selectedRows.size !== 1 ? 's' : ''} selected
                </span>
              )}
            </div>

            <Pagination>
              <PaginationContent>
                {/* Previous */}
                <PaginationItem>
                  <PaginationPrevious
                    href="#"
                    onClick={(e) => {
                      e.preventDefault();
                      if (currentPageSafe > 1) {
                        setCurrentPage(currentPageSafe - 1);
                      }
                    }}
                  />
                </PaginationItem>

                {/* Page numbers */}
                {Array.from({ length: totalPages }, (_, i) => i + 1).map((page) => (
                  <PaginationItem key={page}>
                    <PaginationLink
                      href="#"
                      isActive={page === currentPageSafe}
                      onClick={(e) => {
                        e.preventDefault();
                        setCurrentPage(page);
                      }}
                    >
                      {page}
                    </PaginationLink>
                  </PaginationItem>
                ))}

                {/* Next */}
                <PaginationItem>
                  <PaginationNext
                    href="#"
                    onClick={(e) => {
                      e.preventDefault();
                      if (currentPageSafe < totalPages) {
                        setCurrentPage(currentPageSafe + 1);
                      }
                    }}
                  />
                </PaginationItem>
              </PaginationContent>
            </Pagination>
          </div>

      {/* Floating Keyboard Shortcuts Button */}
      <div className="fixed bottom-6 right-6 z-40">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="outline"
              size="icon"
              onClick={() => setKeyboardShortcutsOpen(true)}
              className="h-10 w-10 rounded-full shadow-lg border-2"
            >
              <Keyboard className="h-5 w-5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="left">
            <p>Keyboard shortcuts (?)</p>
          </TooltipContent>
        </Tooltip>
      </div>

      {/* Inline Edit Dialog */}
      {editingRow && (
        <InlineEditDialog
          redirect={editingRow}
          sessionId={sessionId!}
          onSave={handleSaveEdit}
          onCancel={() => setEditingRow(null)}
          pipelineType={pipelineType}
        />
      )}

      {/* Export Modal */}
      <ExportModal
        open={exportModalOpen}
        onOpenChange={setExportModalOpen}
        onExport={handleExport}
        redirects={redirects}
      />

      {/* Keyboard Shortcuts Dialog */}
      <KeyboardShortcutsDialog
        open={keyboardShortcutsOpen}
        onOpenChange={setKeyboardShortcutsOpen}
      />

      <Dialog open={showTutorialSuccess} onOpenChange={setShowTutorialSuccess}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Tutorial complete</DialogTitle>
            <DialogDescription>
              You exported redirect rules successfully. You can now run a real project with your own CSVs.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="sm:justify-between gap-2">
            <Button
              variant="outline"
              onClick={async () => {
                await completeOnboarding();
                setShowTutorialSuccess(false);
              }}
            >
              Close
            </Button>
            <Button
              onClick={async () => {
                await completeOnboarding();
                setShowTutorialSuccess(false);
                navigate(enterpriseUser ? '/upload' : '/url-match');
              }}
            >
              Start Real Job Now
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Layout>
  );
}
