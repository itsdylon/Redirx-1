import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import * as XLSX from 'xlsx';

// ---------------------------------------------------------------------------
// Mocks — must come before importing the component
// ---------------------------------------------------------------------------

const mockNavigate = vi.fn();
const mockSetSearchParams = vi.fn();
const mockSearchParams = new URLSearchParams();
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    useSearchParams: () => [mockSearchParams, mockSetSearchParams],
  };
});

const mockDiscoverSite = vi.fn();
vi.mock('../api/discovery', () => ({
  discoverSite: (...args: unknown[]) => mockDiscoverSite(...args),
}));

const mockUploadCSVs = vi.fn();
vi.mock('../api/pipeline', () => ({
  uploadCSVs: (...args: unknown[]) => mockUploadCSVs(...args),
  QuotaExceededError: class {},
}));

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({
    user: {
      id: 'user-1',
      email: 'test@example.com',
      plan: 'agency',
    },
    loading: false,
  }),
}));

vi.mock('../contexts/OnboardingContext', () => ({
  useOnboarding: () => ({
    state: {
      status: 'completed',
      path: null,
      steps: {},
    },
    loading: false,
    updateStepCompletion: vi.fn(),
    updateActivity: vi.fn(),
    selectPath: vi.fn(),
    completeOnboarding: vi.fn(),
    dismissOnboarding: vi.fn(),
    resetOnboarding: vi.fn(),
    refreshOnboarding: vi.fn(),
    startOnboarding: vi.fn(),
    maybeOpenPromptForCurrentRoute: vi.fn(),
    setEntryModalOpen: vi.fn(),
    entryModalOpen: false,
    shouldShowPromptForCurrentRoute: false,
    canStartTutorial: true,
  }),
}));

// Lightweight mock for DashboardLayout — renders children without Sidebar/TopBar
vi.mock('./DashboardLayout', () => ({
  DashboardLayout: ({ children, title }: { children: React.ReactNode; title: string }) => (
    <div data-testid="dashboard-layout" data-title={title}>
      {children}
    </div>
  ),
}));

// Lightweight mock for LoadingScreen
vi.mock('./LoadingScreen', () => ({
  LoadingScreen: ({ sessionId }: { sessionId: string | null }) => (
    <div data-testid="loading-screen">Loading session: {sessionId}</div>
  ),
}));

import { UploadPage } from './UploadPage';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------


/**
 * Render UploadPage and switch to the "Upload Files" tab — these suites
 * exercise the CSV flow, which is no longer the default ingestion mode.
 */
function renderUploadPage(props: Record<string, unknown> = {}) {
  const result = render(<UploadPage {...props} />);
  // CSV is demoted to a text link rather than a peer tab.
  fireEvent.click(screen.getByRole('button', { name: 'Import a CSV or sitemap file' }));
  return result;
}

function textFile(content: string, name: string): File {
  return new File([content], name, { type: 'text/plain' });
}

function xlsxFile(data: string[][]): File {
  const wb = XLSX.utils.book_new();
  const ws = XLSX.utils.aoa_to_sheet(data);
  XLSX.utils.book_append_sheet(wb, ws, 'Sheet1');
  const buf = XLSX.write(wb, { type: 'array', bookType: 'xlsx' });
  return new File([buf], 'urls.xlsx', {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  });
}

function sitemapXml(urls: string[]): string {
  const locs = urls.map((u) => `<url><loc>${u}</loc></url>`).join('\n');
  return `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${locs}\n</urlset>`;
}

function xmlFile(urls: string[]): File {
  return new File([sitemapXml(urls)], 'sitemap.xml', { type: 'application/xml' });
}

function largeCsvFile(rows: number, host: string, name: string): File {
  const lines = Array.from({ length: rows }, (_, i) => `https://${host}/page-${i + 1}`).join('\n');
  return textFile(lines, name);
}

/**
 * Simulate uploading a file to a FileUploadZone identified by its label text.
 * Uses fireEvent.change instead of userEvent.upload because the file input
 * has class="hidden" (display:none in Tailwind) which blocks userEvent.
 */
function uploadToZone(label: string, file: File) {
  const labelEl = screen.getByText(label);
  const container = labelEl.closest('div')!;
  const input = container.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
}

function removeFromZone(label: string) {
  const labelEl = screen.getByText(label);
  const container = labelEl.closest('div')!;
  const buttons = container.querySelectorAll('button[type="button"]');
  const removeButton = buttons[buttons.length - 1] as HTMLButtonElement | undefined;
  if (!removeButton) {
    throw new Error(`Remove button not found for zone "${label}"`);
  }
  fireEvent.click(removeButton);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
  mockUploadCSVs.mockResolvedValue({ session_id: 'session-123', is_duplicate: false });
});

describe('UploadPage — rendering', () => {
  it('renders the upload page with two upload zones', () => {
    renderUploadPage();
    expect(screen.getByText('Old Site CSV')).toBeInTheDocument();
    expect(screen.getByText('New Site CSV')).toBeInTheDocument();
  });

  it('renders the subtitle text', () => {
    renderUploadPage();
    expect(screen.getByText(/Upload URL lists/)).toBeInTheDocument();
  });

  it('renders pipeline type selector for paid users', () => {
    renderUploadPage();
    expect(screen.getByText('Deep Match')).toBeInTheDocument();
    expect(screen.getByText('Quick Match')).toBeInTheDocument();
  });

  it('disables the begin button when no files uploaded', () => {
    renderUploadPage();
    const button = screen.getByRole('button', { name: /Begin/ });
    expect(button).toBeDisabled();
  });

  it('renders quick-only start mode without deep/quick selector cards', () => {
    renderUploadPage({ quickOnly: true });
    expect(screen.queryByText('Deep Match')).not.toBeInTheDocument();
    // The explanatory "1. Upload / 2. Review / 3. Export" panel was removed —
    // the flow should be self-evident from the UI rather than narrated.
    expect(screen.queryByText('Quick Match Workflow')).not.toBeInTheDocument();
    expect(screen.getByText('Old Site CSV')).toBeInTheDocument();
  });
});

describe('UploadPage — CSV file upload flow', () => {
  it('validates and shows CSV file info after upload', async () => {
    renderUploadPage();

    const oldFile = textFile('https://old.com/page1\nhttps://old.com/page2\n', 'old.csv');
    const newFile = textFile('https://new.com/page1\nhttps://new.com/page2\n', 'new.csv');

    uploadToZone('Old Site CSV', oldFile);
    uploadToZone('New Site CSV', newFile);

    await waitFor(() => {
      // File name appears in both upload zone and file status section
      expect(screen.getAllByText('old.csv').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('new.csv').length).toBeGreaterThanOrEqual(1);
    });

    const button = screen.getByRole('button', { name: /Begin/ });
    expect(button).not.toBeDisabled();
  });

  it('shows validation error for invalid file extension', async () => {
    renderUploadPage();

    const badFile = new File(['data'], 'data.json', { type: 'application/json' });
    uploadToZone('Old Site CSV', badFile);

    await waitFor(() => {
      expect(screen.getByText(/\.csv, \.txt, \.xml, or \.xlsx/)).toBeInTheDocument();
    });
  });

  it('shows validation error for non-URL content', async () => {
    renderUploadPage();

    const lines = Array.from({ length: 20 }, (_, i) => `fruit-${i}`).join('\n');
    const badFile = textFile(lines, 'fruits.csv');
    uploadToZone('Old Site CSV', badFile);

    await waitFor(() => {
      expect(screen.getByText(/does not appear to contain URLs/)).toBeInTheDocument();
    });
  });
});

describe('UploadPage — XML file upload flow', () => {
  it('validates, converts, and shows XML sitemap info', async () => {
    renderUploadPage();

    const oldXml = xmlFile(['https://old.com/page1', 'https://old.com/page2']);
    const newCsv = textFile('https://new.com/page1\nhttps://new.com/page2\n', 'new.csv');

    uploadToZone('Old Site CSV', oldXml);
    uploadToZone('New Site CSV', newCsv);

    await waitFor(() => {
      // Displays original filename
      expect(screen.getAllByText('sitemap.xml').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('new.csv').length).toBeGreaterThanOrEqual(1);
    });

    const button = screen.getByRole('button', { name: /Begin/ });
    expect(button).not.toBeDisabled();
  });

  it('shows error for sitemap index XML', async () => {
    renderUploadPage();

    const indexXml = `<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap-posts.xml</loc></sitemap>
</sitemapindex>`;
    const file = new File([indexXml], 'sitemap.xml', { type: 'application/xml' });
    uploadToZone('Old Site CSV', file);

    await waitFor(() => {
      expect(screen.getByText(/sitemap index/)).toBeInTheDocument();
    });
  });

  it('shows error for non-sitemap XML', async () => {
    renderUploadPage();

    const rss = `<?xml version="1.0"?><rss><channel></channel></rss>`;
    const file = new File([rss], 'feed.xml', { type: 'application/xml' });
    uploadToZone('Old Site CSV', file);

    await waitFor(() => {
      expect(screen.getByText(/not a sitemap/)).toBeInTheDocument();
    });
  });
});

describe('UploadPage — XLSX file upload flow', () => {
  it('validates, converts, and shows XLSX info', async () => {
    renderUploadPage();

    const xlsx = xlsxFile([
      ['https://old.com/page1'],
      ['https://old.com/page2'],
    ]);
    const newCsv = textFile('https://new.com/page1\nhttps://new.com/page2\n', 'new.csv');

    uploadToZone('Old Site CSV', xlsx);
    uploadToZone('New Site CSV', newCsv);

    await waitFor(() => {
      expect(screen.getAllByText('urls.xlsx').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('new.csv').length).toBeGreaterThanOrEqual(1);
    });

    const button = screen.getByRole('button', { name: /Begin/ });
    expect(button).not.toBeDisabled();
  });

  it('shows error for XLSX with no URLs', async () => {
    renderUploadPage();

    const xlsx = xlsxFile([
      ['Apple'],
      ['Banana'],
      ['Cherry'],
    ]);
    uploadToZone('Old Site CSV', xlsx);

    await waitFor(() => {
      expect(screen.getByText(/does not appear to contain URLs/)).toBeInTheDocument();
    });
  });
});

describe('UploadPage — mixed format uploads', () => {
  it('allows XML for old site and XLSX for new site', async () => {
    renderUploadPage();

    const oldXml = xmlFile(['https://old.com/page1', 'https://old.com/page2']);
    const newXlsx = xlsxFile([
      ['https://new.com/page1'],
      ['https://new.com/page2'],
    ]);

    uploadToZone('Old Site CSV', oldXml);
    uploadToZone('New Site CSV', newXlsx);

    await waitFor(() => {
      expect(screen.getAllByText('sitemap.xml').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('urls.xlsx').length).toBeGreaterThanOrEqual(1);
    });

    const button = screen.getByRole('button', { name: /Begin/ });
    expect(button).not.toBeDisabled();
  });

  it('allows CSV for old site and XML for new site', async () => {
    renderUploadPage();

    const oldCsv = textFile('https://old.com/page1\nhttps://old.com/page2\n', 'old.csv');
    const newXml = xmlFile(['https://new.com/page1', 'https://new.com/page2']);

    uploadToZone('Old Site CSV', oldCsv);
    uploadToZone('New Site CSV', newXml);

    await waitFor(() => {
      expect(screen.getAllByText('old.csv').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('sitemap.xml').length).toBeGreaterThanOrEqual(1);
    });

    const button = screen.getByRole('button', { name: /Begin/ });
    expect(button).not.toBeDisabled();
  });
});

describe('UploadPage — API submission', () => {
  it('sends converted file to API for Quick Match', async () => {
    const user = userEvent.setup();
    renderUploadPage();

    // Use multiple URLs to avoid the "only 1 URL" warning
    const oldXml = xmlFile(['https://old.com/page1', 'https://old.com/page2']);
    const newCsv = textFile('https://new.com/page1\nhttps://new.com/page2\n', 'new.csv');

    uploadToZone('Old Site CSV', oldXml);
    uploadToZone('New Site CSV', newCsv);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Begin/ })).not.toBeDisabled();
    });

    // Switch to Quick Match to avoid the scraping warning flow
    await user.click(screen.getByText('Quick Match'));
    await user.click(screen.getByRole('button', { name: /Begin Quick Match/ }));

    await waitFor(() => {
      expect(mockUploadCSVs).toHaveBeenCalledTimes(1);
    });

    // The first argument (old file) should be a .txt File (converted), not the .xml
    const sentOldFile = mockUploadCSVs.mock.calls[0][0] as File;
    expect(sentOldFile.name).toBe('sitemap.txt');
    expect(sentOldFile.type).toBe('text/plain');
  });

  it('shows scraping warning for Deep Match before submitting', async () => {
    const user = userEvent.setup();
    renderUploadPage();

    const oldCsv = textFile('https://old.com/page1\nhttps://old.com/page2\n', 'old.csv');
    const newCsv = textFile('https://new.com/page1\nhttps://new.com/page2\n', 'new.csv');

    uploadToZone('Old Site CSV', oldCsv);
    uploadToZone('New Site CSV', newCsv);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Begin Deep Match/ })).not.toBeDisabled();
    });

    // Deep Match is already selected by default for pro users
    await user.click(screen.getByRole('button', { name: /Begin Deep Match/ }));

    // Scraping warning should appear
    await waitFor(() => {
      expect(screen.getByText(/We.ll read content from your pages/)).toBeInTheDocument();
    });

    // API should NOT have been called yet
    expect(mockUploadCSVs).not.toHaveBeenCalled();
  });

  it('forces duplicate Deep Match rerun without re-showing scraping warning', async () => {
    const user = userEvent.setup();
    mockUploadCSVs
      .mockResolvedValueOnce({ session_id: 'existing-session', is_duplicate: true })
      .mockResolvedValueOnce({ session_id: 'new-run', is_duplicate: false });

    renderUploadPage();

    const oldCsv = textFile('https://old.com/page1\nhttps://old.com/page2\n', 'old.csv');
    const newCsv = textFile('https://new.com/page1\nhttps://new.com/page2\n', 'new.csv');

    uploadToZone('Old Site CSV', oldCsv);
    uploadToZone('New Site CSV', newCsv);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Begin Deep Match/ })).not.toBeDisabled();
    });

    await user.click(screen.getByRole('button', { name: /Begin Deep Match/ }));

    await waitFor(() => {
      expect(screen.getByText(/We.ll read content from your pages/)).toBeInTheDocument();
    });

    await user.click(
      screen.getByRole('checkbox', { name: /Got it .* read content from my pages/i })
    );
    await user.click(screen.getByRole('button', { name: /Proceed with Deep Match/ }));

    await waitFor(() => {
      expect(screen.getByText(/Duplicate Request Detected/)).toBeInTheDocument();
    });

    expect(mockUploadCSVs).toHaveBeenCalledTimes(1);
    expect(mockUploadCSVs).toHaveBeenNthCalledWith(1, expect.any(File), expect.any(File), false, 'content');

    await user.click(screen.getByRole('button', { name: /Proceed Anyway/ }));

    await waitFor(() => {
      expect(mockUploadCSVs).toHaveBeenCalledTimes(2);
    });

    expect(mockUploadCSVs).toHaveBeenNthCalledWith(2, expect.any(File), expect.any(File), true, 'content');

    await waitFor(() => {
      expect(screen.getByTestId('loading-screen')).toHaveTextContent('new-run');
    });
    expect(screen.queryByText(/We.ll read content from your pages/)).not.toBeInTheDocument();
  });

  it('keeps the begin button disabled when one file has a validation error', async () => {
    renderUploadPage();

    // Upload a valid file for old site
    const oldCsv = textFile('https://old.com/page1\nhttps://old.com/page2\n', 'old.csv');
    uploadToZone('Old Site CSV', oldCsv);

    // Upload an invalid file for new site
    const badFile = new File(['data'], 'bad.json', { type: 'application/json' });
    uploadToZone('New Site CSV', badFile);

    await waitFor(() => {
      const button = screen.getByRole('button', { name: /Begin/ });
      expect(button).toBeDisabled();
    });
  });

  it('blocks Deep Match locally when file counts exceed the content URL cap', async () => {
    renderUploadPage();

    uploadToZone('Old Site CSV', largeCsvFile(5001, 'old.example.com', 'old-large.csv'));
    uploadToZone('New Site CSV', largeCsvFile(5001, 'new.example.com', 'new-large.csv'));

    await waitFor(() => {
      expect(screen.getByText(/Deep Match URL Limit Reached/)).toBeInTheDocument();
      expect(screen.getAllByText(/Old Site CSV has 5,001 URLs/).length).toBeGreaterThan(0);
      expect(screen.getAllByText(/New Site CSV has 5,001 URLs/).length).toBeGreaterThan(0);
    });

    expect(screen.getByRole('button', { name: /Begin Deep Match/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Switch to Quick Match/ })).toBeInTheDocument();
  });

  it('switches to Quick Match from cap panel and falls back to warning flow', async () => {
    const user = userEvent.setup();
    renderUploadPage();

    uploadToZone('Old Site CSV', largeCsvFile(5001, 'old.example.com', 'old-large.csv'));
    uploadToZone('New Site CSV', largeCsvFile(5001, 'new.example.com', 'new-large.csv'));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Switch to Quick Match/ })).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: /Switch to Quick Match/ }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Begin Quick Match/ })).not.toBeDisabled();
    });

    await user.click(screen.getByRole('button', { name: /Begin Quick Match/ }));

    await waitFor(() => {
      expect(screen.getByText(/File Warnings Detected/)).toBeInTheDocument();
      expect(screen.getAllByText(/Large dataset detected/).length).toBeGreaterThan(0);
    });
  });

  it('shows actionable cap details when API returns structured cap error', async () => {
    const user = userEvent.setup();
    mockUploadCSVs.mockRejectedValueOnce({
      type: 'content_url_cap_exceeded',
      message: 'Deep Match has a per-file limit of 5,000 URLs. Split your CSV or switch to Quick Match.',
      reason_code: 'content_old_url_cap_exceeded',
      old_url_count: 5001,
      new_url_count: 42,
      max_old_urls: 5000,
      max_new_urls: 5000,
      affected_file: 'old',
      next_action: 'reduce_csv_rows_or_switch_pipeline',
      retryable: false,
    });

    renderUploadPage();

    const oldCsv = textFile('https://old.com/page1\nhttps://old.com/page2\n', 'old.csv');
    const newCsv = textFile('https://new.com/page1\nhttps://new.com/page2\n', 'new.csv');
    uploadToZone('Old Site CSV', oldCsv);
    uploadToZone('New Site CSV', newCsv);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Begin Deep Match/ })).not.toBeDisabled();
    });

    await user.click(screen.getByRole('button', { name: /Begin Deep Match/ }));
    await waitFor(() => {
      expect(screen.getByText(/We.ll read content from your pages/)).toBeInTheDocument();
    });
    await user.click(
      screen.getByRole('checkbox', { name: /Got it .* read content from my pages/i })
    );
    await user.click(screen.getByRole('button', { name: /Proceed with Deep Match/ }));

    await waitFor(() => {
      expect(screen.getByText(/Deep Match URL Limit Reached/)).toBeInTheDocument();
      expect(
        screen.getAllByText(/Old Site CSV has 5,001 URLs; Deep Match allows up to 5,000/).length
      ).toBeGreaterThan(0);
      expect(screen.getByRole('button', { name: /Switch to Quick Match/ })).toBeInTheDocument();
    });
  });
});

describe('UploadPage — file replacement', () => {
  it('clears previous validation when uploading a new file', async () => {
    renderUploadPage();

    // First upload an invalid file
    const badLines = Array.from({ length: 20 }, (_, i) => `fruit-${i}`).join('\n');
    const badFile = textFile(badLines, 'fruits.csv');
    uploadToZone('Old Site CSV', badFile);

    await waitFor(() => {
      expect(screen.getByText(/does not appear to contain URLs/)).toBeInTheDocument();
    });

    // Now upload a valid file — error should clear
    const goodFile = textFile('https://example.com/page1\nhttps://example.com/page2\n', 'good.csv');
    uploadToZone('Old Site CSV', goodFile);

    await waitFor(() => {
      expect(screen.queryByText(/does not appear to contain URLs/)).not.toBeInTheDocument();
      expect(screen.getAllByText('good.csv').length).toBeGreaterThanOrEqual(1);
    });
  });
});

describe('UploadPage — file removal', () => {
  it('removes a selected file and disables begin matching', async () => {
    renderUploadPage();

    const oldCsv = textFile('https://old.com/page1\nhttps://old.com/page2\n', 'old.csv');
    const newCsv = textFile('https://new.com/page1\nhttps://new.com/page2\n', 'new.csv');

    uploadToZone('Old Site CSV', oldCsv);
    uploadToZone('New Site CSV', newCsv);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Begin Deep Match/ })).not.toBeDisabled();
    });

    removeFromZone('Old Site CSV');

    await waitFor(() => {
      expect(screen.queryByText('old.csv')).not.toBeInTheDocument();
      expect(screen.getAllByText('new.csv').length).toBeGreaterThanOrEqual(1);
      expect(screen.getByRole('button', { name: /Begin Deep Match/ })).toBeDisabled();
    });
  });
});

describe('UploadPage — domain ingestion mode', () => {
  function discoveryResponse(host: string, count: number) {
    return {
      success: true,
      root_url: `https://${host}`,
      urls: Array.from({ length: count }, (_, i) => `https://${host}/page-${i + 1}`),
      count,
      total_found: count,
      truncated: false,
      max_urls: 1000,
      method: 'sitemap',
      generator: 'wordpress',
      duration_ms: 900,
      plan: 'free',
    };
  }

  it('defaults to the paste-domains mode', () => {
    render(<UploadPage />);
    expect(screen.getByText(/Old site/)).toBeInTheDocument();
    expect(screen.getByText(/New site/)).toBeInTheDocument();
    expect(screen.queryByText('Old Site CSV')).not.toBeInTheDocument();
  });

  it('discovers both domains and submits the generated URL files', async () => {
    const user = userEvent.setup();
    mockDiscoverSite
      .mockResolvedValueOnce(discoveryResponse('old-site.com', 3))
      .mockResolvedValueOnce(discoveryResponse('new-site.com', 4));

    render(<UploadPage quickOnly />);

    await user.type(screen.getByLabelText(/Old site .* domain/), 'old-site.com');
    await user.click(screen.getAllByRole('button', { name: 'Find Pages' })[0]);

    await waitFor(() => {
      expect(screen.getByText(/3 pages found via sitemap/)).toBeInTheDocument();
    });

    await user.type(screen.getByLabelText(/New site .* domain/), 'new-site.com');
    await user.click(screen.getByRole('button', { name: 'Find Pages' }));

    await waitFor(() => {
      expect(screen.getByText(/4 pages found via sitemap/)).toBeInTheDocument();
    });

    const begin = screen.getByRole('button', { name: /Begin Quick Match/ });
    expect(begin).not.toBeDisabled();
    await user.click(begin);

    await waitFor(() => {
      expect(mockUploadCSVs).toHaveBeenCalledTimes(1);
    });

    const [oldFile, newFile, , pipelineType] = mockUploadCSVs.mock.calls[0];
    expect((oldFile as File).name).toBe('old-site.com-discovered.csv');
    expect((newFile as File).name).toBe('new-site.com-discovered.csv');
    expect(pipelineType).toBe('url_only');
    expect(await (oldFile as File).text()).toContain('https://old-site.com/page-1');
  });

  it('shows discovery errors and keeps begin disabled', async () => {
    const user = userEvent.setup();
    mockDiscoverSite.mockRejectedValueOnce(new Error('Could not find any pages on https://dead.com.'));

    render(<UploadPage quickOnly />);

    await user.type(screen.getByLabelText(/Old site .* domain/), 'dead.com');
    await user.click(screen.getAllByRole('button', { name: 'Find Pages' })[0]);

    await waitFor(() => {
      expect(screen.getByText(/Could not find any pages/)).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: /Begin Quick Match/ })).toBeDisabled();
  });

  it('switching modes clears prior selections', async () => {
    const user = userEvent.setup();
    mockDiscoverSite.mockResolvedValueOnce(discoveryResponse('old-site.com', 3));

    render(<UploadPage quickOnly />);
    await user.type(screen.getByLabelText(/Old site .* domain/), 'old-site.com');
    await user.click(screen.getAllByRole('button', { name: 'Find Pages' })[0]);
    await waitFor(() => {
      expect(screen.getByText(/3 pages found/)).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: 'Import a CSV or sitemap file' }));
    expect(screen.queryByText(/3 pages found/)).not.toBeInTheDocument();
    expect(screen.getByText('Old Site CSV')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Begin Quick Match/ })).toBeDisabled();
  });
});

describe('UploadPage — traffic risk beat', () => {
  function withTraffic(host: string, count: number, clicks: number[]) {
    const urls = Array.from({ length: count }, (_, i) => `https://${host}/page-${i + 1}`);
    return {
      success: true,
      root_url: `https://${host}`,
      urls,
      entries: urls.map((url, i) => ({
        url,
        sources: ['gsc', 'sitemap'],
        clicks: clicks[i] ?? 0,
        impressions: (clicks[i] ?? 0) * 10,
      })),
      count,
      total_found: count,
      truncated: false,
      max_urls: 1000,
      method: 'gsc',
      generator: 'wordpress',
      summary: {
        total: count,
        with_traffic: clicks.filter((c) => c > 0).length,
        gsc_only: 0,
        no_recorded_traffic: clicks.filter((c) => !c).length,
        total_clicks: clicks.reduce((a, b) => a + b, 0),
        total_impressions: clicks.reduce((a, b) => a + b, 0) * 10,
      },
      plan: 'free',
    };
  }

  async function discoverBothSides(user: ReturnType<typeof userEvent.setup>) {
    await user.type(screen.getByLabelText(/Old site .* domain/), 'old.com');
    await user.click(screen.getAllByRole('button', { name: 'Find Pages' })[0]);
    await waitFor(() => expect(screen.getByText(/pages found/)).toBeInTheDocument());
    await user.type(screen.getByLabelText(/New site .* domain/), 'new.com');
    await user.click(screen.getByRole('button', { name: 'Find Pages' }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Begin Quick Match/ })).not.toBeDisabled()
    );
  }

  it('shows the risk number before matching, and defers submission until confirmed', async () => {
    const user = userEvent.setup();
    // One page carries 900 of 1000 clicks: a single URL should account for 80%.
    mockDiscoverSite
      .mockResolvedValueOnce(withTraffic('old.com', 4, [900, 50, 30, 20]))
      .mockResolvedValueOnce(withTraffic('new.com', 4, [0, 0, 0, 0]));

    render(<UploadPage quickOnly />);
    await discoverBothSides(user);
    await user.click(screen.getByRole('button', { name: /Begin Quick Match/ }));

    await waitFor(() => {
      expect(screen.getByText(/carry 80% of your organic clicks/)).toBeInTheDocument();
    });
    // The job must not have been submitted yet — the beat comes first.
    expect(mockUploadCSVs).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: /Map these to|Match these to/ }));
    await waitFor(() => expect(mockUploadCSVs).toHaveBeenCalledTimes(1));
  });

  it('can be backed out of without losing the discovered domains', async () => {
    const user = userEvent.setup();
    mockDiscoverSite
      .mockResolvedValueOnce(withTraffic('old.com', 3, [500, 10, 5]))
      .mockResolvedValueOnce(withTraffic('new.com', 3, [0, 0, 0]));

    render(<UploadPage quickOnly />);
    await discoverBothSides(user);
    await user.click(screen.getByRole('button', { name: /Begin Quick Match/ }));
    await waitFor(() => expect(screen.getByText(/carry 80%/)).toBeInTheDocument());

    await user.click(screen.getByRole('button', { name: 'Back to domains' }));
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Begin Quick Match/ })).not.toBeDisabled();
    });
    expect(mockUploadCSVs).not.toHaveBeenCalled();
  });

  it('skips the beat when there is no Search Console data to show', async () => {
    // Sitemap-only discovery has nothing to say about what matters, so the
    // number would be meaningless — go straight to matching.
    const user = userEvent.setup();
    mockDiscoverSite
      .mockResolvedValueOnce(withTraffic('old.com', 3, [0, 0, 0]))
      .mockResolvedValueOnce(withTraffic('new.com', 3, [0, 0, 0]));

    render(<UploadPage quickOnly />);
    await discoverBothSides(user);
    await user.click(screen.getByRole('button', { name: /Begin Quick Match/ }));

    await waitFor(() => expect(mockUploadCSVs).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(/carry 80%/)).not.toBeInTheDocument();
  });
});
