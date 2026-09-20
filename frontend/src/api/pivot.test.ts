import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { checkout, createSubscriptionCheckout, getSubscriptionCheckout, getPivotMigration, listPivotMatches,
  listPivotMigrations, monitoring, PivotRequestError, resolvePivotMatch } from './pivot';

const envelope = (data: unknown, changes = {}) => ({ contract_version: '1.0.0', migration_id: 'm',
  operation_id: null, status: 'succeeded', next_action: 'none', data, error: null, ...changes });
const response = (data: unknown, status = 200) => new Response(JSON.stringify(data), {
  status, headers: { 'Content-Type': 'application/json' },
});

describe('pivot browser transport', () => {
  const fetchMock = vi.fn();
  beforeEach(() => { localStorage.setItem('access_token', 'browser-token'); vi.stubGlobal('fetch', fetchMock); fetchMock.mockReset(); });
  afterEach(() => { localStorage.clear(); vi.unstubAllGlobals(); });

  it('sends the current browser bearer token and preserves explicit decision identity', async () => {
    fetchMock.mockImplementation(() => response(envelope({ items: [], outcomes: [{ code: 'ok' }] })));
    const signal = new AbortController().signal;
    await listPivotMatches('m/a', 'r/b', 'opaque+/cursor', signal);
    await resolvePivotMatch('m/a', 'r/b', { mapping_id: 'x', expected_revision: 2, action: 'set_target',
      target_url: 'https://new.example/Case?x=1' }, 'stable-key', signal);
    expect(fetchMock.mock.calls[0][0]).toContain('/migrations/m%2Fa/runs/r%2Fb/matches?filter=needs_review&limit=20&cursor=opaque%2B%2Fcursor');
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ signal, headers: { Authorization: 'Bearer browser-token' } });
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: 'PATCH', signal });
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ idempotency_key: 'stable-key',
      decisions: [{ mapping_id: 'x', expected_revision: 2, action: 'set_target', target_url: 'https://new.example/Case?x=1' }] });
  });

  it.each([401, 403, 500])('rejects HTTP%s even when it carries superficially successful data', async status => {
    fetchMock.mockResolvedValue(response(envelope({ items: [] }), status));
    await expect(listPivotMigrations()).rejects.toMatchObject({ status });
  });

  it('rejects an error envelope on HTTP200 instead of returning empty history', async () => {
    fetchMock.mockResolvedValue(response(envelope({ items: [] }, { error: {
      code: 'reconnect_required', message: 'Reconnect your account.', retryable: false, next_action: 'reconnect',
    } })));
    await expect(listPivotMigrations()).rejects.toMatchObject({ code: 'reconnect_required', message: 'Reconnect your account.' });
  });

  it('rejects invalid JSON and the wrong contract instead of reporting success', async () => {
    fetchMock.mockResolvedValueOnce(new Response('not JSON')).mockResolvedValueOnce(response(envelope({}, { contract_version: '0.1' })));
    await expect(listPivotMigrations()).rejects.toBeInstanceOf(PivotRequestError);
    await expect(listPivotMigrations()).rejects.toMatchObject({ code: 'invalid_response' });
  });

  it('retains monitoring identity and numeric fixes cursor in query parameters', async () => {
    fetchMock.mockImplementation(() => response(envelope({ items: [] })));
    await monitoring('m', false, 'monitor/1'); await monitoring('m', true, 'monitor/1', 0);
    const status = new URL(fetchMock.mock.calls[0][0], 'https://fixture.invalid');
    const fixes = new URL(fetchMock.mock.calls[1][0], 'https://fixture.invalid');
    expect(status.searchParams.get('monitoring_id')).toBe('monitor/1');
    expect(status.searchParams.has('after')).toBe(false);
    expect(fixes.pathname).toBe('/api/v2/migrations/m/monitoring/fixes');
    expect(Object.fromEntries(fixes.searchParams)).toEqual({ monitoring_id: 'monitor/1', after: '0', limit: '20' });
  });

  it('uses POST and explicit consent for checkout while return remains a server read', async () => {
    fetchMock.mockImplementation(() => response(envelope({ state: 'open' })));
    await checkout('m', 'q', '11111111-1111-4111-8111-111111111111', 'oneoff-key');
    await createSubscriptionCheckout({ sku: 'monitoring', deployment_id: 'deployment-1', recurring_consent: true }, 'subscription-key');
    await getSubscriptionCheckout('checkout/1', true);
    expect(fetchMock.mock.calls[0][1].method).toBe('POST');
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ operation_id: '11111111-1111-4111-8111-111111111111', idempotency_key: 'oneoff-key' });
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ sku: 'monitoring', deployment_id: 'deployment-1', recurring_consent: true, idempotency_key: 'subscription-key' });
    expect(fetchMock.mock.calls[2][0]).toContain('/billing/subscription-checkouts/checkout%2F1/return');
    expect(fetchMock.mock.calls[2][1].method).toBeUndefined();
    expect(fetchMock.mock.calls[2][1].body).toBeUndefined();
  });

  it('forwards AbortSignal and preserves cancellation', async () => {
    const controller = new AbortController();
    fetchMock.mockImplementation((_url, init) => new Promise((_resolve, reject) => {
      init.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
    }));
    const pending = listPivotMigrations(undefined, controller.signal);
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true);
  });
  it('preserves the durable run operation from a reloaded payment-required summary into checkout', async () => {
    const operation = '11111111-1111-4111-8111-111111111111';
    fetchMock.mockResolvedValueOnce(response(envelope({ quote_id: 'quote-1',
      quote: { operation_id: '22222222-2222-4222-8222-222222222222', amount_cents: 4900, currency: 'usd', kind: 'paid' },
    }, { operation_id: operation, status: 'payment_required', next_action: 'complete_payment' })))
      .mockResolvedValueOnce(response(envelope({ checkout_url: 'https://checkout.stripe.com/fixture', state: 'open' })));
    const summary = await getPivotMigration('migration-1');
    const signal = new AbortController().signal;
    const result = await checkout('migration-1', summary.data.quote_id!, summary.operation_id!, 'retry-key', signal);
    expect(result.data.checkout_url).toBe('https://checkout.stripe.com/fixture');
    expect(fetchMock.mock.calls[1][0]).toContain('/migrations/migration-1/quotes/quote-1/checkout');
    expect(fetchMock.mock.calls[1][1].signal).toBe(signal);
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ operation_id: operation, idempotency_key: 'retry-key' });
  });

  it.each([undefined, null, '', 'quote-operation-not-a-uuid'])('refuses missing or malformed checkout operation %s before sending a request', async operation => {
    await expect(checkout('migration-1', 'quote-1', operation as unknown as string, 'retry-key')).rejects.toMatchObject({ code: 'invalid_response' });
    expect(fetchMock).not.toHaveBeenCalled();
  });

});
