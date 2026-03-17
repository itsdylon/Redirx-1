import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { ToolLayout } from './ToolLayout';

const mockUseAuth = vi.fn();
const mockLogout = vi.fn().mockResolvedValue(undefined);

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => mockUseAuth(),
}));

function LocationEcho() {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
}

function renderLayout(initialEntry: string) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <ToolLayout title="URL Based Matching">
        <LocationEcho />
      </ToolLayout>
    </MemoryRouter>,
  );
}

describe('ToolLayout navigation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseAuth.mockReturnValue({
      user: { id: 'tool-user', email: 'tool@example.com', plan: 'free' },
      logout: mockLogout,
    });
  });

  it('marks Tools as active on review routes and renders breadcrumb', () => {
    renderLayout('/review/session-1');

    const primaryNav = screen.getByRole('navigation', { name: 'Primary' });
    const toolsButton = within(primaryNav).getByRole('button', { name: 'Tools' });
    const projectHistoryButton = within(primaryNav).getByRole('button', { name: 'Project History' });

    expect(toolsButton).toHaveAttribute('aria-current', 'page');
    expect(projectHistoryButton).not.toHaveAttribute('aria-current');

    const breadcrumbNav = screen.getByRole('navigation', { name: 'Breadcrumb' });
    expect(within(breadcrumbNav).getByText('URL Based Matching')).toBeInTheDocument();
    expect(within(breadcrumbNav).getByText('Review Redirects')).toBeInTheDocument();
  });

  it('marks Project History as active on /projects and hides breadcrumb', () => {
    renderLayout('/projects');

    const primaryNav = screen.getByRole('navigation', { name: 'Primary' });
    const toolsButton = within(primaryNav).getByRole('button', { name: 'Tools' });
    const projectHistoryButton = within(primaryNav).getByRole('button', { name: 'Project History' });

    expect(projectHistoryButton).toHaveAttribute('aria-current', 'page');
    expect(toolsButton).not.toHaveAttribute('aria-current');
    expect(screen.queryByRole('navigation', { name: 'Breadcrumb' })).not.toBeInTheDocument();
  });

  it('shows pricing breadcrumb only when source_session_id is present', () => {
    renderLayout('/pricing?source_session_id=session-1');
    expect(screen.getByRole('navigation', { name: 'Breadcrumb' })).toBeInTheDocument();
    expect(screen.getByText('Project Pricing')).toBeInTheDocument();
  });

  it('does not render breadcrumb on url-match root', () => {
    renderLayout('/url-match');
    expect(screen.queryByRole('navigation', { name: 'Breadcrumb' })).not.toBeInTheDocument();
  });

  it('opens mobile navigation sheet with fixed link order and navigates', async () => {
    const user = userEvent.setup();
    renderLayout('/review/session-1');

    await user.click(screen.getByRole('button', { name: 'Open navigation menu' }));

    const mobileNav = screen.getByRole('navigation', { name: 'Primary mobile' });
    const labels = within(mobileNav)
      .getAllByRole('button')
      .map((button) => button.textContent?.trim());
    expect(labels.slice(0, 3)).toEqual(['URL Based Matching', 'Content Based Matching', 'Project History']);

    await user.click(within(mobileNav).getByRole('button', { name: 'Project History' }));
    expect(screen.getByTestId('location')).toHaveTextContent('/projects');
  });

  it('uses a profile dropdown for email and logout instead of inline navbar text', async () => {
    const user = userEvent.setup();
    renderLayout('/url-match');

    expect(screen.queryByText('tool@example.com')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Logout' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Open profile menu' }));

    expect(screen.getByText('tool@example.com')).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'Logout' })).toBeInTheDocument();
  });
});
