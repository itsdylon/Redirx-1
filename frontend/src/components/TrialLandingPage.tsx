import { useState, useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { Card, CardHeader, CardTitle, CardContent } from './ui/card';
import { Button } from './ui/button';
import { Badge } from './ui/badge';
import { Separator } from './ui/separator';
import { Gift, Clock, Check, AlertTriangle, ArrowRight, Loader2 } from 'lucide-react';
import { validateTrialCode, redeemTrialCode, type ValidationResult } from '../api/trials';

type PageState = 'loading' | 'invalid' | 'preview' | 'redeeming' | 'success' | 'error';

export function TrialLandingPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [pageState, setPageState] = useState<PageState>('loading');
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [errorMessage, setErrorMessage] = useState('');
  const [successData, setSuccessData] = useState<{
    trial_days: number;
    credits_granted: number;
    campaign_name: string;
  } | null>(null);

  // Get code from URL params or localStorage
  const codeFromUrl = searchParams.get('code');
  const cidFromUrl = searchParams.get('cid');
  const pendingCode = localStorage.getItem('pending_trial_code');
  const code = codeFromUrl || pendingCode || '';

  useEffect(() => {
    if (!code) {
      setPageState('invalid');
      setErrorMessage('No invite code provided');
      return;
    }

    // Clear pending code from localStorage if we got it from URL
    if (codeFromUrl && pendingCode) {
      localStorage.removeItem('pending_trial_code');
    }

    // Validate the code
    validateTrialCode(code)
      .then((result) => {
        setValidation(result);
        if (result.valid) {
          setPageState('preview');
        } else {
          setPageState('invalid');
          setErrorMessage(result.error || 'Invalid invite code');
        }
      })
      .catch(() => {
        setPageState('invalid');
        setErrorMessage('Failed to validate invite code');
      });
  }, [code]);

  const handleRedeem = async () => {
    if (!code) return;
    setPageState('redeeming');

    try {
      const result = await redeemTrialCode(code);
      if (result.success) {
        localStorage.removeItem('pending_trial_code');
        setSuccessData({
          trial_days: result.trial_days!,
          credits_granted: result.credits_granted!,
          campaign_name: result.campaign_name || '',
        });
        setPageState('success');
        // Redirect to dashboard after 3 seconds
        setTimeout(() => navigate('/'), 3000);
      } else {
        setPageState('error');
        setErrorMessage(result.error || 'Redemption failed');
      }
    } catch (err: any) {
      setPageState('error');
      setErrorMessage(err.message || 'Redemption failed');
    }
  };

  const handleSignupRedirect = () => {
    // Store the full return URL so we come back here after auth
    const returnUrl = `/trial?code=${encodeURIComponent(code)}${cidFromUrl ? `&cid=${encodeURIComponent(cidFromUrl)}` : ''}`;
    localStorage.setItem('pending_trial_code', code);
    localStorage.setItem('auth_redirect', returnUrl);
    navigate('/signup');
  };

  const handleLoginRedirect = () => {
    const returnUrl = `/trial?code=${encodeURIComponent(code)}${cidFromUrl ? `&cid=${encodeURIComponent(cidFromUrl)}` : ''}`;
    localStorage.setItem('pending_trial_code', code);
    localStorage.setItem('auth_redirect', returnUrl);
    navigate('/login');
  };

  // Loading state
  if (pageState === 'loading') {
    return (
      <div className="min-h-screen flex items-center justify-center p-4">
        <Card className="w-full max-w-md p-8 text-center">
          <Loader2 className="h-8 w-8 animate-spin mx-auto text-muted-foreground" />
          <p className="mt-4 text-muted-foreground">Validating invite code...</p>
        </Card>
      </div>
    );
  }

  // Invalid code
  if (pageState === 'invalid') {
    return (
      <div className="min-h-screen flex items-center justify-center p-4">
        <Card className="w-full max-w-md p-8 text-center">
          <AlertTriangle className="h-12 w-12 mx-auto text-destructive mb-4" />
          <h1 className="text-xl font-bold text-foreground mb-2">Invalid Invite Code</h1>
          <p className="text-muted-foreground mb-6">{errorMessage}</p>
          <Button variant="outline" onClick={() => navigate('/')}>
            Go to Homepage
          </Button>
        </Card>
      </div>
    );
  }

  // Success state
  if (pageState === 'success' && successData) {
    return (
      <div className="min-h-screen flex items-center justify-center p-4">
        <Card className="w-full max-w-md p-8 text-center">
          <div className="w-16 h-16 bg-green-500/10 rounded-full flex items-center justify-center mx-auto mb-4">
            <Check className="h-8 w-8 text-green-500" />
          </div>
          <h1 className="text-2xl font-bold text-foreground mb-2">Trial Activated!</h1>
          <p className="text-muted-foreground mb-4">
            Your {successData.trial_days}-day premium trial is now active.
          </p>
          <div className="space-y-2 text-sm text-muted-foreground mb-6">
            <p>{successData.credits_granted.toLocaleString()} mapping credits</p>
            <p>Unlimited Quick Match</p>
            <p>Up to 3 concurrent projects</p>
          </div>
          <p className="text-xs text-muted-foreground">
            Redirecting to dashboard...
          </p>
        </Card>
      </div>
    );
  }

  // Error state (redemption failed)
  if (pageState === 'error') {
    return (
      <div className="min-h-screen flex items-center justify-center p-4">
        <Card className="w-full max-w-md p-8 text-center">
          <AlertTriangle className="h-12 w-12 mx-auto text-destructive mb-4" />
          <h1 className="text-xl font-bold text-foreground mb-2">Redemption Failed</h1>
          <p className="text-muted-foreground mb-6">{errorMessage}</p>
          <div className="flex gap-2 justify-center">
            <Button variant="outline" onClick={() => setPageState('preview')}>
              Try Again
            </Button>
            <Button variant="outline" onClick={() => navigate('/')}>
              Go to Dashboard
            </Button>
          </div>
        </Card>
      </div>
    );
  }

  // Preview state - show campaign info
  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <Card className="w-full max-w-md p-8">
        <div className="text-center mb-6">
          <div className="w-16 h-16 bg-primary/10 rounded-full flex items-center justify-center mx-auto mb-4">
            <Gift className="h-8 w-8 text-primary" />
          </div>
          <h1 className="text-2xl font-bold text-foreground mb-1">
            You're Invited!
          </h1>
          {validation?.campaign_name && (
            <p className="text-muted-foreground">{validation.campaign_name}</p>
          )}
        </div>

        <div className="space-y-4 mb-6">
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted-foreground">Trial Duration</span>
            <Badge variant="secondary" className="flex items-center gap-1">
              <Clock className="h-3 w-3" />
              {validation?.trial_days} days
            </Badge>
          </div>
          <Separator />
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted-foreground">Mapping Credits</span>
            <span className="text-sm font-medium text-foreground">
              {(validation?.credits_granted || 0).toLocaleString()}
            </span>
          </div>
          <Separator />
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted-foreground">Quick Match</span>
            <span className="text-sm font-medium text-foreground">Unlimited</span>
          </div>
          <Separator />
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted-foreground">Concurrent Projects</span>
            <span className="text-sm font-medium text-foreground">3</span>
          </div>
        </div>

        {user ? (
          // Logged in - show activate button
          <Button
            className="w-full"
            onClick={handleRedeem}
            disabled={pageState === 'redeeming'}
          >
            {pageState === 'redeeming' ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Activating...
              </>
            ) : (
              <>
                Activate My Trial
                <ArrowRight className="h-4 w-4 ml-2" />
              </>
            )}
          </Button>
        ) : (
          // Not logged in - show signup/login buttons
          <div className="space-y-3">
            <Button className="w-full" onClick={handleSignupRedirect}>
              Sign Up to Activate
              <ArrowRight className="h-4 w-4 ml-2" />
            </Button>
            <p className="text-center text-sm text-muted-foreground">
              Already have an account?{' '}
              <button
                onClick={handleLoginRedirect}
                className="text-primary hover:underline font-medium"
              >
                Log in
              </button>
            </p>
          </div>
        )}
      </Card>
    </div>
  );
}
