import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import * as XLSX from 'xlsx';

// ---------------------------------------------------------------------------
// Mocks — must come before importing the component
// ---------------------------------------------------------------------------

const mockNavigate = vi.fn();
vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
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
      plan: 'pro',
      credits_limit: 1000,
      credits_used: 50,
    },
    loading: false,
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

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
  mockUploadCSVs.mockResolvedValue({ session_id: 'session-123', is_duplicate: false });
});

describe('UploadPage — rendering', () => {
  it('renders the upload page with two upload zones', () => {
    render(<UploadPage />);
    expect(screen.getByText('Old Site CSV')).toBeInTheDocument();
    expect(screen.getByText('New Site CSV')).toBeInTheDocument();
  });

  it('renders the subtitle text', () => {
    render(<UploadPage />);
    expect(screen.getByText(/Upload URL lists/)).toBeInTheDocument();
  });

  it('renders pipeline type selector for paid users', () => {
    render(<UploadPage />);
    expect(screen.getByText('Deep Match')).toBeInTheDocument();
    expect(screen.getByText('Quick Match')).toBeInTheDocument();
  });

  it('disables the begin button when no files uploaded', () => {
    render(<UploadPage />);
    const button = screen.getByRole('button', { name: /Begin/ });
    expect(button).toBeDisabled();
  });
});

describe('UploadPage — CSV file upload flow', () => {
  it('validates and shows CSV file info after upload', async () => {
    render(<UploadPage />);

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
    render(<UploadPage />);

    const badFile = new File(['data'], 'data.json', { type: 'application/json' });
    uploadToZone('Old Site CSV', badFile);

    await waitFor(() => {
      expect(screen.getByText(/\.csv, \.txt, \.xml, or \.xlsx/)).toBeInTheDocument();
    });
  });

  it('shows validation error for non-URL content', async () => {
    render(<UploadPage />);

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
    render(<UploadPage />);

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
    render(<UploadPage />);

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
    render(<UploadPage />);

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
    render(<UploadPage />);

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
    render(<UploadPage />);

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
    render(<UploadPage />);

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
    render(<UploadPage />);

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
    render(<UploadPage />);

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
    render(<UploadPage />);

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
      expect(screen.getByText(/Disable Rate Limiting/)).toBeInTheDocument();
    });

    // API should NOT have been called yet
    expect(mockUploadCSVs).not.toHaveBeenCalled();
  });

  it('keeps the begin button disabled when one file has a validation error', async () => {
    render(<UploadPage />);

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
});

describe('UploadPage — file replacement', () => {
  it('clears previous validation when uploading a new file', async () => {
    render(<UploadPage />);

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
