import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { ReactNode } from 'react';
import { PivotCompanionPage } from '../PivotCompanionPage';
import { PivotMigrationDetail } from '../PivotMigrationDetail';
import { SubscriptionCheckoutPanel } from '../SubscriptionCheckoutPanel';

// Keep the actual screens, controls, request-scope hook, API and auth headers.
// Only the unrelated authenticated navigation shell is replaced.
vi.mock('../ToolLayout', () => ({ ToolLayout: ({ children }: { children: ReactNode }) => <>{children}</> }));
const envelope = (data: unknown, changes: Record<string, unknown> = {}) => ({ contract_version: '1.0.0',
  migration_id: 'm1', operation_id: 'op1', status: 'succeeded', next_action: 'none', data, error: null, ...changes });
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status,
  headers: { 'Content-Type': 'application/json' } });
const failure = (message = 'Try this request again.') => json(envelope({}, { error: {
  code: 'unavailable', message, retryable: true, next_action: 'retry',
} }), 503);
const row = (id: string, revision = 0) => ({ mapping_id: id, old_url: `https://old.example/${id}`,
  new_url: `https://new.example/${id}`, revision, review_status: 'needs_review', traffic_observed: false, traffic_clicks: null });
const detail = (changes = {}) => ({ migration: { id: 'm1', name: 'Autumn migration' }, run_id: 'run1',
  run: { status: 'succeeded' }, ...changes });
type Request = { url: URL; init: RequestInit; body: Record<string, any> };
let requests: Request[];
let handler: (request: Request) => Response | Promise<Response>;
const paths = (part: string, method?: string) => requests.filter(r => r.url.pathname.includes(part) && (!method || r.init.method === method));
function mountDetail(search = '') {
  return render(<MemoryRouter initialEntries={[`/migrations/m1${search}`]}><Routes>
    <Route path="/migrations/:migrationId" element={<PivotMigrationDetail />} />
  </Routes></MemoryRouter>);
}
function mountHistory() { return render(<MemoryRouter><PivotCompanionPage /></MemoryRouter>); }
function mountBilling(search = '', deploymentId?: string) {
  return render(<MemoryRouter initialEntries={[`/billing/subscriptions/return${search}`]}><SubscriptionCheckoutPanel deploymentId={deploymentId} /></MemoryRouter>);
}
function ordinary(r: Request): Response {
  if (r.url.pathname === '/api/v2/migrations/m1') return json(envelope(detail()));
  if (r.url.pathname.endsWith('/matches') && !r.init.method) return json(envelope({ items: [row('a')], next_cursor: null }));
  throw new Error(`Unexpected fixture request: ${r.init.method || 'GET'} ${r.url}`);
}

beforeEach(() => {
  requests = []; handler = ordinary; localStorage.setItem('access_token', 'browser-fixture');
  vi.stubGlobal('fetch', vi.fn((url: string, init: RequestInit = {}) => {
    const request = { url: new URL(url, 'https://fixture.invalid'), init, body: init.body ? JSON.parse(String(init.body)) : {} };
    requests.push(request); return Promise.resolve(handler(request));
  }));
});
afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); });

describe('companion history through its actual API', () => {
  it.each([401, 200])('shows an auth failure on HTTP%s without claiming empty history', async status => {
    handler = () => json(envelope({ items: [] }, { error: { code: 'reconnect_required', message: 'Sign in again to view migrations.', retryable: false, next_action: 'reconnect' } }), status);
    mountHistory();
    expect(await screen.findByRole('alert')).toHaveTextContent('Sign in again');
    expect(screen.queryByText(/No migrations yet/)).not.toBeInTheDocument();
    expect(requests[0].init.headers).toMatchObject({ Authorization: 'Bearer browser-fixture' });
  });

  it('appends cursor pages and refreshes back to the first page', async () => {
    handler = r => json(envelope({ items: [{ id: r.url.searchParams.has('cursor') ? 'm2' : 'm1',
      name: r.url.searchParams.has('cursor') ? 'Second migration' : 'First migration' }],
      next_cursor: r.url.searchParams.has('cursor') ? null : 'opaque+/cursor' }));
    const user = userEvent.setup(); mountHistory();
    await user.click(await screen.findByRole('button', { name: 'Load more migrations' }));
    expect(await screen.findByRole('link', { name: /Second migration/ })).toHaveAttribute('href', '/migrations/m2');
    expect(screen.getByRole('link', { name: /First migration/ })).toBeInTheDocument();
    expect(requests[1].url.searchParams.get('cursor')).toBe('opaque+/cursor');
    await user.click(screen.getByRole('button', { name: 'Refresh history' }));
    await waitFor(() => expect(screen.queryByRole('link', { name: /Second migration/ })).not.toBeInTheDocument());
    expect(requests[2].url.searchParams.has('cursor')).toBe(false);
  });
});

describe('migration decisions and payment', () => {
  it('shows one-off checkout only for payment-required status and retains its retry key', async () => {
    let paid = false; let attempts = 0;
    handler = r => {
      if (r.url.pathname.endsWith('/checkout')) { attempts++; return attempts === 1 ? failure() : json(envelope({ state: 'open' })); }
      if (r.url.pathname === '/api/v2/migrations/m1') return json(envelope(detail({ run_id: undefined,
        quote_id: 'q1', quote: { amount_cents: 4900, currency: 'usd', kind: 'paid' } }),
        { status: paid ? 'succeeded' : 'payment_required', next_action: paid ? 'none' : 'complete_payment' }));
      return ordinary(r);
    };
    const user = userEvent.setup(); mountDetail('?paid=true&payment_return=success');
    const button = await screen.findByRole('button', { name: 'Continue to checkout' });
    expect(screen.getByText('$49.00 for this migration.')).toBeInTheDocument();
    expect(within(screen.getByRole('region', { name: 'Payment required' })).getByText(/Test checkout only/)).toBeInTheDocument();
    expect(paths('/checkout')).toHaveLength(0);
    await user.click(button); await screen.findByText('Try this request again.');
    await user.click(button); await screen.findByText(/Checkout status: open/);
    const calls = paths('/quotes/q1/checkout', 'POST'); expect(calls).toHaveLength(2);
    expect(calls[0].body.idempotency_key).toBeTruthy(); expect(calls[0].body).toEqual(calls[1].body);
    paid = true; await user.click(screen.getByRole('button', { name: 'Refresh status' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Continue to checkout' })).not.toBeInTheDocument());
  });

  it('refreshes exceptions after a saved decision even when the run ID is unchanged', async () => {
    let saved = false;
    handler = r => {
      if (r.init.method === 'PATCH') { saved = true; return json(envelope({ outcomes: [{ code: 'ok' }] })); }
      if (r.url.pathname.endsWith('/matches')) return json(envelope({ items: saved ? [row('b', 1)] : [row('a')], next_cursor: null }));
      return ordinary(r);
    };
    const user = userEvent.setup(); mountDetail();
    await user.click(await screen.findByRole('button', { name: 'Approve', exact: true }));
    expect(await screen.findByLabelText('Destination for https://old.example/b')).toBeInTheDocument();
    expect(screen.queryByLabelText('Destination for https://old.example/a')).not.toBeInTheDocument();
    expect(paths('/matches').every(r => r.url.pathname.includes('/runs/run1/'))).toBe(true);
    expect(paths('/matches', 'PATCH')[0].body.decisions).toEqual([{ mapping_id: 'a', expected_revision: 0, action: 'approve' }]);
    expect(paths('/api/v2/migrations/m1').filter(r => r.url.pathname === '/api/v2/migrations/m1')).toHaveLength(2);
  });

  it('does not claim there are no exceptions when the exception request fails', async () => {
    handler = r => r.url.pathname.endsWith('/matches') ? failure('Exception review is temporarily unavailable.') : ordinary(r);
    mountDetail();
    expect(await screen.findByRole('alert')).toHaveTextContent('Exception review is temporarily unavailable.');
    expect(screen.queryByText('No exceptions on this page.')).not.toBeInTheDocument();
  });

  it('retains a row and explains a per-row partial failure rather than reporting a saved decision', async () => {
    handler = r => r.init.method === 'PATCH'
      ? json(envelope({ outcomes: [{ code: 'revision_conflict', message: 'This mapping changed. Refresh before retrying.' }] }, { status: 'partial' })) : ordinary(r);
    const user = userEvent.setup(); mountDetail();
    await user.click(await screen.findByRole('button', { name: 'Approve', exact: true }));
    expect(await screen.findByRole('alert')).toHaveTextContent('This mapping changed');
    expect(screen.getByLabelText('Destination for https://old.example/a')).toBeInTheDocument();
    expect(paths('/matches').filter(r => !r.init.method)).toHaveLength(1);
  });

  it('reuses decision keys on retry but changes identity when the target changes', async () => {
    handler = r => r.init.method === 'PATCH' ? failure() : ordinary(r);
    const user = userEvent.setup(); mountDetail();
    const input = await screen.findByLabelText('Destination for https://old.example/a');
    await user.type(input, 'https://new.example/first');
    const save = screen.getByRole('button', { name: 'Set destination' });
    await user.click(save); await screen.findByRole('alert');
    await user.click(save); await screen.findByRole('alert');
    await user.clear(input); await user.type(input, 'https://new.example/second');
    await user.click(save); await screen.findByRole('alert');
    const calls = paths('/matches', 'PATCH'); expect(calls).toHaveLength(3);
    expect(calls[0].body.idempotency_key).toBe(calls[1].body.idempotency_key);
    expect(calls[2].body.idempotency_key).not.toBe(calls[1].body.idempotency_key);
    expect(calls[2].body.decisions[0]).toMatchObject({ expected_revision: 0, target_url: 'https://new.example/second' });
  });

  it('appends exceptions with the exact opaque cursor and renders unmeasured traffic honestly', async () => {
    handler = r => r.url.pathname.endsWith('/matches') ? json(envelope({
      items: [row(r.url.searchParams.has('cursor') ? 'b' : 'a')],
      next_cursor: r.url.searchParams.has('cursor') ? null : 'exceptions+/next',
    })) : ordinary(r);
    const user = userEvent.setup(); mountDetail();
    await user.click(await screen.findByRole('button', { name: 'Load more exceptions' }));
    expect(await screen.findByLabelText('Destination for https://old.example/b')).toBeInTheDocument();
    expect(screen.getByLabelText('Destination for https://old.example/a')).toBeInTheDocument();
    expect(paths('/matches')[1].url.searchParams.get('cursor')).toBe('exceptions+/next');
    expect(screen.getAllByText(/Search traffic not measured/)).toHaveLength(2);
    expect(screen.queryByRole('button', { name: 'Load more exceptions' })).not.toBeInTheDocument();
  });
});

describe('Search Console and monitoring', () => {
  it('loads properties and syncs only the explicitly selected property', async () => {
    handler = r => r.url.pathname.endsWith('/connections/search-console/actions') ? json(envelope(r.body.action === 'properties'
      ? { properties: [{ site_url: 'sc-domain:old.example' }, { site_url: 'https://old.example/blog/' }] }
      : { summary: 'Historical traffic synced.' })) : ordinary(r);
    const user = userEvent.setup(); mountDetail();
    expect(screen.getByRole('button', { name: 'Sync selected property' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Load properties' }));
    await user.selectOptions(await screen.findByLabelText('Search Console property'), 'https://old.example/blog/');
    await user.click(screen.getByRole('button', { name: 'Sync selected property' }));
    expect(await screen.findByText('Historical traffic synced.')).toBeInTheDocument();
    const calls = paths('/connections/search-console/actions');
    expect(calls.map(r => r.body.action)).toEqual(['properties', 'sync']);
    expect(calls[1].body).toMatchObject({ migration_id: 'm1', property: 'https://old.example/blog/' });
    expect(calls[1].body.idempotency_key).toBeTruthy();
  });

  it('uses the reentry monitoring ID for status and paged fixes, rendering measured coverage', async () => {
    handler = r => {
      if (r.url.pathname.endsWith('/monitoring')) return json(envelope({ state: 'active', coverage: { checked: 2, total: 5, unchecked: 3 } }));
      if (r.url.pathname.endsWith('/monitoring/fixes')) return json(envelope({ items: [{ issue_id: r.url.searchParams.get('after') === '-1' ? 'i1' : 'i2',
        source_url: r.url.searchParams.get('after') === '-1' ? 'https://old.example/broken' : 'https://old.example/offline',
        expected_url: 'https://new.example/fixed', evidence: { issue: 'wrong_destination' } }],
        next_cursor: r.url.searchParams.get('after') === '-1' ? 0 : null }));
      if (r.url.pathname === '/api/v2/migrations/m1') return json(envelope(detail({ monitoring_id: 'latest-other-monitor' })));
      return ordinary(r);
    };
    const user = userEvent.setup(); mountDetail('?monitoring_id=monitor-reentry');
    expect(await screen.findByText('https://old.example/broken')).toBeInTheDocument();
    expect(screen.getByText('Coverage: 2 of 5 URLs checked. 3 unchecked.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Load more corrections' }));
    expect(await screen.findByText('https://old.example/offline')).toBeInTheDocument();
    expect(screen.getAllByText('wrong destination')).toHaveLength(2);
    const calls = paths('/monitoring'); expect(calls).toHaveLength(3);
    expect(calls.every(r => r.url.searchParams.get('monitoring_id') === 'monitor-reentry')).toBe(true);
    expect(calls[2].url.searchParams.get('after')).toBe('0');
  });
});

describe('subscription consent and request cancellation', () => {
  it('requires explicit recurring consent, keeps retry identity and resets consent when the SKU changes', async () => {
    handler = () => failure();
    const user = userEvent.setup(); mountBilling('', 'installed-deployment');
    const button = screen.getByRole('button', { name: 'Continue to secure checkout' });
    expect(screen.getByText(/Test checkout only/)).toBeInTheDocument();
    expect(screen.getByRole('checkbox')).toHaveAccessibleName(/in test mode/);
    expect(button).toBeDisabled(); expect(requests).toHaveLength(0);
    await user.click(screen.getByRole('checkbox')); await user.click(button); await screen.findByRole('alert');
    await user.click(button); await screen.findByRole('alert');
    expect(requests[0].body).toMatchObject({ sku: 'studio', recurring_consent: true });
    expect(requests[0].body).not.toHaveProperty('deployment_id');
    expect(requests[0].body.idempotency_key).toBe(requests[1].body.idempotency_key);
    await user.selectOptions(screen.getByRole('combobox', { name: 'Subscription' }), 'monitoring');
    expect(screen.getByRole('checkbox')).not.toBeChecked(); expect(button).toBeDisabled();
    await user.click(screen.getByRole('checkbox')); await user.click(button); await screen.findByRole('alert');
    expect(requests[2].body).toMatchObject({ sku: 'monitoring', deployment_id: 'installed-deployment', recurring_consent: true });
    expect(requests[2].body.idempotency_key).not.toBe(requests[0].body.idempotency_key);
  });

  it('does not treat a migration checkout return as a subscription checkout', async () => {
    handler = r => r.url.pathname.includes('/billing/subscription-checkouts/')
      ? json(envelope({}, { error: { code: 'not_found', message: 'Subscription checkout not found.', retryable: false, next_action: 'none' } }), 404)
      : ordinary(r);
    mountDetail('?checkout_id=oneoff-checkout&payment_return=success');
    await screen.findByLabelText('Destination for https://old.example/a');
    expect(paths('/billing/subscription-checkouts/')).toHaveLength(0);
    expect(screen.queryByText('Subscription checkout not found.')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Refresh checkout status' })).not.toBeInTheDocument();
  });

  it('treats a forged success return as a read of server state, never as paid authority', async () => {
    handler = () => json(envelope({ checkout_id: 'c1', state: 'open', sku: 'studio', monthly_amount_cents: 9900,
      currency: 'usd', interval: 'month', subscription: { status: 'incomplete', eligible: false } }));
    const user = userEvent.setup(); mountBilling('?checkout_id=c1&paid=true&success=true');
    expect(await screen.findByText('Checkout status: open')).toBeInTheDocument();
    expect(screen.getByText(/No current allowance is available/)).toBeInTheDocument();
    expect(screen.queryByText('Current allowance is available.')).not.toBeInTheDocument();
    expect(screen.getByRole('checkbox')).not.toBeChecked();
    await user.click(screen.getByRole('button', { name: 'Refresh checkout status' }));
    await waitFor(() => expect(requests).toHaveLength(2));
    expect(requests.every(r => r.url.pathname.endsWith('/subscription-checkouts/c1/return') && !r.init.method && !r.init.body)).toBe(true);
  });

  it('aborts a pending history request on unmount and ignores a late response', async () => {
    let finish!: (response: Response) => void;
    handler = () => new Promise(resolve => { finish = resolve; });
    const view = mountHistory();
    expect(requests).toHaveLength(1);
    const signal = requests[0].init.signal!; expect(signal.aborted).toBe(false);
    view.unmount(); expect(signal.aborted).toBe(true);
    await act(async () => { finish(json(envelope({ items: [{ id: 'late', name: 'Late history' }] }))); });
    expect(screen.queryByText('Late history')).not.toBeInTheDocument();
  });

  it('aborts an in-flight decision when the detail unmounts', async () => {
    let finish!: (response: Response) => void;
    handler = r => r.init.method === 'PATCH' ? new Promise(resolve => { finish = resolve; }) : ordinary(r);
    const user = userEvent.setup(); const view = mountDetail();
    await user.click(await screen.findByRole('button', { name: 'Approve', exact: true }));
    const signal = paths('/matches', 'PATCH')[0].init.signal!;
    view.unmount(); expect(signal.aborted).toBe(true);
    await act(async () => { finish(json(envelope({ outcomes: [{ code: 'ok' }] }))); });
    expect(paths('/matches').filter(r => !r.init.method)).toHaveLength(1);
  });
});
