import { useState, useMemo, useEffect } from 'react';
import { useNavigate, Link, useSearchParams } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Card } from './ui/card';
import { Progress } from './ui/progress';
import { CheckCircle2, Circle } from 'lucide-react';
import { calculatePasswordStrength, validateEmail } from '../utils/validation';

export function SignupPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [fullName, setFullName] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [emailSent, setEmailSent] = useState(false);
  const [sentToEmail, setSentToEmail] = useState('');
  const [emailError, setEmailError] = useState('');
  const [emailTouched, setEmailTouched] = useState(false);

  const { register } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  useEffect(() => {
    const prefillEmail = (searchParams.get('email') || '').trim();
    if (!prefillEmail || email) {
      return;
    }

    setEmail(prefillEmail);
    const validation = validateEmail(prefillEmail);
    if (!validation.valid) {
      setEmailError(validation.error || 'Invalid email');
      setEmailTouched(true);
    }
  }, [searchParams, email]);

  // Calculate password strength in real-time
  const passwordStrength = useMemo(
    () => calculatePasswordStrength(password),
    [password]
  );

  // Get color based on strength
  const getStrengthColor = (strength: string) => {
    switch (strength) {
      case 'weak':
        return 'bg-red-500';
      case 'fair':
        return 'bg-yellow-500';
      case 'good':
        return 'bg-green-500';
      case 'strong':
        return 'bg-green-600';
      default:
        return 'bg-gray-300';
    }
  };

  // Get progress value (0-100)
  const getProgressValue = (score: number) => {
    return (score / 5) * 100;
  };

  const handleEmailBlur = () => {
    setEmailTouched(true);
    const validation = validateEmail(email);
    if (!validation.valid) {
      setEmailError(validation.error || 'Invalid email');
    } else {
      setEmailError('');
    }
  };

  const handleEmailChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setEmail(e.target.value);
    // Clear error when user starts typing again
    if (emailTouched) {
      setEmailError('');
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    // Validate email before submission
    const emailValidation = validateEmail(email);
    if (!emailValidation.valid) {
      setEmailError(emailValidation.error || 'Invalid email');
      setEmailTouched(true);
      return;
    }

    // Validation
    if (password !== confirmPassword) {
      setError('Passwords do not match');
      return;
    }

    // Require at least 'fair' password strength
    if (passwordStrength.strength === 'weak') {
      setError('Password is too weak. Please meet more security requirements.');
      return;
    }

    setLoading(true);

    try {
      const result = await register(email, password, fullName);

      if (result.emailConfirmationRequired) {
        // Show email confirmation message
        setEmailSent(true);
        setSentToEmail(result.email || email);
      } else {
        // Immediate login - check for pending redirect
        const redirect = localStorage.getItem('auth_redirect');
        if (redirect) {
          localStorage.removeItem('auth_redirect');
          navigate(redirect);
        } else {
          navigate('/');
        }
      }
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  // Show success message after email is sent
  if (emailSent) {
    return (
      <div className="min-h-screen flex items-center justify-center p-4">
        <Card className="w-full max-w-md p-8 text-center">
          <div className="mb-6">
            <div className="w-16 h-16 bg-primary/10 rounded-full flex items-center justify-center mx-auto mb-4">
              <svg
                className="w-8 h-8 text-primary"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"
                />
              </svg>
            </div>
            <h1 className="text-2xl font-bold text-foreground">Check your email</h1>
            <p className="text-muted-foreground mt-2">
              We've sent a confirmation link to
            </p>
            <p className="text-foreground font-medium mt-1">{sentToEmail}</p>
          </div>

          <p className="text-muted-foreground text-sm mb-6">
            Click the link in the email to confirm your account and get started.
          </p>

          <Link to="/login">
            <Button variant="outline" className="w-full">
              Back to Login
            </Button>
          </Link>
        </Card>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <Card className="w-full max-w-md p-8">
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-foreground">Create Account</h1>
          <p className="text-muted-foreground text-sm mt-1">
            Sign up to start generating redirect mappings
          </p>
        </div>

        {error && (
          <div className="bg-destructive/10 border border-destructive/50 text-destructive px-4 py-3 rounded mb-4">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-foreground mb-2">
              Full Name
            </label>
            <Input
              type="text"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="John Doe"
              required
              autoComplete="name"
              className="w-full"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-foreground mb-2">
              Email
            </label>
            <Input
              type="email"
              value={email}
              onChange={handleEmailChange}
              onBlur={handleEmailBlur}
              placeholder="you@example.com"
              required
              autoComplete="email"
              className={`w-full ${emailError ? 'border-destructive' : ''}`}
            />
            {emailError && (
              <p className="text-destructive text-sm mt-1">{emailError}</p>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-foreground mb-2">
              Password
            </label>
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="At least 8 characters"
              required
              autoComplete="new-password"
              className="w-full"
            />

            {/* Password Strength Meter */}
            {password && (
              <div className="mt-3 space-y-2">
                {/* Strength Bar */}
                <div className="space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-muted-foreground">
                      Password strength:
                    </span>
                    <span className={`text-xs font-medium capitalize ${
                      passwordStrength.strength === 'weak' ? 'text-red-500' :
                      passwordStrength.strength === 'fair' ? 'text-yellow-500' :
                      passwordStrength.strength === 'good' ? 'text-green-500' :
                      'text-green-600'
                    }`}>
                      {passwordStrength.strength}
                    </span>
                  </div>
                  <div className="relative">
                    <Progress
                      value={getProgressValue(passwordStrength.score)}
                      className="h-2"
                    />
                    <div
                      className={`absolute top-0 left-0 h-2 rounded-full transition-all ${getStrengthColor(passwordStrength.strength)}`}
                      style={{ width: `${getProgressValue(passwordStrength.score)}%` }}
                    />
                  </div>
                </div>

                {/* Requirements Checklist */}
                <div className="space-y-1.5 pt-1">
                  <div className="flex items-center gap-2">
                    {passwordStrength.criteria.minLength ? (
                      <CheckCircle2 className="w-4 h-4 text-green-500" />
                    ) : (
                      <Circle className="w-4 h-4 text-muted-foreground/50" />
                    )}
                    <span className={`text-xs ${
                      passwordStrength.criteria.minLength
                        ? 'text-foreground'
                        : 'text-muted-foreground'
                    }`}>
                      At least 8 characters
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    {passwordStrength.criteria.hasUppercase ? (
                      <CheckCircle2 className="w-4 h-4 text-green-500" />
                    ) : (
                      <Circle className="w-4 h-4 text-muted-foreground/50" />
                    )}
                    <span className={`text-xs ${
                      passwordStrength.criteria.hasUppercase
                        ? 'text-foreground'
                        : 'text-muted-foreground'
                    }`}>
                      Uppercase letter
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    {passwordStrength.criteria.hasLowercase ? (
                      <CheckCircle2 className="w-4 h-4 text-green-500" />
                    ) : (
                      <Circle className="w-4 h-4 text-muted-foreground/50" />
                    )}
                    <span className={`text-xs ${
                      passwordStrength.criteria.hasLowercase
                        ? 'text-foreground'
                        : 'text-muted-foreground'
                    }`}>
                      Lowercase letter
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    {passwordStrength.criteria.hasNumber ? (
                      <CheckCircle2 className="w-4 h-4 text-green-500" />
                    ) : (
                      <Circle className="w-4 h-4 text-muted-foreground/50" />
                    )}
                    <span className={`text-xs ${
                      passwordStrength.criteria.hasNumber
                        ? 'text-foreground'
                        : 'text-muted-foreground'
                    }`}>
                      Number
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    {passwordStrength.criteria.hasSpecial ? (
                      <CheckCircle2 className="w-4 h-4 text-green-500" />
                    ) : (
                      <Circle className="w-4 h-4 text-muted-foreground/50" />
                    )}
                    <span className={`text-xs ${
                      passwordStrength.criteria.hasSpecial
                        ? 'text-foreground'
                        : 'text-muted-foreground'
                    }`}>
                      Special character
                    </span>
                  </div>
                </div>
              </div>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-foreground mb-2">
              Confirm Password
            </label>
            <Input
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="Re-enter password"
              required
              autoComplete="new-password"
              className="w-full"
            />
          </div>

          <Button
            type="submit"
            className="w-full"
            disabled={loading || (password.length > 0 && passwordStrength.strength === 'weak') || !!emailError || !email}
          >
            {loading ? 'Creating account...' : 'Sign Up'}
          </Button>
        </form>

        <p className="mt-6 text-center text-sm text-muted-foreground">
          Already have an account?{' '}
          <Link to="/login" className="text-primary hover:underline font-medium">
            Login
          </Link>
        </p>
      </Card>
    </div>
  );
}
