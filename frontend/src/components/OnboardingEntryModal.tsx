import { Loader2, Rocket, Upload, Beaker, X } from 'lucide-react';
import { Button } from './ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';

interface OnboardingEntryModalProps {
  open: boolean;
  loading?: boolean;
  error?: string | null;
  onOpenChange: (open: boolean) => void;
  onTrySample: () => void;
  onUseOwnCsv: () => void;
  onSkip: () => void;
}

export function OnboardingEntryModal({
  open,
  loading = false,
  error,
  onOpenChange,
  onTrySample,
  onUseOwnCsv,
  onSkip,
}: OnboardingEntryModalProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Rocket className="h-5 w-5 text-primary" />
            Get your first redirect export in minutes
          </DialogTitle>
          <DialogDescription>
            Pick a quick path to learn RedirX. You can skip now and restart from settings any time.
          </DialogDescription>
        </DialogHeader>

        {error && (
          <div className="border border-destructive/40 bg-destructive/10 text-destructive px-3 py-2 rounded-md text-sm">
            {error}
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 py-2 items-stretch">
          <Button
            type="button"
            className="h-auto w-full min-h-[120px] items-start text-left justify-start p-4 whitespace-normal"
            onClick={onTrySample}
            disabled={loading}
          >
            <div className="flex gap-3 items-start min-w-0">
              <Beaker className="h-4 w-4 mt-0.5 shrink-0" />
              <div className="min-w-0">
                <div className="font-medium leading-snug break-words">Try with sample data</div>
                <div className="text-xs opacity-90 mt-1 leading-snug break-words">
                  Fastest path. Start with preloaded sample CSV files.
                </div>
              </div>
            </div>
          </Button>

          <Button
            type="button"
            variant="outline"
            className="h-auto w-full min-h-[120px] items-start text-left justify-start p-4 whitespace-normal"
            onClick={onUseOwnCsv}
            disabled={loading}
          >
            <div className="flex gap-3 items-start min-w-0">
              <Upload className="h-4 w-4 mt-0.5 shrink-0" />
              <div className="min-w-0">
                <div className="font-medium leading-snug break-words">Use my own CSV files</div>
                <div className="text-xs text-muted-foreground mt-1 leading-snug break-words">
                  Start with your real migration files now.
                </div>
              </div>
            </div>
          </Button>
        </div>

        <DialogFooter className="sm:justify-between gap-2">
          <Button
            type="button"
            variant="ghost"
            onClick={onSkip}
            disabled={loading}
          >
            <X className="h-4 w-4 mr-2" />
            Skip for now
          </Button>
          {loading && (
            <div className="inline-flex items-center text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              Preparing tutorial...
            </div>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
