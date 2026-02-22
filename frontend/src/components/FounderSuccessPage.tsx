import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from './ui/button';
import { Loader2 } from 'lucide-react';
import { getSubscriptionStatus } from '../api/billing';
import faviconImg from '@/assets/favicon.png';

const RAIN_COUNT = 30;

function RainingImages() {
  const items = Array.from({ length: RAIN_COUNT }, (_, i) => {
    const left = Math.random() * 100;
    const delay = Math.random() * 5;
    const duration = 4 + Math.random() * 6;
    const size = 30 + Math.random() * 40;
    return (
      <img
        key={i}
        src={faviconImg}
        alt=""
        className="founder-rain-item pointer-events-none"
        style={{
          position: 'fixed',
          left: `${left}%`,
          top: '-80px',
          width: `${size}px`,
          height: `${size}px`,
          opacity: 0.15 + Math.random() * 0.2,
          animationDelay: `${delay}s`,
          animationDuration: `${duration}s`,
        }}
      />
    );
  });
  return <div className="fixed inset-0 overflow-hidden pointer-events-none z-0">{items}</div>;
}

export function FounderSuccessPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<'polling' | 'confirmed' | 'timeout'>('polling');
  const pollingRef = useRef(false);

  useEffect(() => {
    if (pollingRef.current) return;
    pollingRef.current = true;

    const poll = async () => {
      for (let i = 0; i < 10; i++) {
        try {
          const sub = await getSubscriptionStatus();
          if (sub.plan === 'founder') {
            setStatus('confirmed');
            return;
          }
        } catch {
          // Keep polling
        }
        await new Promise((r) => setTimeout(r, 2000));
      }
      setStatus('timeout');
    };
    poll();
  }, []);

  return (
    <div className="min-h-screen flex items-center justify-center p-4 relative">
      <RainingImages />
      <div className="text-center relative z-10">
        <h1 className="text-5xl font-bold text-foreground mb-4 tracking-tight">
          THANK YOU
        </h1>

        {status === 'polling' && (
          <>
            <div className="flex items-center justify-center gap-2 text-muted-foreground mb-8">
              <Loader2 className="h-5 w-5 animate-spin" />
              <p className="text-lg">Activating your Founder access...</p>
            </div>
            <Button size="lg" disabled>
              Go to Dashboard
            </Button>
          </>
        )}

        {status === 'confirmed' && (
          <>
            <p className="text-lg text-muted-foreground mb-8 max-w-md mx-auto">
              Welcome to the Founder circle. Your lifetime access is now active.
            </p>
            <Button size="lg" onClick={() => navigate('/dashboard')}>
              Go to Dashboard
            </Button>
          </>
        )}

        {status === 'timeout' && (
          <>
            <p className="text-lg text-muted-foreground mb-4 max-w-md mx-auto">
              Welcome to the Founder circle. Your payment was received.
            </p>
            <p className="text-sm text-muted-foreground mb-8 max-w-md mx-auto">
              Plan activation may take a moment. If your plan hasn't updated yet, please refresh from the dashboard.
            </p>
            <Button size="lg" onClick={() => navigate('/dashboard')}>
              Go to Dashboard
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
