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

interface ReviewToolbarProps {
  searchQuery: string;
  onSearchChange: (query: string) => void;
  confidenceFilter: string;
  onConfidenceFilterChange: (filter: string) => void;
  onExportClick: () => void;
  sortOption: string;
  onSortChange: (value: string) => void;
}

export function ReviewToolbar({
  searchQuery,
  onSearchChange,
  confidenceFilter,
  onConfidenceFilterChange,
  onExportClick,
  sortOption,
  onSortChange,
}: ReviewToolbarProps) {
  return (
    <div className="bg-white border border-gray-300 p-4 flex items-center gap-4">
      {/* Search Bar */}
      <div className="flex-1 relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
        <Input
          type="text"
          placeholder="Search old or new URLs..."
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          className="pl-10"
        />
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
      <Button variant="outline" onClick={onExportClick}>
        <Download className="h-4 w-4 mr-2" />
        Export
      </Button>
    </div>
  );
}
