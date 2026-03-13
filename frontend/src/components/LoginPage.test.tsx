import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { LoginPage } from './LoginPage';
import { ApiError } from '../utils/errorHandler';

const mockLogin = vi.fn();
const mockStartOAuth = vi.fn();
const mockResendConfirmationEmail = vi.fn();

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({
    login: mockLogin,
    startOAuth: mockStartOAuth,
    resendConfirmationEmail: mockResendConfirmationEmail,
  }),
}));

function renderLoginPage(initialPath = '/login') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/" element={<div>Dashboard</div>} />
        <Route path="/signup" element={<div>Signup</div>} />
      </Routes>
    </MemoryRouter>
  );
}

describe('LoginPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders OAuth provider actions', () => {
    renderLoginPage();

    expect(screen.getByRole('button', { name: 'Continue with Google' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Continue with GitHub' })).toBeInTheDocument();
    expect(screen.getByText('or continue with email')).toBeInTheDocument();
  });

  it('starts OAuth flow with redirect/source context and disables inputs while connecting', async () => {
    mockStartOAuth.mockImplementationOnce(() => new Promise(() => {}));
    const user = userEvent.setup();
    renderLoginPage('/login?redirect=%2Fquick-match&source=quick-match');

    await user.click(screen.getByRole('button', { name: 'Continue with Google' }));

    expect(mockStartOAuth).toHaveBeenCalledWith('google', '/quick-match', 'quick-match');
    expect(screen.getByRole('button', { name: 'Connecting...' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Continue with GitHub' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Login' })).toBeDisabled();
  });

  it('shows generic invalid-credentials copy', async () => {
    mockLogin.mockRejectedValueOnce(
      new ApiError('provider detail', {
        code: 'auth_invalid_credentials',
        user_message: 'provider detail',
        status: 401,
      })
    );

    const user = userEvent.setup();
    renderLoginPage();

    await user.type(screen.getByPlaceholderText('you@example.com'), 'user@example.com');
    await user.type(screen.getByPlaceholderText('Enter your password'), 'BadPassword1!');
    await user.click(screen.getByRole('button', { name: /login/i }));

    await waitFor(() => {
      expect(screen.getByText('Email or password is incorrect.')).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: /resend confirmation email/i })).not.toBeInTheDocument();
  });

  it('shows unconfirmed-email state and resend action', async () => {
    mockLogin.mockRejectedValueOnce(
      new ApiError('Please confirm your email before signing in.', {
        code: 'auth_email_unconfirmed',
        user_message: 'Please confirm your email before signing in.',
        status: 403,
      })
    );

    const user = userEvent.setup();
    renderLoginPage();

    await user.type(screen.getByPlaceholderText('you@example.com'), 'user@example.com');
    await user.type(screen.getByPlaceholderText('Enter your password'), 'BadPassword1!');
    await user.click(screen.getByRole('button', { name: /login/i }));

    await waitFor(() => {
      expect(screen.getByText('Please confirm your email before signing in.')).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: /resend confirmation email/i })).toBeInTheDocument();
  });

  it('resends confirmation and starts cooldown', async () => {
    mockLogin.mockRejectedValueOnce(
      new ApiError('Please confirm your email before signing in.', {
        code: 'auth_email_unconfirmed',
        user_message: 'Please confirm your email before signing in.',
        status: 403,
      })
    );
    mockResendConfirmationEmail.mockResolvedValueOnce({
      message: 'If an unconfirmed account exists, a new confirmation email has been sent.',
    });

    const user = userEvent.setup();
    renderLoginPage();

    await user.type(screen.getByPlaceholderText('you@example.com'), 'user@example.com');
    await user.type(screen.getByPlaceholderText('Enter your password'), 'BadPassword1!');
    await user.click(screen.getByRole('button', { name: /login/i }));

    const resendButton = await screen.findByRole('button', { name: /resend confirmation email/i });
    await user.click(resendButton);

    await waitFor(() => {
      expect(mockResendConfirmationEmail).toHaveBeenCalledWith('user@example.com');
    });
    expect(
      screen.getByText('If an unconfirmed account exists, a new confirmation email has been sent.')
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /resend in 30s/i })).toBeDisabled();
  });

  it('shows rate-limit guidance', async () => {
    mockLogin.mockRejectedValueOnce(
      new ApiError('Too many sign-in attempts. Please wait and try again.', {
        code: 'auth_rate_limited',
        user_message: 'Too many sign-in attempts. Please wait and try again.',
        status: 429,
      })
    );

    const user = userEvent.setup();
    renderLoginPage();

    await user.type(screen.getByPlaceholderText('you@example.com'), 'user@example.com');
    await user.type(screen.getByPlaceholderText('Enter your password'), 'BadPassword1!');
    await user.click(screen.getByRole('button', { name: /login/i }));

    await waitFor(() => {
      expect(
        screen.getByText('Too many sign-in attempts. Please wait and try again.')
      ).toBeInTheDocument();
    });
  });

  it('shows temporary service outage guidance', async () => {
    mockLogin.mockRejectedValueOnce(
      new ApiError('Sign-in is temporarily unavailable. Please try again shortly.', {
        code: 'auth_service_unavailable',
        user_message: 'Sign-in is temporarily unavailable. Please try again shortly.',
        status: 503,
      })
    );

    const user = userEvent.setup();
    renderLoginPage();

    await user.type(screen.getByPlaceholderText('you@example.com'), 'user@example.com');
    await user.type(screen.getByPlaceholderText('Enter your password'), 'BadPassword1!');
    await user.click(screen.getByRole('button', { name: /login/i }));

    await waitFor(() => {
      expect(
        screen.getByText('Sign-in is temporarily unavailable. Please try again shortly.')
      ).toBeInTheDocument();
    });
  });
});
