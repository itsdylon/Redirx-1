import { useState, useEffect } from 'react';
import { Header } from './Header';
import { StatsSidebar } from './StatsSidebar';
import { ReviewToolbar } from './ReviewToolbar';
import { RedirectTable } from './RedirectTable';
import { InlineEditDialog } from './InlineEditDialog';
import { ExportModal } from './ExportModal';
import { Button } from './ui/button';
import { ArrowLeft } from 'lucide-react';
import { toast } from 'sonner@2.0.3';
import { getResults } from '../api/pipeline';
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

interface ReviewInterfaceProps {
  sessionId: string | null;
  onBackToUpload: () => void;
  onNavigate: (view: 'dashboard' | 'upload' | 'review') => void;
}

export function ReviewInterface({ sessionId, onBackToUpload, onNavigate }: ReviewInterfaceProps) {
  const [redirects, setRedirects] = useState<RedirectMapping[]>([]);
  const [selectedRows, setSelectedRows] = useState<Set<string>>(new Set());
  const [expandedRow, setExpandedRow] = useState<string | null>(null);
  const [editingRow, setEditingRow] = useState<RedirectMapping | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [confidenceFilter, setConfidenceFilter] = useState<string>('all');
  const [currentPage, setCurrentPage] = useState(1);
  const [exportModalOpen, setExportModalOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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
  };

  const handleBulkAction = (action: string) => {
    if (action === 'approve-all-high') {
      setRedirects(redirects.map(r => 
        r.confidenceBand === 'high' ? { ...r, approved: true } : r
      ));
    } else if (action === 'approve-selected') {
      setRedirects(redirects.map(r => 
        selectedRows.has(r.id) ? { ...r, approved: true } : r
      ));
      setSelectedRows(new Set());
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

  const stats = {
    total: redirects.length,
    high: redirects.filter(r => r.confidenceBand === 'high').length,
    medium: redirects.filter(r => r.confidenceBand === 'medium').length,
    low: redirects.filter(r => r.confidenceBand === 'low').length,
    approved: redirects.filter(r => r.approved).length,
    approvalProgress: redirects.length > 0 ? Math.round((redirects.filter(r => r.approved).length / redirects.length) * 100) : 0,
  };

  // Show loading state
  if (isLoading) {
    return (
      <div className="min-h-screen bg-gray-50">
        <Header currentView="review" onNavigate={onNavigate} />
        <div className="flex items-center justify-center h-screen">
          <div className="text-center">
            <div className="text-lg font-medium text-gray-900 mb-2">Loading results...</div>
            <div className="text-sm text-gray-600">Fetching your redirect mappings</div>
          </div>
        </div>
      </div>
    );
  }

  // Show error state
  if (error) {
    return (
      <div className="min-h-screen bg-gray-50">
        <Header currentView="review" onNavigate={onNavigate} />
        <div className="flex items-center justify-center h-screen">
          <div className="text-center">
            <div className="text-lg font-medium text-red-600 mb-2">Error Loading Results</div>
            <div className="text-sm text-gray-600 mb-4">{error}</div>
            <Button onClick={onBackToUpload}>
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back to Dashboard
            </Button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <Header currentView="review" onNavigate={onNavigate} />
      
      <div className="flex">
        {/* Left Sidebar */}
        <StatsSidebar stats={stats} />

        {/* Main Content */}
        <main className="flex-1 p-8">
          {/* Back Button */}
          <div className="mb-4">
            <Button variant="outline" onClick={onBackToUpload}>
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
            onExportClick={() => setExportModalOpen(true)}
          />

          {/* Table */}
          <div className="mt-6">
            <RedirectTable
              redirects={redirects}
              selectedRows={selectedRows}
              expandedRow={expandedRow}
              onToggleSelect={handleToggleSelect}
              onToggleExpand={handleToggleExpand}
              onEdit={handleEdit}
            />
          </div>

          {/* Bottom Controls */}
          <div className="mt-6 flex items-center justify-between border-t border-gray-300 pt-6 bg-white px-6 py-4">
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
                <span className="text-sm text-gray-600">
                  {selectedRows.size} row{selectedRows.size !== 1 ? 's' : ''} selected
                </span>
              )}
            </div>

            <Pagination>
              <PaginationContent>
                <PaginationItem>
                  <PaginationPrevious href="#" />
                </PaginationItem>
                <PaginationItem>
                  <PaginationLink href="#" isActive>1</PaginationLink>
                </PaginationItem>
                <PaginationItem>
                  <PaginationLink href="#">2</PaginationLink>
                </PaginationItem>
                <PaginationItem>
                  <PaginationLink href="#">3</PaginationLink>
                </PaginationItem>
                <PaginationItem>
                  <PaginationNext href="#" />
                </PaginationItem>
              </PaginationContent>
            </Pagination>
          </div>
        </main>
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
      />
    </div>
  );
}
