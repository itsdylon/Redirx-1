import { Search, Download, ArrowUpDown, Link2 } from 'lucide-react';
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
import { Switch } from './ui/switch';
import { Label } from './ui/label';
import { formatShortcut } from '../lib/keyboard';

interface ReviewToolbarProps {
  searchQuery: string;
  onSearchChange: (query: string) => void;
  confidenceFilter: string;
  onConfidenceFilterChange: (filter: string) => void;
  onExportClick: () => void;
  sortOption: string;
  onSortChange: (value: string) => void;
  showExactMatches: boolean;
  onShowExactMatchesChange: (show: boolean) => void;
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
  showExactMatches,
  onShowExactMatchesChange,
  totalCount,
  filteredCount,
  searchInputRef,
}: ReviewToolbarProps) {
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
          className="pl-10 pr-20"
        />
        <kbd className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none hidden sm:inline-flex h-5 items-center gap-1 rounded border border-border bg-muted px-1.5 font-mono text-[10px] font-medium text-muted-foreground">
          {formatShortcut('K')}
        </kbd>
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

      {/* Exact Match Toggle */}
      <div className="flex items-center gap-2">
        <Switch
          id="show-exact"
          checked={showExactMatches}
          onCheckedChange={onShowExactMatchesChange}
        />
        <Label htmlFor="show-exact" className="text-sm text-muted-foreground cursor-pointer whitespace-nowrap flex items-center gap-1">
          <Link2 className="h-3.5 w-3.5" />
          Exact
        </Label>
      </div>

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
      <div className="hidden sm:flex items-center whitespace-nowrap">
        <span className="text-sm text-muted-foreground">
          {filteredCount} of {totalCount} pages
        </span>
      </div>
    </div>
  );
}
