import { useState, useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { Card, CardContent } from './ui/card';
import { Button } from './ui/button';
import { Badge } from './ui/badge';
import { Input } from './ui/input';
import { Separator } from './ui/separator';
import { Crown, Check, AlertTriangle, ArrowRight, Loader2, CheckCircle2 } from 'lucide-react';
import { validateTrialCode, createFounderCheckout, submitWaitlistRequest, type ValidationResult } from '../api/trials';

type PageState = 'loading' | 'invalid' | 'request_invite' | 'submitted' | 'preview' | 'processing' | 'error';

export function FounderLandingPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [pageState, setPageState] = useState<PageState>('loading');
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [errorMessage, setErrorMessage] = useState('');

  // Waitlist form state
  const [waitlistForm, setWaitlistForm] = useState({ name: '', email: '', company: '' });
  const [waitlistSubmitting, setWaitlistSubmitting] = useState(false);
  const [waitlistError, setWaitlistError] = useState('');

  const codeFromUrl = searchParams.get('code');
  const pendingCode = localStorage.getItem('pending_founder_code');
  const code = codeFromUrl || pendingCode || '';

  useEffect(() => {
    if (!code) {
      setPageState('request_invite');
      return;
    }

    // Clear pending code from localStorage if we got it from URL
    if (codeFromUrl && pendingCode) {
      localStorage.removeItem('pending_founder_code');
    }

    validateTrialCode(code)
      .then((result) => {
        setValidation(result);
        if (result.valid) {
          // If this isn't a founder invite, redirect to trial page
          if (result.invite_type !== 'founder') {
            navigate(`/trial?code=${encodeURIComponent(code)}`, { replace: true });
            return;
          }
          // Clean up pending code if user returned from auth
          if (pendingCode) {
            localStorage.removeItem('pending_founder_code');
          }
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
  }, [code, user]);

  const handleCheckout = async () => {
    if (!code) return;
    setPageState('processing');

    try {
      const result = await createFounderCheckout(code);
      if (result.url) {
        window.location.href = result.url;
      } else {
        setPageState('error');
        setErrorMessage('Failed to create checkout session');
      }
    } catch (err: any) {
      setPageState('error');
      setErrorMessage(err.message || 'Checkout failed');
    }
  };

  const handleSignupRedirect = () => {
    const returnUrl = `/founder?code=${encodeURIComponent(code)}`;
    localStorage.setItem('pending_founder_code', code);
    localStorage.setItem('auth_redirect', returnUrl);
    navigate('/signup');
  };

  const handleLoginRedirect = () => {
    const returnUrl = `/founder?code=${encodeURIComponent(code)}`;
    localStorage.setItem('pending_founder_code', code);
    localStorage.setItem('auth_redirect', returnUrl);
    navigate('/login');
  };

  const handleWaitlistSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setWaitlistError('');

    const name = waitlistForm.name.trim();
    const email = waitlistForm.email.trim();
    if (!name) { setWaitlistError('Name is required'); return; }
    if (!email || !email.includes('@') || !email.includes('.')) {
      setWaitlistError('Please enter a valid email address');
      return;
    }

    setWaitlistSubmitting(true);
    try {
      await submitWaitlistRequest({
        name,
        email,
        company: waitlistForm.company.trim() || undefined,
      });
      setPageState('submitted');
    } catch (err: any) {
      setWaitlistError(err.message || 'Failed to submit request');
    } finally {
      setWaitlistSubmitting(false);
    }
  };

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

  if (pageState === 'submitted') {
    return (
      <div className="min-h-screen flex items-center justify-center p-4">
        <Card className="w-full max-w-md overflow-hidden">
          <div className="bg-gray-900 px-8 py-6 text-center">
            <div className="w-16 h-16 bg-green-500 rounded-full flex items-center justify-center mx-auto mb-4">
              <CheckCircle2 className="h-8 w-8 text-white" />
            </div>
            <h1 className="text-2xl font-bold text-white mb-1">
              Request Received
            </h1>
          </div>
          <CardContent className="p-8 text-center">
            <p className="text-foreground mb-2">
              Thanks for your interest in the Founder Package!
            </p>
            <p className="text-sm text-muted-foreground mb-6">
              We'll review your request and send you an invite link if approved.
              Keep an eye on your inbox.
            </p>
            <Button variant="outline" onClick={() => navigate('/')}>
              Go to Homepage
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (pageState === 'request_invite') {
    return (
      <div className="min-h-screen flex items-center justify-center p-4">
        <Card className="w-full max-w-md overflow-hidden">
          {/* Premium header */}
          <div className="bg-gray-900 px-8 py-6 text-center">
            <div className="w-16 h-16 bg-yellow-500 rounded-full flex items-center justify-center mx-auto mb-4">
              <Crown className="h-8 w-8 text-gray-900" />
            </div>
            <h1 className="text-2xl font-bold text-white mb-1">
              Founder Package
            </h1>
            <p className="text-gray-400 text-sm">Invite-only early access</p>
          </div>

          <CardContent className="p-8">
            {/* Plan details */}
            <div className="text-center mb-6">
              <Badge className="bg-yellow-100 text-yellow-800 border-yellow-300 mb-3">
                Founder Package
              </Badge>
              <div className="text-3xl font-bold text-foreground">
                $999
              </div>
              <p className="text-sm text-muted-foreground">one-time payment</p>
            </div>

            {/* Benefits */}
            <div className="space-y-3 mb-6">
              {[
                '500,000 lifetime mapping credits',
                'Unlimited Quick Match',
                'Up to 3 concurrent projects',
                'Lifetime access - no recurring fees',
              ].map((benefit) => (
                <div key={benefit} className="flex items-start gap-3">
                  <div className="w-5 h-5 rounded-full bg-green-100 flex items-center justify-center flex-shrink-0 mt-0.5">
                    <Check className="h-3 w-3 text-green-700" />
                  </div>
                  <span className="text-sm text-foreground">{benefit}</span>
                </div>
              ))}
            </div>

            <Separator className="mb-6" />

            {/* Request form */}
            <form onSubmit={handleWaitlistSubmit} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-foreground mb-1">
                  Name <span className="text-destructive">*</span>
                </label>
                <Input
                  value={waitlistForm.name}
                  onChange={(e) => setWaitlistForm((f) => ({ ...f, name: e.target.value }))}
                  placeholder="Your name"
                  required
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-foreground mb-1">
                  Email <span className="text-destructive">*</span>
                </label>
                <Input
                  type="email"
                  value={waitlistForm.email}
                  onChange={(e) => setWaitlistForm((f) => ({ ...f, email: e.target.value }))}
                  placeholder="you@company.com"
                  required
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-foreground mb-1">
                  Company <span className="text-muted-foreground text-xs">(optional)</span>
                </label>
                <Input
                  value={waitlistForm.company}
                  onChange={(e) => setWaitlistForm((f) => ({ ...f, company: e.target.value }))}
                  placeholder="Your company"
                />
              </div>

              {waitlistError && (
                <p className="text-sm text-destructive">{waitlistError}</p>
              )}

              <Button
                type="submit"
                className="w-full"
                disabled={waitlistSubmitting}
              >
                {waitlistSubmitting ? (
                  <>
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    Submitting...
                  </>
                ) : (
                  <>
                    Request Invite
                    <ArrowRight className="h-4 w-4 ml-2" />
                  </>
                )}
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (pageState === 'error') {
    return (
      <div className="min-h-screen flex items-center justify-center p-4">
        <Card className="w-full max-w-md p-8 text-center">
          <AlertTriangle className="h-12 w-12 mx-auto text-destructive mb-4" />
          <h1 className="text-xl font-bold text-foreground mb-2">Checkout Failed</h1>
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

  // Preview state
  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <Card className="w-full max-w-md overflow-hidden">
        {/* Premium header */}
        <div className="bg-gray-900 px-8 py-6 text-center">
          <div className="w-16 h-16 bg-yellow-500 rounded-full flex items-center justify-center mx-auto mb-4">
            <Crown className="h-8 w-8 text-gray-900" />
          </div>
          <h1 className="text-2xl font-bold text-white mb-1">
            You've Been Invited
          </h1>
          {validation?.campaign_name && (
            <p className="text-gray-400 text-sm">{validation.campaign_name}</p>
          )}
        </div>

        <CardContent className="p-8">
          {/* Plan details */}
          <div className="text-center mb-6">
            <Badge className="bg-yellow-100 text-yellow-800 border-yellow-300 mb-3">
              Founder Package
            </Badge>
            <div className="text-3xl font-bold text-foreground">
              $999
            </div>
            <p className="text-sm text-muted-foreground">one-time payment</p>
          </div>

          {/* Benefits */}
          <div className="space-y-3 mb-6">
            {[
              '500,000 lifetime mapping credits',
              'Unlimited Quick Match',
              'Up to 3 concurrent projects',
              'Lifetime access - no recurring fees',
            ].map((benefit) => (
              <div key={benefit} className="flex items-start gap-3">
                <div className="w-5 h-5 rounded-full bg-green-100 flex items-center justify-center flex-shrink-0 mt-0.5">
                  <Check className="h-3 w-3 text-green-700" />
                </div>
                <span className="text-sm text-foreground">{benefit}</span>
              </div>
            ))}
          </div>

          <Separator className="mb-6" />

          {user ? (
            <Button
              className="w-full"
              onClick={handleCheckout}
              disabled={pageState === 'processing'}
            >
              {pageState === 'processing' ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Redirecting to checkout...
                </>
              ) : (
                <>
                  Purchase Founder Package
                  <ArrowRight className="h-4 w-4 ml-2" />
                </>
              )}
            </Button>
          ) : (
            <div className="space-y-3">
              <Button className="w-full" onClick={handleSignupRedirect}>
                Sign Up to Purchase
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
        </CardContent>
      </Card>
    </div>
  );
}
