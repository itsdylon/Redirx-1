import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { Button } from './ui/button';
import { Progress } from './ui/progress';
import { getSessionStatus, SessionStatus } from '../api/sessions';

interface LoadingScreenProps {
  sessionId?: string | null;
}

const POLL_INTERVAL = 3000; // 3 seconds

export function LoadingScreen({ sessionId }: LoadingScreenProps) {
  const navigate = useNavigate();
  const [progress, setProgress] = useState<{
    currentStage: number | null;
    stageName: string | null;
    totalStages: number | null;
  }>({ currentStage: null, stageName: null, totalStages: null });

  // Poll for job completion
  useEffect(() => {
    if (!sessionId) return;

    const pollInterval = setInterval(async () => {
      try {
        const status: SessionStatus = await getSessionStatus(sessionId);

        // Update progress state
        setProgress({
          currentStage: status.current_stage ?? null,
          stageName: status.stage_name ?? null,
          totalStages: status.total_stages ?? null,
        });

        if (status.status === 'completed') {
          clearInterval(pollInterval);
          navigate(`/review/${sessionId}`);
        } else if (status.status === 'failed') {
          clearInterval(pollInterval);
          // Could show an error state here
          console.error('Job failed');
        }
      } catch (error) {
        console.error('Error polling status:', error);
      }
    }, POLL_INTERVAL);

    return () => clearInterval(pollInterval);
  }, [sessionId, navigate]);

  const handleContinueInBackground = () => {
    navigate('/');
  };

  const hasProgress = progress.currentStage != null && progress.totalStages != null;
  const progressPercent = hasProgress
    ? (progress.currentStage! / progress.totalStages!) * 100
    : 0;

  return (
    <div className="min-h-screen flex items-center justify-center p-8">
      <div className="w-full max-w-xl">
        <div className="text-center">
          {/* Progress heading */}
          <h1 className="text-foreground mb-2">
            {hasProgress
              ? `Step ${progress.currentStage} of ${progress.totalStages}`
              : 'Preparing...'}
          </h1>

          {/* Stage name with spinner */}
          <div className="flex items-center justify-center gap-2 mb-6">
            <Loader2 className="h-4 w-4 text-muted-foreground animate-spin" />
            <p className="text-muted-foreground">
              {progress.stageName ? `${progress.stageName}...` : 'Analyzing and matching your URLs.'}
            </p>
          </div>

          {/* Progress bar */}
          <div className="mb-8">
            <Progress value={progressPercent} className="h-3" />
          </div>

          {/* Info box */}
          <div className="mb-6 bg-card border border-border p-4 text-left">
            <p className="text-sm text-muted-foreground">
              You can wait here or continue working. Your job will process in the background
              and you can view the results from your dashboard.
            </p>
          </div>

          {/* Continue in Background Button */}
          <Button
            variant="outline"
            onClick={handleContinueInBackground}
            className="w-full"
          >
            Continue in background
          </Button>
        </div>
      </div>
    </div>
  );
}
