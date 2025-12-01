import { useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { Button } from './ui/button';
import { Checkbox } from './ui/checkbox';
import { Label } from './ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';
import { Alert, AlertDescription } from './ui/alert';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from './ui/collapsible';
import { Separator } from './ui/separator';
import { AlertTriangle, CheckCircle, ChevronDown, ChevronRight, X } from 'lucide-react';

interface ExportModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onExport: (format: string, confidenceLevels: string[]) => void;
}

export function ExportModal({ open, onOpenChange, onExport }: ExportModalProps) {
  const [format, setFormat] = useState<string>('');
  const [includeHigh, setIncludeHigh] = useState(true);
  const [includeMedium, setIncludeMedium] = useState(true);
  const [includeLow, setIncludeLow] = useState(false);
  const [previewExpanded, setPreviewExpanded] = useState(false);

  // Mock data
  const totalHigh = 89;
  const totalMedium = 35;
  const totalLow = 23;
  const unapprovedHigh = 3;
  const duplicateTargets = 2;

  const selectedCount = 
    (includeHigh ? totalHigh : 0) + 
    (includeMedium ? totalMedium : 0) + 
    (includeLow ? totalLow : 0);

  const hasWarnings = unapprovedHigh > 0 && includeHigh;
  const hasDuplicates = duplicateTargets > 0;

  const getPreviewRules = () => {
    switch (format) {
      case 'apache':
        return `# First 3 redirects
Redirect 301 /old-page-1 /new-page-1
Redirect 301 /products/item-123 /shop/item-123
Redirect 301 /about/team /company/team

# ... (${selectedCount - 6} more rules)

# Last 3 redirects
Redirect 301 /blog/post-456 /articles/post-456
Redirect 301 /contact-us /contact
Redirect 301 /services/web /solutions/web`;

      case 'nginx':
        return `# First 3 redirects
map $uri $new_uri {
    /old-page-1 /new-page-1;
    /products/item-123 /shop/item-123;
    /about/team /company/team;

    # ... (${selectedCount - 6} more rules)

    # Last 3 redirects
    /blog/post-456 /articles/post-456;
    /contact-us /contact;
    /services/web /solutions/web;
}`;

      case 'wordpress':
        return `# First 3 redirects
/old-page-1,/new-page-1,301
/products/item-123,/shop/item-123,301
/about/team,/company/team,301

# ... (${selectedCount - 6} more rows)

# Last 3 redirects
/blog/post-456,/articles/post-456,301
/contact-us,/contact,301
/services/web,/solutions/web,301`;

      default:
        return 'Select a format to preview rules';
    }
  };

  const handleDownload = () => {
    const confidenceLevels: string[] = [];
    if (includeHigh) confidenceLevels.push('high');
    if (includeMedium) confidenceLevels.push('medium');
    if (includeLow) confidenceLevels.push('low');

    onExport(format, confidenceLevels);
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <DialogHeader>
          <DialogTitle>Export Redirects</DialogTitle>
          <DialogDescription>
            Configure export format and select which redirects to include in your download.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-6 py-4">
          {/* Format Selection */}
          <div>
            <Label className="text-gray-700 mb-2 block">Export Format</Label>
            <Select value={format} onValueChange={setFormat}>
              <SelectTrigger>
                <SelectValue placeholder="Select Format" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="apache">Apache .htaccess</SelectItem>
                <SelectItem value="nginx">Nginx map</SelectItem>
                <SelectItem value="wordpress">WordPress Redirection CSV</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <Separator />

          {/* Confidence Level Selection */}
          <div>
            <Label className="text-gray-700 mb-3 block">Confidence Levels to Include</Label>
            <div className="space-y-3">
              <div className="flex items-center space-x-2">
                <Checkbox
                  id="high"
                  checked={includeHigh}
                  onCheckedChange={(checked) => setIncludeHigh(checked as boolean)}
                />
                <Label htmlFor="high" className="text-gray-900 cursor-pointer">
                  Include High Confidence ({totalHigh} redirects)
                </Label>
              </div>
              <div className="flex items-center space-x-2">
                <Checkbox
                  id="medium"
                  checked={includeMedium}
                  onCheckedChange={(checked) => setIncludeMedium(checked as boolean)}
                />
                <Label htmlFor="medium" className="text-gray-900 cursor-pointer">
                  Include Medium Confidence ({totalMedium} redirects)
                </Label>
              </div>
              <div className="flex items-center space-x-2">
                <Checkbox
                  id="low"
                  checked={includeLow}
                  onCheckedChange={(checked) => setIncludeLow(checked as boolean)}
                />
                <Label htmlFor="low" className="text-gray-900 cursor-pointer">
                  Include Low Confidence ({totalLow} redirects)
                </Label>
              </div>
            </div>
          </div>

          {/* Live Count Display */}
          <div className="border border-gray-300 bg-gray-50 p-4">
            <div className="flex items-center justify-between">
              <span className="text-gray-700">Redirects to export:</span>
              <span className="text-gray-900">{selectedCount}</span>
            </div>
          </div>

          {/* Warning for Unapproved High-Confidence */}
          {hasWarnings && (
            <Alert variant="destructive">
              <AlertTriangle className="h-4 w-4" />
              <AlertDescription>
                {unapprovedHigh} high-confidence redirects are unapproved. Review or override to continue.
              </AlertDescription>
            </Alert>
          )}

          {/* Duplicate Warnings */}
          {hasDuplicates && (
            <Alert className="border-yellow-500 bg-yellow-50">
              <AlertTriangle className="h-4 w-4 text-yellow-600" />
              <AlertDescription className="text-yellow-900">
                Warning: {duplicateTargets} duplicate target URLs detected.{' '}
                <button className="underline">View Details</button>
              </AlertDescription>
            </Alert>
          )}

          {/* Preview Rules Section */}
          <Collapsible open={previewExpanded} onOpenChange={setPreviewExpanded}>
            <CollapsibleTrigger className="w-full">
              <div className="flex items-center justify-between border border-gray-300 bg-white p-3 hover:bg-gray-50">
                <div className="flex items-center gap-2">
                  {previewExpanded ? (
                    <ChevronDown className="h-4 w-4 text-gray-600" />
                  ) : (
                    <ChevronRight className="h-4 w-4 text-gray-600" />
                  )}
                  <span className="text-gray-900">Preview Rules</span>
                </div>
                {format && (
                  <CheckCircle className="h-4 w-4 text-green-600" />
                )}
              </div>
            </CollapsibleTrigger>
            <CollapsibleContent>
              <div className="border border-gray-300 border-t-0 p-4 bg-gray-50">
                <div className="bg-white border border-gray-300 p-4 font-mono text-xs overflow-x-auto">
                  <pre className="text-gray-900 whitespace-pre">{getPreviewRules()}</pre>
                </div>
                {format && (
                  <div className="flex items-center gap-2 mt-3 text-sm text-green-700">
                    <CheckCircle className="h-4 w-4" />
                    <span>Syntax validation passed</span>
                  </div>
                )}
              </div>
            </CollapsibleContent>
          </Collapsible>
        </div>

        {/* Action Buttons */}
        <div className="flex justify-end gap-3 pt-4 border-t border-gray-300">
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
          >
            Cancel
          </Button>
          <Button
            onClick={handleDownload}
            disabled={!format || selectedCount === 0}
          >
            Download File
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
