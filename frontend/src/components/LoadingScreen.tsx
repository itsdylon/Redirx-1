import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { Button } from './ui/button';
import { getSessionStatus } from '../api/sessions';

interface LoadingScreenProps {
  sessionId?: string | null;
}

const POLL_INTERVAL = 3000; // 3 seconds

export function LoadingScreen({ sessionId }: LoadingScreenProps) {
  const navigate = useNavigate();

  // Poll for job completion
  useEffect(() => {
    if (!sessionId) return;

    const pollInterval = setInterval(async () => {
      try {
        const status = await getSessionStatus(sessionId);

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

  return (
    <div className="min-h-screen flex items-center justify-center p-8">
      <div className="w-full max-w-xl">
        <div className="text-center">
          {/* Spinner Icon */}
          <div className="mb-8 flex justify-center">
            <div className="border border-border rounded-full p-8 bg-card">
              <Loader2 className="h-16 w-16 text-foreground animate-spin" />
            </div>
          </div>

          {/* Heading */}
          <h1 className="text-foreground mb-2">Processing Redirects</h1>
          <p className="text-muted-foreground mb-6">
            Analyzing and matching your URLs. This may take a few minutes.
          </p>

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
