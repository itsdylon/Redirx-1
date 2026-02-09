import { Search, Download, ArrowUpDown } from 'lucide-react';
import { Input } from './ui/input';
import { Button } from './ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from './ui/tooltip';
import { formatShortcut } from '../lib/keyboard';

interface ReviewToolbarProps {
  searchQuery: string;
  onSearchChange: (query: string) => void;
  confidenceFilter: string;
  onConfidenceFilterChange: (filter: string) => void;
  onExportClick: () => void;
  sortOption: string;
  onSortChange: (value: string) => void;
  totalCount: number;
  filteredCount: number;
  searchInputRef?: React.RefObject<HTMLInputElement>;
}

export function ReviewToolbar({
  searchQuery,
  onSearchChange,
  confidenceFilter,
  onConfidenceFilterChange,
  onExportClick,
  sortOption,
  onSortChange,
  totalCount,
  filteredCount,
  searchInputRef,
}: ReviewToolbarProps) {
  // Determine if filters are active
  const isFiltered = searchQuery.trim().length > 0 || confidenceFilter !== 'all';

  return (
    <div className="bg-card border border-border p-4 flex items-center gap-4 flex-wrap">
      {/* Search Bar */}
      <div className="flex-1 relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <Input
          ref={searchInputRef}
          type="text"
          placeholder="Search old or new URLs..."
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          className="pl-10"
        />
        <p className="text-xs text-muted-foreground mt-1.5 ml-1">
          Press {formatShortcut('K')} to focus search
        </p>
      </div>

      {/* Confidence Filter */}
      <Select value={confidenceFilter} onValueChange={onConfidenceFilterChange}>
        <SelectTrigger className="w-[180px]">
          <SelectValue placeholder="Confidence" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All Confidence</SelectItem>
          <SelectItem value="high">High (≥85%)</SelectItem>
          <SelectItem value="medium">Medium (60-85%)</SelectItem>
          <SelectItem value="low">Low (&lt;60%)</SelectItem>
        </SelectContent>
      </Select>

      {/* Sort Options */}
      <Select value={sortOption} onValueChange={onSortChange}>
        <SelectTrigger className="w-[180px]">
          <SelectValue placeholder="Sort by" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="confidence-desc">
            Confidence: High to Low
          </SelectItem>
          <SelectItem value="confidence-asc">
            Confidence: Low to High
          </SelectItem>
          <SelectItem value="url-asc">
            Old URL: A to Z
          </SelectItem>
          <SelectItem value="warnings">
            Warnings First
          </SelectItem>
        </SelectContent>
      </Select>

      {/* Export Button */}
      <Tooltip>
        <TooltipTrigger asChild>
          <Button variant="outline" onClick={onExportClick}>
            <Download className="h-4 w-4 mr-2" />
            Export ({formatShortcut('E')})
          </Button>
        </TooltipTrigger>
        <TooltipContent>
          <p>Export redirects ({formatShortcut('E')})</p>
        </TooltipContent>
      </Tooltip>

      {/* Result Count Display */}
      <div className="ml-auto hidden sm:flex items-center">
        {isFiltered ? (
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">Showing</span>
            <span className="inline-flex items-center rounded-md bg-primary/10 px-2.5 py-0.5 text-sm font-semibold text-primary">
              {filteredCount}
            </span>
            <span className="text-sm text-muted-foreground">of</span>
            <span className="text-sm font-medium text-foreground">{totalCount}</span>
            <span className="text-sm text-muted-foreground">redirects</span>
          </div>
        ) : (
          <span className="text-sm text-muted-foreground">
            {totalCount} redirect{totalCount !== 1 ? 's' : ''}
          </span>
        )}
      </div>
    </div>
  );
}
