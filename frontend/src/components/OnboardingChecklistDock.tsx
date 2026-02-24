import { useMemo } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { CheckCircle2, Circle, Sparkles } from 'lucide-react';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { useIsMobile } from './ui/use-mobile';
import { useOnboarding } from '../contexts/OnboardingContext';
import { OnboardingStep } from '../api/onboarding';

const STEP_CONFIG: Array<{ id: OnboardingStep; label: string }> = [
  { id: 'choose_path', label: 'Choose a path' },
  { id: 'generate_mappings', label: 'Generate mappings' },
  { id: 'open_review', label: 'Open and review results' },
  { id: 'export_redirects', label: 'Export redirect rules' },
];

export function OnboardingChecklistDock() {
  const location = useLocation();
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  const {
    onboarding,
    dismissOnboarding,
    completeOnboarding,
    isStepCompleted,
  } = useOnboarding();

  const showDock = !!onboarding && onboarding.onboarding_status === 'in_progress';
  const isCoreRoute = location.pathname === '/upload' || location.pathname.startsWith('/review');

  const completedCount = useMemo(
    () => STEP_CONFIG.filter((step) => isStepCompleted(step.id)).length,
    [isStepCompleted]
  );

  const canMarkDone = isStepCompleted('open_review') && !isStepCompleted('export_redirects');

  if (!showDock || !isCoreRoute) {
    return null;
  }

  return (
    <Card
      className={`z-40 border-[#26D99D]/70 bg-background/95 backdrop-blur-sm ${
        isMobile
          ? 'fixed bottom-0 left-0 right-0 rounded-none border-x-0 border-b-0 p-4'
          : 'fixed bottom-4 right-4 w-[360px] p-4 shadow-xl'
      }`}
      role="region"
      aria-label="Onboarding checklist"
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-[#26D99D]" />
          <h3 className="text-sm font-semibold text-foreground">First-time tutorial</h3>
        </div>
        <span className="text-xs text-muted-foreground">
          {completedCount}/{STEP_CONFIG.length}
        </span>
      </div>

      <div className="mt-3 space-y-2">
        {STEP_CONFIG.map((step) => {
          const completed = isStepCompleted(step.id);
          return (
            <div
              key={step.id}
              className="flex items-center gap-2 text-sm"
            >
              {completed ? (
                <CheckCircle2 className="h-4 w-4 text-green-600 dark:text-green-400" aria-hidden="true" />
              ) : (
                <Circle className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
              )}
              <span className={completed ? 'text-foreground' : 'text-muted-foreground'}>
                {step.label}
              </span>
            </div>
          );
        })}
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        {canMarkDone && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => completeOnboarding()}
          >
            Mark Tutorial Done
          </Button>
        )}
        {location.pathname !== '/upload' && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => navigate('/upload?tutorial=1')}
          >
            Go to Upload
          </Button>
        )}
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={() => dismissOnboarding()}
          className="ml-auto"
        >
          Skip Tutorial
        </Button>
      </div>
    </Card>
  );
}
