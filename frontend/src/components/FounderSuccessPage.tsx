import { useNavigate } from 'react-router-dom';
import { Button } from './ui/button';

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
        src="/favicon.PNG"
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

  return (
    <div className="min-h-screen flex items-center justify-center p-4 relative">
      <RainingImages />
      <div className="text-center relative z-10">
        <h1 className="text-5xl font-bold text-foreground mb-4 tracking-tight">
          THANK YOU
        </h1>
        <p className="text-lg text-muted-foreground mb-8 max-w-md mx-auto">
          Welcome to the Founder circle. Your lifetime access is now active.
        </p>
        <Button size="lg" onClick={() => navigate('/dashboard')}>
          Go to Dashboard
        </Button>
      </div>
    </div>
  );
}
