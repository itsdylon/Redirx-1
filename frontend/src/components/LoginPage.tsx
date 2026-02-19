import { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Card } from './ui/card';
import { validateEmail } from '../utils/validation';

export function LoginPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [emailError, setEmailError] = useState('');
  const [emailTouched, setEmailTouched] = useState(false);

  const { login } = useAuth();
  const navigate = useNavigate();

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

    setLoading(true);

    try {
      await login(email, password);
      const redirect = localStorage.getItem('auth_redirect');
      if (redirect) {
        localStorage.removeItem('auth_redirect');
        navigate(redirect);
      } else {
        navigate('/');
      }
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-4">
      <Card className="w-full max-w-md p-8">
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-foreground">Login to Redirx</h1>
          <p className="text-muted-foreground text-sm mt-1">
            Enter your credentials to access your account
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
              placeholder="Enter your password"
              required
              autoComplete="current-password"
              className="w-full"
            />
          </div>

          <Button
            type="submit"
            className="w-full"
            disabled={loading || !!emailError || !email}
          >
            {loading ? 'Logging in...' : 'Login'}
          </Button>
        </form>

        <p className="mt-6 text-center text-sm text-muted-foreground">
          Don't have an account?{' '}
          <Link to="/signup" className="text-primary hover:underline font-medium">
            Sign up
          </Link>
        </p>
      </Card>

      <div className="mt-8 max-w-md text-center px-4">
        <p className="text-xs text-muted-foreground leading-relaxed">
          Limited early partner program for agencies shaping the next generation of redirect automation.{' '}
          <a
            href="#"
            className="text-primary hover:underline font-medium"
          >
            Apply for access &rarr;
          </a>
        </p>
      </div>
    </div>
  );
}
