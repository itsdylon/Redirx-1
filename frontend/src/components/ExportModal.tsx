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
import { AlertTriangle, CheckCircle, ChevronDown, ChevronRight, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
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
  const [isDownloading, setIsDownloading] = useState(false);

  // URL format options
  const [urlFormat, setUrlFormat] = useState<'paths' | 'full' | 'custom'>('paths');
  const [customOldDomain, setCustomOldDomain] = useState('');
  const [customNewDomain, setCustomNewDomain] = useState('');

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

  // Validate custom domains
  const customDomainErrors: string[] = [];
  if (urlFormat === 'custom') {
    if (customOldDomain) {
      try {
        new URL(customOldDomain);
      } catch {
        customDomainErrors.push('Old domain must be a valid URL (e.g., https://example.com)');
      }
    }
    if (customNewDomain) {
      try {
        new URL(customNewDomain);
      } catch {
        customDomainErrors.push('New domain must be a valid URL (e.g., https://example.com)');
      }
    }
  }

  const hasCustomDomainErrors = customDomainErrors.length > 0;

  // URL transformation helper
  const transformUrl = (url: string, type: 'old' | 'new'): string => {
    if (urlFormat === 'full') {
      return url; // Current behavior - keep full URLs
    }

    if (urlFormat === 'paths') {
      // Extract path only
      try {
        const parsedUrl = new URL(url);
        return parsedUrl.pathname;
      } catch {
        return url; // Fallback if URL is invalid
      }
    }

    if (urlFormat === 'custom') {
      // Replace domain with custom domain
      try {
        const parsedUrl = new URL(url);
        const customDomain = type === 'old' ? customOldDomain : customNewDomain;

        if (customDomain) {
          const customParsed = new URL(customDomain);
          parsedUrl.protocol = customParsed.protocol;
          parsedUrl.host = customParsed.host;
          return parsedUrl.href;
        }

        return url;
      } catch {
        return url; // Fallback if parsing fails
      }
    }

    return url;
  };

  // Single source of truth for export + preview content
  const buildExportContent = (fmt: string, rules: RedirectMapping[], limitPreview = false): string => {
    if (!fmt) return 'Select a format to preview rules';
    if (rules.length === 0) return 'No redirects match the selected confidence levels.';

    // Limit to first 10 redirects for preview
    const displayRules = limitPreview ? rules.slice(0, 10) : rules;
    const hasMore = limitPreview && rules.length > 10;

    let content = '';

    switch (fmt) {
      case 'apache':
        // .htaccess style: Redirect 301 /old /new
        content = displayRules
          .map((r) => {
            const oldPath = transformUrl(r.oldUrl, 'old');
            const newPath = transformUrl(r.newUrl, 'new');
            return `Redirect 301 ${oldPath} ${newPath}`;
          })
          .join('\n');
        break;

      case 'nginx':
        // Nginx map: map $uri $new_uri { /old /new; ... }
        content = [
          'map $uri $new_uri {',
          ...displayRules.map((r) => {
            const oldPath = transformUrl(r.oldUrl, 'old');
            const newPath = transformUrl(r.newUrl, 'new');
            return `    ${oldPath} ${newPath};`;
          }),
          '}',
        ].join('\n');
        break;

      case 'wordpress':
        // WordPress Redirection CSV: /old,/new,301
        content = displayRules
          .map((r) => {
            const oldPath = transformUrl(r.oldUrl, 'old');
            const newPath = transformUrl(r.newUrl, 'new');
            return `${oldPath},${newPath},301`;
          })
          .join('\n');
        break;

      default:
        return 'Select a format to preview rules';
    }

    if (hasMore) {
      content += `\n\n... and ${rules.length - 10} more redirects`;
    }

    return content;
  };

  const previewContent = buildExportContent(format, filteredRedirects, true);

  const handleDownload = async () => {
    if (!format || selectedCount === 0) return;

    setIsDownloading(true);

    try {
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

      // Show success toast
      toast.success(`${fileName} downloaded successfully`, {
        duration: 3000,
      });

      // Still call onExport so your parent can show toasts, analytics, etc.
      onExport(format, confidenceLevels);
      onOpenChange(false);
    } catch (error) {
      // Handle specific error types
      if (error instanceof DOMException) {
        if (error.name === 'SecurityError') {
          toast.error('Download blocked by browser security settings', {
            description: 'Please check your browser permissions and try again.',
            duration: 5000,
          });
        } else if (error.name === 'QuotaExceededError') {
          toast.error('Not enough disk space', {
            description: 'Please free up disk space and try again.',
            duration: 5000,
          });
        } else {
          toast.error('Download failed', {
            description: `Browser error: ${error.message}. Please try again.`,
            duration: 5000,
          });
        }
      } else if (error instanceof Error) {
        toast.error('Download failed', {
          description: error.message || 'An unexpected error occurred. Please try again.',
          duration: 5000,
        });
      } else {
        toast.error('Download failed', {
          description: 'An unexpected error occurred. Please try again.',
          duration: 5000,
        });
      }

      console.error('Export download error:', error);
    } finally {
      setIsDownloading(false);
    }
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
            <Label className="text-foreground mb-2 block">Export Format</Label>
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

          {/* URL Format Selection */}
          <div>
            <Label className="text-foreground mb-3 block font-medium">URL Format</Label>
            <div className="space-y-3">
              {/* Paths Only */}
              <div className="flex items-start space-x-3">
                <input
                  type="radio"
                  id="url-paths"
                  name="urlFormat"
                  checked={urlFormat === 'paths'}
                  onChange={() => setUrlFormat('paths')}
                  className="mt-1"
                />
                <div className="flex-1">
                  <label htmlFor="url-paths" className="text-sm font-medium text-foreground cursor-pointer">
                    Paths only (Recommended)
                  </label>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    Export: <code className="bg-muted px-1 rounded">/old-path</code> → <code className="bg-muted px-1 rounded">/new-path</code>
                  </p>
                </div>
              </div>

              {/* Full URLs */}
              <div className="flex items-start space-x-3">
                <input
                  type="radio"
                  id="url-full"
                  name="urlFormat"
                  checked={urlFormat === 'full'}
                  onChange={() => setUrlFormat('full')}
                  className="mt-1"
                />
                <div className="flex-1">
                  <label htmlFor="url-full" className="text-sm font-medium text-foreground cursor-pointer">
                    Full URLs from CSV
                  </label>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    Export: <code className="bg-muted px-1 rounded text-xs">http://staging.com/old</code> → <code className="bg-muted px-1 rounded text-xs">http://staging.com/new</code>
                  </p>
                </div>
              </div>

              {/* Custom Domains */}
              <div className="flex items-start space-x-3">
                <input
                  type="radio"
                  id="url-custom"
                  name="urlFormat"
                  checked={urlFormat === 'custom'}
                  onChange={() => setUrlFormat('custom')}
                  className="mt-1"
                />
                <div className="flex-1">
                  <label htmlFor="url-custom" className="text-sm font-medium text-foreground cursor-pointer">
                    Custom domains
                  </label>
                  <p className="text-xs text-muted-foreground mt-0.5 mb-2">
                    Replace staging domains with production domains
                  </p>

                  {urlFormat === 'custom' && (
                    <div className="space-y-2 mt-2">
                      <div>
                        <Label htmlFor="custom-old" className="text-xs text-muted-foreground">Old domain (optional)</Label>
                        <input
                          id="custom-old"
                          type="text"
                          placeholder="https://example.com"
                          value={customOldDomain}
                          onChange={(e) => setCustomOldDomain(e.target.value)}
                          className="w-full border border-border rounded px-2 py-1 text-sm mt-1 bg-background text-foreground"
                        />
                      </div>
                      <div>
                        <Label htmlFor="custom-new" className="text-xs text-muted-foreground">New domain (optional)</Label>
                        <input
                          id="custom-new"
                          type="text"
                          placeholder="https://example.com"
                          value={customNewDomain}
                          onChange={(e) => setCustomNewDomain(e.target.value)}
                          className="w-full border border-border rounded px-2 py-1 text-sm mt-1 bg-background text-foreground"
                        />
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>

          <Separator />

          {/* Confidence Level Selection */}
          <div>
            <Label className="text-foreground mb-3 block">Confidence Levels to Include</Label>
            <div className="space-y-3">
              <div className="flex items-center space-x-2">
                <Checkbox
                  id="high"
                  checked={includeHigh}
                  onCheckedChange={(checked) => setIncludeHigh(!!checked)}
                />
                <Label htmlFor="high" className="text-foreground cursor-pointer">
                  Include High Confidence ({totalHigh} redirects)
                </Label>
              </div>
              <div className="flex items-center space-x-2">
                <Checkbox
                  id="medium"
                  checked={includeMedium}
                  onCheckedChange={(checked) => setIncludeMedium(!!checked)}
                />
                <Label htmlFor="medium" className="text-foreground cursor-pointer">
                  Include Medium Confidence ({totalMedium} redirects)
                </Label>
              </div>
              <div className="flex items-center space-x-2">
                <Checkbox
                  id="low"
                  checked={includeLow}
                  onCheckedChange={(checked) => setIncludeLow(!!checked)}
                />
                <Label htmlFor="low" className="text-foreground cursor-pointer">
                  Include Low Confidence ({totalLow} redirects)
                </Label>
              </div>
            </div>
          </div>

          {/* Live Count Display */}
          <div className="border border-border bg-muted p-4">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Redirects to export:</span>
              <span className="text-foreground">{selectedCount}</span>
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
            <Alert className="border-yellow-500/50 bg-yellow-500/10">
              <AlertTriangle className="h-4 w-4 text-yellow-600" />
              <AlertDescription className="text-foreground">
                Warning: {duplicateTargets} target URL
                {duplicateTargets > 1 ? 's are' : ' is'} used by multiple redirects in this export.{' '}
                <button className="underline">View Details</button>
              </AlertDescription>
            </Alert>
          )}

          {/* Custom Domain Validation Errors */}
          {hasCustomDomainErrors && (
            <Alert variant="destructive">
              <AlertTriangle className="h-4 w-4" />
              <AlertDescription>
                <div className="space-y-1">
                  {customDomainErrors.map((error, idx) => (
                    <div key={idx}>{error}</div>
                  ))}
                </div>
              </AlertDescription>
            </Alert>
          )}

          {/* Preview Rules Section */}
          <Collapsible open={previewExpanded} onOpenChange={setPreviewExpanded}>
            <CollapsibleTrigger className="w-full">
              <div className="flex items-center justify-between border border-border bg-card p-3 hover:bg-accent">
                <div className="flex items-center gap-2">
                  {previewExpanded ? (
                    <ChevronDown className="h-4 w-4 text-muted-foreground" />
                  ) : (
                    <ChevronRight className="h-4 w-4 text-muted-foreground" />
                  )}
                  <span className="text-foreground">Preview Rules</span>
                  {filteredRedirects.length > 10 && (
                    <span className="text-xs text-muted-foreground">(showing first 10 of {filteredRedirects.length})</span>
                  )}
                </div>
                {format && selectedCount > 0 && !hasCustomDomainErrors && (
                  <CheckCircle className="h-4 w-4 text-green-600" />
                )}
              </div>
            </CollapsibleTrigger>
            <CollapsibleContent>
              <div className="border border-border border-t-0 p-4 bg-muted">
                {/* URL Format Info */}
                {format && selectedCount > 0 && (
                  <div className="mb-3 text-xs text-muted-foreground">
                    {urlFormat === 'paths' && (
                      <span>URLs transformed to paths only (e.g., /page.html)</span>
                    )}
                    {urlFormat === 'full' && (
                      <span>Full URLs preserved from CSV</span>
                    )}
                    {urlFormat === 'custom' && (
                      <span>
                        Custom domains applied
                        {customOldDomain && ` - Old: ${customOldDomain}`}
                        {customNewDomain && ` - New: ${customNewDomain}`}
                      </span>
                    )}
                  </div>
                )}

                <div className="bg-card border border-border p-4 font-mono text-xs overflow-x-auto">
                  <pre className="text-foreground whitespace-pre">
                    {previewContent}
                  </pre>
                </div>
                {format && selectedCount > 0 && !hasCustomDomainErrors && (
                  <div className="flex items-center gap-2 mt-3 text-sm text-green-600">
                    <CheckCircle className="h-4 w-4" />
                    <span>Syntax validation passed</span>
                  </div>
                )}
              </div>
            </CollapsibleContent>
          </Collapsible>
        </div>

        {/* Action Buttons */}
        <div className="flex justify-end gap-3 pt-4 border-t border-border">
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
          >
            Cancel
          </Button>
          <Button
            onClick={handleDownload}
            disabled={!format || selectedCount === 0 || hasCustomDomainErrors || isDownloading}
          >
            {isDownloading ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Preparing download...
              </>
            ) : (
              'Download File'
            )}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
