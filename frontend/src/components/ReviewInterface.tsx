import { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useHotkeys } from 'react-hotkeys-hook';
import { Header } from './Header';
import { StatsSidebar } from './StatsSidebar';
import { ReviewToolbar } from './ReviewToolbar';
import { RedirectTable } from './RedirectTable';
import { InlineEditDialog } from './InlineEditDialog';
import { ExportModal } from './ExportModal';
import { KeyboardShortcutsDialog } from './KeyboardShortcutsDialog';
import { Button } from './ui/button';
import { ArrowLeft, Keyboard } from 'lucide-react';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from './ui/tooltip';
import { toast } from 'sonner';
import { getResults } from '../api/pipeline';
import { isMac } from '../lib/keyboard';
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
  approved: boolean;
  warnings: string[];
  pathSimilarity: number;
  titleSimilarity: number;
  contentSimilarity: number;
}

export function ReviewInterface() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const [redirects, setRedirects] = useState<RedirectMapping[]>([]);
  const [selectedRows, setSelectedRows] = useState<Set<string>>(new Set());
  const [expandedRow, setExpandedRow] = useState<string | null>(null);
  const [editingRow, setEditingRow] = useState<RedirectMapping | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [confidenceFilter, setConfidenceFilter] = useState<string>('all');
  const [currentPage, setCurrentPage] = useState(1);
  const [exportModalOpen, setExportModalOpen] = useState(false);
  const [keyboardShortcutsOpen, setKeyboardShortcutsOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sortOption, setSortOption] = useState<string>('confidence-desc');
  const PAGE_SIZE = 25;

  // Refs for keyboard shortcuts
  const searchInputRef = useRef<HTMLInputElement>(null);

  // Fetch results from backend when sessionId is available
  useEffect(() => {
    async function fetchResults() {
      if (!sessionId) {
        setError("No session ID provided");
        setIsLoading(false);
        return;
      }

      try {
        setIsLoading(true);
        setError(null);
        const data = await getResults(sessionId);

        if (data.success && data.mappings) {
          setRedirects(data.mappings);
        } else {
          setError("Failed to load results");
        }
      } catch (err) {
        console.error("Error fetching results:", err);
        setError(err instanceof Error ? err.message : "Failed to fetch results");
      } finally {
        setIsLoading(false);
      }
    }

    fetchResults();
  }, [sessionId]);

  const handleToggleSelect = (id: string) => {
    const newSelected = new Set(selectedRows);
    if (newSelected.has(id)) {
      newSelected.delete(id);
    } else {
      newSelected.add(id);
    }
    setSelectedRows(newSelected);
  };

  const handleToggleExpand = (id: string) => {
    setExpandedRow(expandedRow === id ? null : id);
  };

  const handleEdit = (redirect: RedirectMapping) => {
    setEditingRow(redirect);
  };

  const handleSaveEdit = (updatedRedirect: RedirectMapping) => {
    setRedirects(redirects.map(r => r.id === updatedRedirect.id ? updatedRedirect : r));
    setEditingRow(null);
    toast.success('Redirect updated');
  };

  const handleBulkAction = (action: string) => {
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


  const handleExport = (format: string, confidenceLevels: string[]) => {
    // Generate filename
    const formatExtensions: Record<string, string> = {
      apache: '.htaccess',
      nginx: '_nginx.conf',
      wordpress: '_wordpress.csv',
    };

    const formatNames: Record<string, string> = {
      apache: 'redirects_apache',
      nginx: 'redirects_nginx',
      wordpress: 'redirects_wordpress',
    };

    const today = new Date().toISOString().split('T')[0];
    const filename = `${formatNames[format]}_${today}${formatExtensions[format]}`;

    // Show success toast
    toast.success(`${filename} downloaded successfully`, {
      duration: 3000,
    });
  };

  const handleApproveRow = (id: string) => {
    setRedirects(redirects.map((r) =>
      r.id === id ? { ...r, approved: true } : r
    ));
  };

  const filteredRedirects = redirects.filter((r) => {
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
  const hasActiveFilters = searchQuery.trim().length > 0 || confidenceFilter !== 'all';

  // Handler to clear all filters
  const handleClearFilters = () => {
    setSearchQuery('');
    setConfidenceFilter('all');
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
    high: redirects.filter(r => r.confidenceBand === 'high').length,
    medium: redirects.filter(r => r.confidenceBand === 'medium').length,
    low: redirects.filter(r => r.confidenceBand === 'low').length,
    approved: redirects.filter(r => r.approved).length,
    approvalProgress: redirects.length > 0 ? Math.round((redirects.filter(r => r.approved).length / redirects.length) * 100) : 0,
  };

  // Keyboard shortcuts
  const modKey = isMac() ? 'meta' : 'ctrl';

  // Ctrl/Cmd+K: Focus search
  useHotkeys(`${modKey}+k`, (e) => {
    e.preventDefault();
    searchInputRef.current?.focus();
  }, []);

  // Ctrl/Cmd+E: Open export modal
  useHotkeys(`${modKey}+e`, (e) => {
    e.preventDefault();
    if (!exportModalOpen) {
      setExportModalOpen(true);
    }
  }, [exportModalOpen]);

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
    const allVisibleIds = new Set(pageRedirects.map(r => r.id));
    setSelectedRows(allVisibleIds);
    toast.success(`Selected ${allVisibleIds.size} redirect${allVisibleIds.size !== 1 ? 's' : ''}`);
  }, [pageRedirects]);

  // ?: Show keyboard shortcuts
  useHotkeys('shift+/', () => {
    setKeyboardShortcutsOpen(true);
  }, []);

  // Show loading state
  if (isLoading) {
    return (
      <div className="min-h-screen">
        <Header currentView="review" />
        <div className="flex items-center justify-center h-screen">
          <div className="text-center">
            <div className="text-lg font-medium text-foreground mb-2">Loading results...</div>
            <div className="text-sm text-muted-foreground">Fetching your redirect mappings</div>
          </div>
        </div>
      </div>
    );
  }

  // Show error state
  if (error) {
    return (
      <div className="min-h-screen">
        <Header currentView="review" />
        <div className="flex items-center justify-center h-screen">
          <div className="text-center">
            <div className="text-lg font-medium text-destructive mb-2">Error Loading Results</div>
            <div className="text-sm text-muted-foreground mb-4">{error}</div>
            <Button onClick={() => navigate('/')}>
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back to Dashboard
            </Button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      <Header currentView="review" />

      <div className="flex">
        {/* Left Sidebar */}
        <StatsSidebar stats={stats} />

        {/* Main Content */}
        <main className="flex-1 p-8">
          {/* Back Button */}
          <div className="mb-4">
            <Button variant="outline" onClick={() => navigate('/')}>
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back to Dashboard
            </Button>
          </div>

          {/* Toolbar */}
          <ReviewToolbar
            searchQuery={searchQuery}
            onSearchChange={setSearchQuery}
            confidenceFilter={confidenceFilter}
            onConfidenceFilterChange={setConfidenceFilter}
            sortOption={sortOption}
            onSortChange={setSortOption}
            onExportClick={() => setExportModalOpen(true)}
            totalCount={redirects.length}
            filteredCount={sortedRedirects.length}
            searchInputRef={searchInputRef}
          />

          {/* Table */}
          <div className="mt-6">
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
          />

          </div>

          {/* Bottom Controls */}
          <div className="mt-6 flex items-center justify-between border-t border-border pt-6 bg-card px-6 py-4">
            <div className="flex items-center gap-4">
              <Select onValueChange={handleBulkAction}>
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
        </main>
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
          onSave={handleSaveEdit}
          onCancel={() => setEditingRow(null)}
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
    </div>
  );
}
