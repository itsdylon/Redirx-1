import { useState, useEffect, useCallback } from 'react';
import { X, CheckCircle2, AlertCircle, Loader2 } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { RedirectMapping } from './ReviewInterface';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';
import { validateUrl, debounce } from '../utils/validation';

interface InlineEditDialogProps {
  redirect: RedirectMapping;
  onSave: (redirect: RedirectMapping) => void;
  onCancel: () => void;
}

export function InlineEditDialog({ redirect, onSave, onCancel }: InlineEditDialogProps) {
  const [editedRedirect, setEditedRedirect] = useState(redirect);
  const [urlError, setUrlError] = useState<string | null>(null);
  const [isUrlValid, setIsUrlValid] = useState<boolean>(true);
  const [isValidating, setIsValidating] = useState<boolean>(false);
  const [isSaving, setIsSaving] = useState<boolean>(false);

  // Debounced validation function
  const debouncedValidate = useCallback(
    debounce((url: string) => {
      const result = validateUrl(url);
      setUrlError(result.error);
      setIsUrlValid(result.valid);
      setIsValidating(false);
    }, 300),
    []
  );

  // Validate initial URL on mount
  useEffect(() => {
    const result = validateUrl(editedRedirect.newUrl);
    setUrlError(result.error);
    setIsUrlValid(result.valid);
  }, []);

  // Handle new URL changes with debounced validation
  const handleNewUrlChange = (newUrl: string) => {
    setEditedRedirect({ ...editedRedirect, newUrl });
    setIsValidating(true);
    debouncedValidate(newUrl);
  };

  const handleSave = async () => {
    // Final validation check before saving
    const result = validateUrl(editedRedirect.newUrl);
    if (result.valid) {
      setIsSaving(true);
      try {
        await onSave(editedRedirect);
      } finally {
        setIsSaving(false);
      }
    }
  };

  return (
    <Dialog open={true} onOpenChange={onCancel}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Edit Redirect Mapping</DialogTitle>
          <DialogDescription>
            Modify the redirect mapping details below. Changes will be reflected in the mapping table.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-6 py-4">
          {/* Old URL */}
          <div className="space-y-2">
            <Label htmlFor="old-url" className="text-foreground">
              Old URL
            </Label>
            <Input
              id="old-url"
              value={editedRedirect.oldUrl}
              onChange={(e) => setEditedRedirect({ ...editedRedirect, oldUrl: e.target.value })}
              className="font-mono text-sm"
              disabled
            />
            <p className="text-xs text-muted-foreground">Source URL cannot be modified</p>
          </div>

          {/* New URL */}
          <div className="space-y-2">
            <Label htmlFor="new-url" className="text-foreground">
              New Target URL
            </Label>
            <div className="relative">
              <Input
                id="new-url"
                value={editedRedirect.newUrl}
                onChange={(e) => handleNewUrlChange(e.target.value)}
                className={`font-mono text-sm pr-10 ${
                  !isValidating && !isUrlValid
                    ? 'border-red-500 dark:border-red-400 focus-visible:ring-red-500'
                    : !isValidating && isUrlValid
                    ? 'border-green-500 dark:border-green-400 focus-visible:ring-green-500'
                    : ''
                }`}
                placeholder="/new/url/path"
              />
              {/* Validation icon */}
              {!isValidating && (
                <div className="absolute right-3 top-1/2 -translate-y-1/2">
                  {isUrlValid ? (
                    <CheckCircle2 className="h-5 w-5 text-green-500 dark:text-green-400" />
                  ) : (
                    <AlertCircle className="h-5 w-5 text-red-500 dark:text-red-400" />
                  )}
                </div>
              )}
            </div>
            {/* Error message */}
            {urlError && !isValidating && (
              <div className="flex items-start gap-2 text-sm text-red-600 dark:text-red-400">
                <AlertCircle className="h-4 w-4 mt-0.5 flex-shrink-0" />
                <span>{urlError}</span>
              </div>
            )}
            {/* Help text */}
            {!urlError && (
              <p className="text-xs text-muted-foreground">
                Enter a relative path (e.g., /about) or absolute URL (e.g., https://example.com/page)
              </p>
            )}
          </div>

          {/* Match Score Override */}
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="match-score" className="text-foreground">
                Manual Confidence Override
              </Label>
              <Select
                value={editedRedirect.confidenceBand}
                onValueChange={(value: 'high' | 'medium' | 'low') =>
                  setEditedRedirect({ ...editedRedirect, confidenceBand: value })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="high">High Confidence</SelectItem>
                  <SelectItem value="medium">Medium Confidence</SelectItem>
                  <SelectItem value="low">Low Confidence</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label className="text-foreground">Current Match Score</Label>
              <div className="flex items-center h-10 px-3 border border-border rounded-md bg-muted">
                <span className="text-foreground">{editedRedirect.matchScore}%</span>
              </div>
            </div>
          </div>

          {/* Alternative Suggestions */}
          <div className="border border-border bg-muted p-4">
            <h4 className="text-foreground text-sm mb-3">Alternative Matches</h4>
            <div className="space-y-2">
              <button className="w-full text-left p-2 border border-border bg-card hover:bg-accent transition-colors text-sm font-mono">
                /solutions/consulting-services <span className="text-muted-foreground float-right">82%</span>
              </button>
              <button className="w-full text-left p-2 border border-border bg-card hover:bg-accent transition-colors text-sm font-mono">
                /services/advisory <span className="text-muted-foreground float-right">76%</span>
              </button>
              <button className="w-full text-left p-2 border border-border bg-card hover:bg-accent transition-colors text-sm font-mono">
                /professional-services <span className="text-muted-foreground float-right">71%</span>
              </button>
            </div>
            <p className="text-xs text-muted-foreground mt-2">Click an alternative to use it as the new target</p>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onCancel} disabled={isSaving}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={!isUrlValid || isValidating || isSaving}>
            {isSaving && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
            {isSaving ? 'Saving...' : 'Save Changes'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
