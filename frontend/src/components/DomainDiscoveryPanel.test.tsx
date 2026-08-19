import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const mockDiscoverSite = vi.fn();
vi.mock('../api/discovery', () => ({
  discoverSite: (...args: unknown[]) => mockDiscoverSite(...args),
}));

const mockGetGscStatus = vi.fn();
const mockGetGscProperties = vi.fn();
const mockGetGscConnectUrl = vi.fn();
vi.mock('../api/gsc', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/gsc')>();
  return {
    ...actual,
    getGscStatus: () => mockGetGscStatus(),
    getGscProperties: () => mockGetGscProperties(),
    getGscConnectUrl: (...args: unknown[]) => mockGetGscConnectUrl(...args),
  };
});

import { DomainDiscoveryPanel } from './DomainDiscoveryPanel';
import { formatProperty, propertyCovers } from '../api/gsc';

function renderPanel(side: 'old' | 'new' = 'old') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <DomainDiscoveryPanel
        side={side}
        label={side === 'old' ? 'Old site' : 'New site'}
        onDiscovered={vi.fn()}
      />
    </QueryClientProvider>
  );
}

const DISCOVERY_RESULT = {
  success: true,
  root_url: 'https://example.com',
  urls: ['https://example.com/a'],
  count: 1,
  total_found: 1,
  truncated: false,
  max_urls: 1000,
  method: 'gsc',
  generator: null,
  plan: 'free',
};

beforeEach(() => {
  vi.clearAllMocks();
  mockDiscoverSite.mockResolvedValue(DISCOVERY_RESULT);
  mockGetGscStatus.mockResolvedValue({ success: true, connected: false, configured: true });
  mockGetGscProperties.mockResolvedValue([]);
});

describe('property formatting', () => {
  it('renders a domain property as a domain, not Google wire format', () => {
    expect(formatProperty('sc-domain:example.com')).toBe('example.com (all subdomains)');
  });

  it('strips scheme and trailing slash from a URL-prefix property', () => {
    expect(formatProperty('https://www.example.com/')).toBe('www.example.com');
  });
});

describe('property matching', () => {
  it('matches a domain property against its subdomains', () => {
    expect(propertyCovers('sc-domain:example.com', 'example.com')).toBe(true);
    expect(propertyCovers('sc-domain:example.com', 'shop.example.com')).toBe(true);
    expect(propertyCovers('sc-domain:example.com', 'notexample.com')).toBe(false);
  });

  it('is www-insensitive for URL-prefix properties', () => {
    expect(propertyCovers('https://www.example.com/', 'example.com')).toBe(true);
    expect(propertyCovers('https://example.com/', 'www.example.com')).toBe(true);
  });

  it('does not match a different host', () => {
    expect(propertyCovers('https://other.com/', 'example.com')).toBe(false);
  });

  it('tolerates a pasted URL rather than a bare host', () => {
    expect(propertyCovers('sc-domain:example.com', 'https://example.com/pricing')).toBe(true);
  });
});

describe('Search Console offer on the old side', () => {
  it('labels the action by outcome, never by the vendor integration', async () => {
    // Spec rule: the CTA sells what the user gets, not the plumbing.
    renderPanel('old');
    expect(await screen.findByRole('button', { name: /pages that get traffic/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /connect google search console/i })).toBeNull();
  });

  it('states the access is read-only before sending anyone to a consent screen', async () => {
    renderPanel('old');
    await screen.findByRole('button', { name: /pages that get traffic/i });
    expect(screen.getByText(/Read-only/)).toBeInTheDocument();
    expect(screen.getByText(/never change anything on your site/i)).toBeInTheDocument();
  });

  it('is absent on the new side, which has no traffic to lose', async () => {
    renderPanel('new');
    await screen.findByRole('button', { name: 'Find Pages' });
    expect(screen.queryByRole('button', { name: /pages that get traffic/i })).toBeNull();
    expect(mockGetGscStatus).not.toHaveBeenCalled();
  });

  it('offers nothing when the server has no OAuth client configured', async () => {
    mockGetGscStatus.mockResolvedValue({ success: true, connected: false, configured: false });
    renderPanel('old');
    await screen.findByRole('button', { name: 'Find Pages' });
    await waitFor(() => expect(mockGetGscStatus).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: /pages that get traffic/i })).toBeNull();
  });
});

describe('property selection when connected', () => {
  beforeEach(() => {
    mockGetGscStatus.mockResolvedValue({ success: true, connected: true, configured: true });
  });

  it('names the property it will pull traffic from', async () => {
    mockGetGscProperties.mockResolvedValue([
      { site_url: 'sc-domain:example.com', permission_level: 'siteOwner' },
    ]);
    renderPanel('old');
    await userEvent.type(screen.getByLabelText('Old site domain'), 'example.com');
    expect(await screen.findByText(/example\.com \(all subdomains\)/)).toBeInTheDocument();
  });

  it('prefers the domain property when both cover the host', async () => {
    mockGetGscProperties.mockResolvedValue([
      { site_url: 'https://www.example.com/', permission_level: 'siteOwner' },
      { site_url: 'sc-domain:example.com', permission_level: 'siteOwner' },
    ]);
    renderPanel('old');
    await userEvent.type(screen.getByLabelText('Old site domain'), 'example.com');
    await screen.findByText(/all subdomains/);

    await userEvent.click(screen.getByRole('button', { name: 'Find Pages' }));
    await waitFor(() =>
      expect(mockDiscoverSite).toHaveBeenCalledWith('example.com', 'old', 'sc-domain:example.com')
    );
  });

  it('says so plainly when no property covers the domain', async () => {
    mockGetGscProperties.mockResolvedValue([
      { site_url: 'sc-domain:other.com', permission_level: 'siteOwner' },
    ]);
    renderPanel('old');
    await userEvent.type(screen.getByLabelText('Old site domain'), 'example.com');
    expect(
      await screen.findByText(/No Search Console property covers this domain/)
    ).toBeInTheDocument();
  });

  it('discovers without a property when the user opts out of Search Console', async () => {
    mockGetGscProperties.mockResolvedValue([
      { site_url: 'sc-domain:example.com', permission_level: 'siteOwner' },
      { site_url: 'sc-domain:other.com', permission_level: 'siteOwner' },
    ]);
    renderPanel('old');
    await userEvent.type(screen.getByLabelText('Old site domain'), 'example.com');
    await userEvent.click(await screen.findByRole('button', { name: 'Change' }));
    await userEvent.selectOptions(screen.getByLabelText('Search Console property'), '');

    await userEvent.click(screen.getByRole('button', { name: 'Find Pages' }));
    await waitFor(() =>
      expect(mockDiscoverSite).toHaveBeenCalledWith('example.com', 'old', undefined)
    );
  });
});
