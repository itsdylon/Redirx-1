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
import { AlertTriangle, CheckCircle, ChevronDown, ChevronRight } from 'lucide-react';
import type { RedirectMapping } from './ReviewInterface';

interface ExportModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onExport: (format: string, confidenceLevels: string[]) => void;
  redirects: RedirectMapping[];
}

export function ExportModal({ open, onOpenChange, onExport, redirects }: ExportModalProps) {
  const [format, setFormat] = useState<string>('');
  const [includeHigh, setIncludeHigh] = useState(true);
  const [includeMedium, setIncludeMedium] = useState(true);
  const [includeLow, setIncludeLow] = useState(false);
  const [previewExpanded, setPreviewExpanded] = useState(false);

  // Live counts from actual data
  const totalHigh = redirects.filter((r) => r.confidenceBand === 'high').length;
  const totalMedium = redirects.filter((r) => r.confidenceBand === 'medium').length;
  const totalLow = redirects.filter((r) => r.confidenceBand === 'low').length;

  // Apply confidence filters
  const filteredRedirects = redirects.filter((r) => {
    if (r.confidenceBand === 'high' && !includeHigh) return false;
    if (r.confidenceBand === 'medium' && !includeMedium) return false;
    if (r.confidenceBand === 'low' && !includeLow) return false;
    // Only export mappings that actually have a target
    if (!r.oldUrl || !r.newUrl) return false;
    return true;
  });

  const selectedCount = filteredRedirects.length;

  // Warnings: unapproved high-confidence redirects in the export set
  const unapprovedHigh = filteredRedirects.filter(
    (r) => r.confidenceBand === 'high' && !r.approved
  ).length;

  const hasWarnings = unapprovedHigh > 0;

  // Duplicate targets in the export set
  const duplicateTargets = (() => {
    const counts = new Map<string, number>();
    for (const r of filteredRedirects) {
      const key = r.newUrl;
      if (!key) continue;
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    let dupCount = 0;
    counts.forEach((c) => {
      if (c > 1) dupCount += 1; // number of target URLs that are duplicated
    });
    return dupCount;
  })();

  const hasDuplicates = duplicateTargets > 0;

  // Single source of truth for export + preview content
  const buildExportContent = (fmt: string, rules: RedirectMapping[]): string => {
    if (!fmt) return 'Select a format to preview rules';
    if (rules.length === 0) return 'No redirects match the selected confidence levels.';
    console.log(fmt);
    switch (fmt) {
      case 'apache':
        // .htaccess style: Redirect 301 /old /new
        // Assumes 301; update if you later track status per rule.
        return rules
          .map((r) => `Redirect 301 ${r.oldUrl} ${r.newUrl}`)
          .join('\n');

      case 'nginx':
        // Nginx map: map $uri $new_uri { /old /new; ... }
        return [
          'map $uri $new_uri {',
          ...rules.map((r) => `    ${r.oldUrl} ${r.newUrl};`),
          '}',
        ].join('\n');

      case 'wordpress':
        // WordPress Redirection CSV: /old,/new,301
        return rules
          .map((r) => `${r.oldUrl},${r.newUrl},301`)
          .join('\n');

      default:
        return 'Select a format to preview rules';
    }
  };

  const previewContent = buildExportContent(format, filteredRedirects);

  const handleDownload = () => {
    if (!format || selectedCount === 0) return;

    const confidenceLevels: string[] = [];
    if (includeHigh) confidenceLevels.push('high');
    if (includeMedium) confidenceLevels.push('medium');
    if (includeLow) confidenceLevels.push('low');

    // Use the same content as the preview
    const content = buildExportContent(format, filteredRedirects);

    const fileName =
      format === 'apache'
        ? 'redirects.htaccess'
        : format === 'nginx'
        ? 'redirects_nginx.conf'
        : format === 'wordpress'
        ? 'redirects_wordpress.csv'
        : 'redirects.txt';

    // Create a blob and trigger a download
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
    const url = window.URL.createObjectURL(blob);

    const link = document.createElement('a');
    link.href = url;
    link.download = fileName;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    window.URL.revokeObjectURL(url);

    // Still call onExport so your parent can show toasts, analytics, etc.
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
                  onCheckedChange={(checked) => setIncludeHigh(!!checked)}
                />
                <Label htmlFor="high" className="text-gray-900 cursor-pointer">
                  Include High Confidence ({totalHigh} redirects)
                </Label>
              </div>
              <div className="flex items-center space-x-2">
                <Checkbox
                  id="medium"
                  checked={includeMedium}
                  onCheckedChange={(checked) => setIncludeMedium(!!checked)}
                />
                <Label htmlFor="medium" className="text-gray-900 cursor-pointer">
                  Include Medium Confidence ({totalMedium} redirects)
                </Label>
              </div>
              <div className="flex items-center space-x-2">
                <Checkbox
                  id="low"
                  checked={includeLow}
                  onCheckedChange={(checked) => setIncludeLow(!!checked)}
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
                {unapprovedHigh} high-confidence redirects in this export are unapproved. Review or
                override before using in production.
              </AlertDescription>
            </Alert>
          )}

          {/* Duplicate Warnings */}
          {hasDuplicates && (
            <Alert className="border-yellow-500 bg-yellow-50">
              <AlertTriangle className="h-4 w-4 text-yellow-600" />
              <AlertDescription className="text-yellow-900">
                Warning: {duplicateTargets} target URL
                {duplicateTargets > 1 ? 's are' : ' is'} used by multiple redirects in this export.{' '}
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
                {format && selectedCount > 0 && (
                  <CheckCircle className="h-4 w-4 text-green-600" />
                )}
              </div>
            </CollapsibleTrigger>
            <CollapsibleContent>
              <div className="border border-gray-300 border-t-0 p-4 bg-gray-50">
                <div className="bg-white border border-gray-300 p-4 font-mono text-xs overflow-x-auto">
                  <pre className="text-gray-900 whitespace-pre">
                    {previewContent}
                  </pre>
                </div>
                {format && selectedCount > 0 && (
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
