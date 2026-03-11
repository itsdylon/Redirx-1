import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QuickMatchLandingPage } from './QuickMatchLandingPage';
import { MemoryRouter } from 'react-router-dom';

const mockNavigate = vi.fn();
const mockCapture = vi.fn();
const mockUseAuth = vi.fn();

vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

vi.mock('@posthog/react', () => ({
  usePostHog: () => ({
    capture: mockCapture,
  }),
}));

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => mockUseAuth(),
}));

vi.mock('./UploadPage', () => ({
  UploadPage: () => <div>Quick Upload Experience</div>,
}));

describe('QuickMatchLandingPage', () => {
  function renderPage() {
    return render(
      <MemoryRouter>
        <QuickMatchLandingPage />
      </MemoryRouter>,
    );
  }

  beforeEach(() => {
    vi.clearAllMocks();
    mockUseAuth.mockReturnValue({ user: null });
    localStorage.clear();
  });

  it('tracks tool view and routes signup CTA to auth flow', async () => {
    const user = userEvent.setup();
    renderPage();

    expect(mockCapture).toHaveBeenCalledWith(
      'quick_match_tool_viewed',
      expect.objectContaining({
        source: 'quick-match',
      }),
    );

    await user.click(screen.getByRole('button', { name: 'Sign up with email' }));

    expect(mockCapture).toHaveBeenCalledWith(
      'quick_match_auth_gate_clicked',
      expect.objectContaining({
        target: 'signup',
      }),
    );
    expect(mockNavigate).toHaveBeenCalledWith('/signup?redirect=%2Fquick-match&source=quick-match');
    expect(localStorage.getItem('auth_redirect')).toBe('/quick-match');
  });

  it('renders authenticated quick upload experience', async () => {
    mockUseAuth.mockReturnValue({
      user: { id: 'u1', email: 'test@example.com', plan: 'free' },
    });

    renderPage();
    expect(await screen.findByText('Quick Upload Experience')).toBeInTheDocument();
  });
});
