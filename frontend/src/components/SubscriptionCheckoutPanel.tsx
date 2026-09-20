import { useEffect, useState } from 'react';
import { useLocation, useSearchParams } from 'react-router-dom';
import { createSubscriptionCheckout, getSubscriptionCheckout, type SubscriptionCheckout } from '../api/pivot';
import { Button } from './ui/button';
import { useRequestScope } from './pivot/useRequestScope';
import { PivotBillingNotice, PIVOT_TEST_BILLING } from './PivotBillingNotice';

export function SubscriptionCheckoutPanel({ deploymentId }: { deploymentId?: string }) {
  const [query] = useSearchParams();
  const location = useLocation();
  const checkoutId = location.pathname === '/billing/subscriptions/return' ? query.get('checkout_id') : null;
  const scope = useRequestScope(`billing:${deploymentId || 'account'}:${checkoutId || ''}`);
  const [sku, setSku] = useState<'studio' | 'monitoring'>('studio');
  const [consent, setConsent] = useState(false);
  const [message, setMessage] = useState('');
  const [record, setRecord] = useState<SubscriptionCheckout | null>(null);
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    if (!checkoutId) return;
    const signal = scope.signal();
    setBusy(true);
    void getSubscriptionCheckout(checkoutId, true, signal).then(result => {
      if (!signal.aborted) { setRecord(result.data); setMessage(''); }
    }).catch(e => { if (!signal.aborted) setMessage(e instanceof Error ? e.message : 'Unable to verify checkout. Refresh its status.'); })
      .finally(() => { if (!signal.aborted) setBusy(false); });
  }, [checkoutId, refresh]);
  async function begin() {
    if (!consent) return;
    const signal = scope.signal();
    setBusy(true); setMessage('');
    try {
      const result = await createSubscriptionCheckout({ sku, recurring_consent: true,
        ...(sku === 'monitoring' ? { deployment_id: deploymentId } : {}) }, scope.key(`subscribe:${sku}:${deploymentId || ''}`), signal);
      if (signal.aborted) return;
      setRecord(result.data);
      if (result.data.checkout_url) window.location.assign(result.data.checkout_url);
    } catch (e) { if (!signal.aborted) setMessage(e instanceof Error ? e.message : 'Checkout could not start. Try again.'); }
    finally { if (!signal.aborted) setBusy(false); }
  }
  return <section aria-labelledby="billing-heading" className="space-y-3 border-t pt-6">
    <h2 id="billing-heading" className="text-lg font-semibold">Subscription billing</h2>
    <PivotBillingNotice />
    {record && <div role="status" className="space-y-1 text-sm">
      <p>Checkout status: {record.state}</p>
      {record.subscription && <p>Subscription: {record.subscription.status}. {record.subscription.eligible ? 'Current allowance is available.' : 'No current allowance is available.'}</p>}
    </div>}
    {checkoutId && <Button variant="outline" disabled={busy} onClick={() => setRefresh(n => n + 1)}>Refresh checkout status</Button>}
    <p className="max-w-prose text-sm text-muted-foreground">Studio includes five migrations per billing period for $99/month. Monitoring is $29/month for an installed site. Recurring billing requires your consent at checkout.</p>
    <label className="block max-w-lg space-y-2 text-sm">Subscription
      <select className="block w-full rounded-md border bg-background p-2" value={sku} disabled={busy}
        onChange={e => { setSku(e.target.value as 'studio' | 'monitoring'); setConsent(false); }}>
        <option value="studio">Studio — $99/month</option>
        {deploymentId && <option value="monitoring">Monitoring — $29/month</option>}
      </select>
    </label>
    {!deploymentId && <p className="text-sm text-muted-foreground">To subscribe to monitoring, open an installed migration.</p>}
    <label className="flex items-start gap-2 text-sm"><input className="mt-1" type="checkbox" checked={consent} disabled={busy}
      onChange={e => setConsent(e.target.checked)} />I agree to {sku === 'studio' ? '$99' : '$29'} monthly recurring billing{PIVOT_TEST_BILLING ? ' in test mode' : ''}.</label>
    <Button disabled={busy || !consent} onClick={() => void begin()}>{busy ? 'Checking billing…' : 'Continue to secure checkout'}</Button>
    {message && <p role="alert" className="text-sm">{message}</p>}
  </section>;
}
