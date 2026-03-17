import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { SignupPage } from './SignupPage';

const mockRegister = vi.fn();
const mockStartOAuth = vi.fn();
const mockCapture = vi.fn();

vi.mock('@posthog/react', () => ({
  usePostHog: () => ({
    capture: mockCapture,
  }),
}));

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({
    register: mockRegister,
    startOAuth: mockStartOAuth,
  }),
}));

function renderSignupPage(initialPath = '/signup') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/signup" element={<SignupPage />} />
        <Route path="/login" element={<div>Login</div>} />
        <Route path="/" element={<div>Home</div>} />
      </Routes>
    </MemoryRouter>
  );
}

describe('SignupPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('renders OAuth provider actions', () => {
    renderSignupPage();

    expect(screen.getByRole('button', { name: 'Continue with Google' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Continue with GitHub' })).toBeInTheDocument();
    expect(screen.getByText('or continue with email')).toBeInTheDocument();
  });

  it('tracks signup page view', () => {
    renderSignupPage('/signup?redirect=%2Fquick-match&source=quick-match');

    expect(mockCapture).toHaveBeenCalledWith(
      'signup_page_viewed',
      expect.objectContaining({
        source: 'quick-match',
        redirect: '/quick-match',
        authenticated: false,
      }),
    );
  });

  it('starts OAuth flow with redirect/source context and disables submit while connecting', async () => {
    mockStartOAuth.mockImplementationOnce(() => new Promise(() => {}));
    const user = userEvent.setup();
    renderSignupPage('/signup?redirect=%2Fquick-match&source=quick-match');

    await user.click(screen.getByRole('button', { name: 'Continue with GitHub' }));

    expect(mockStartOAuth).toHaveBeenCalledWith('github', '/quick-match', 'quick-match');
    expect(screen.getByRole('button', { name: 'Connecting...' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Continue with Google' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Sign Up' })).toBeDisabled();
  });
});
