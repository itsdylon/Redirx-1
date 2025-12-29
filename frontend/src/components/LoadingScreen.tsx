import { useState, useEffect } from 'react';
import { Progress } from './ui/progress';
import { Loader2 } from 'lucide-react';

export function LoadingScreen() {
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    // Simulate progress
    const progressInterval = setInterval(() => {
      setProgress((prev) => {
        if (prev >= 95) return prev;
        return prev + Math.random() * 3;
      });
    }, 800);

    return () => clearInterval(progressInterval);
  }, []);

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
          <p className="text-muted-foreground mb-8">Analyzing and matching URLs...</p>

          {/* Progress Section */}
          <div className="mb-6 bg-card border border-border p-6">
            <div className="flex justify-between items-center mb-2">
              <span className="text-muted-foreground">Progress</span>
              <span className="text-foreground">{Math.floor(progress)}%</span>
            </div>
            <Progress value={progress} className="h-2" />
          </div>
        </div>
      </div>
    </div>
  );
}
