import { useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { checkout, downloadArtifact, getPivotMigration, listPivotMatches, monitoring, resolvePivotMatch,
  type MigrationDetail, type MonitoringData, type MonitoringIssue, type PivotEnvelope, type PivotMatch } from '../api/pivot';
import { ToolLayout } from './ToolLayout';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { SubscriptionCheckoutPanel } from './SubscriptionCheckoutPanel';
import { SearchConsolePanel } from './pivot/SearchConsolePanel';
import { useRequestScope } from './pivot/useRequestScope';

function MonitoringPanel({ migrationId, monitoringId }: { migrationId: string; monitoringId?: string | null }) {
  const scope = useRequestScope(`monitor:${migrationId}:${monitoringId || ''}`);
  const [data, setData] = useState<MonitoringData | null>(null);
  const [issues, setIssues] = useState<MonitoringIssue[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    if (!monitoringId) return;
    const controller = new AbortController();
    setBusy(true); setError('');
    void Promise.all([monitoring(migrationId, false, monitoringId, -1, controller.signal),
      monitoring(migrationId, true, monitoringId, -1, controller.signal)]).then(([status, fixes]) => {
      if (controller.signal.aborted) return;
      setData(status.data); setIssues(fixes.data.items || []); setNext(fixes.data.next_cursor ?? null);
    }).catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Monitoring could not load. Refresh to retry.'); })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [migrationId, monitoringId, refresh]);
  async function more() {
    if (next === null) return;
    const signal = scope.signal();
    setBusy(true);
    try {
      const result = await monitoring(migrationId, true, monitoringId, next, signal);
      if (!signal.aborted) { setIssues(current => [...current, ...(result.data.items || [])]); setNext(result.data.next_cursor ?? null); }
    } catch (e) { if (!signal.aborted) setError(e instanceof Error ? e.message : 'Corrections could not load. Try again.'); }
    finally { if (!signal.aborted) setBusy(false); }
  }
  return <section className="space-y-3 border-t pt-6" aria-labelledby="monitoring-heading">
    <h2 id="monitoring-heading" className="text-lg font-semibold">Monitoring</h2>
    {!monitoringId ? <p className="text-sm text-muted-foreground">Monitoring has not started for this migration. Ask your agent to confirm installation and check eligibility.</p> : <>
      <Button variant="outline" disabled={busy} onClick={() => setRefresh(n => n + 1)}>Refresh monitoring</Button>
      {busy && <p role="status">Loading monitoring…</p>}
      {error && <p role="alert">{error}</p>}
      {data && <><p className="text-sm">Monitoring state: {data.state}</p><p className="text-sm">{data.summary}</p>
        <p className="text-sm">Coverage: {data.coverage?.checked ?? 0} of {data.coverage?.total ?? 'unknown'} URLs checked. {data.coverage?.unchecked ?? 'Unknown'} unchecked.</p>
      </>}
      <ul className="divide-y">{issues.map(issue => <li className="space-y-1 py-3 text-sm" key={issue.issue_id}>
        <p className="break-all font-medium">{issue.source_url}</p>
        <p>{(issue.issue || issue.evidence?.issue || 'Needs review').replaceAll('_', ' ')}</p>
        {issue.expected_url && <p className="break-all">Expected destination: {issue.expected_url}</p>}
        <p>{issue.suggested_action === 'retry_check' ? 'Retry the check when the origin is available.' : 'Restore the saved artifact rule, deploy it, then verify again.'}</p>
      </li>)}</ul>
      {data && !busy && !error && !issues.length && <p className="text-sm">No recorded correction items. Coverage above shows what has actually been checked.</p>}
      {next !== null && <Button variant="outline" disabled={busy} onClick={() => void more()}>Load more corrections</Button>}
    </>}
  </section>;
}

export function PivotMigrationDetail() {
  const { migrationId = '' } = useParams();
  const [search] = useSearchParams();
  const scope = useRequestScope(migrationId);
  const [summary, setSummary] = useState<PivotEnvelope<MigrationDetail> | null>(null);
  const [rows, setRows] = useState<PivotMatch[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [targets, setTargets] = useState<Record<string, string>>({});
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(true);
  const [matchesLoaded, setMatchesLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    setLoading(true); setMessage(''); setMatchesLoaded(false);
    void (async () => {
      try {
        const result = await getPivotMigration(migrationId, controller.signal);
        if (controller.signal.aborted) return;
        setSummary(result);
        if (result.data.run_id && !['queued', 'running'].includes(result.data.run?.status || '')) {
          const matches = await listPivotMatches(migrationId, result.data.run_id, undefined, controller.signal);
          if (controller.signal.aborted) return;
          setRows(matches.data.items); setNext(matches.data.next_cursor || null); setMatchesLoaded(true);
        } else { setRows([]); setNext(null); }
        if (result.next_action === 'poll') timer = setTimeout(() => setRefresh(n => n + 1),
          Math.min(30, Math.max(3, result.retry_after_seconds || 10)) * 1000);
      } catch (e) { if (!controller.signal.aborted) setMessage(e instanceof Error ? e.message : 'Migration could not load. Refresh to retry.'); }
      finally { if (!controller.signal.aborted) setLoading(false); }
    })();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [migrationId, refresh]);
  async function perform(action: (signal: AbortSignal) => Promise<void>) {
    const signal = scope.signal();
    setBusy(true); setMessage('');
    try { await action(signal); }
    catch (e) { if (!signal.aborted) setMessage(e instanceof Error ? e.message : 'The action could not finish. Try again.'); }
    finally { if (!signal.aborted) setBusy(false); }
  }
  async function decide(row: PivotMatch, action: string) {
    await perform(async signal => {
      const decision = { mapping_id: row.mapping_id, expected_revision: row.revision, action,
        ...(action === 'set_target' ? { target_url: targets[row.mapping_id] } : {}) };
      const result = await resolvePivotMatch(migrationId, summary!.data.run_id!, decision,
        scope.key(JSON.stringify(decision)), signal);
      if (signal.aborted) return;
      const outcome = result.data.outcomes?.[0];
      if (!outcome || outcome.code !== 'ok') throw new Error(outcome?.message ||
        `Decision was not saved (${outcome?.code || 'missing result'}). Refresh the mappings and retry.`);
      setRefresh(n => n + 1);
    });
  }
  async function more() {
    await perform(async signal => {
      const result = await listPivotMatches(migrationId, summary!.data.run_id!, next!, signal);
      if (!signal.aborted) { setRows(current => [...current, ...result.data.items]); setNext(result.data.next_cursor || null); }
    });
  }
  async function pay() {
    await perform(async signal => {
      const result = await checkout(migrationId, summary!.data.quote_id!, scope.key(`checkout:${summary!.data.quote_id}`), signal);
      if (signal.aborted) return;
      if (result.data.checkout_url) window.location.assign(result.data.checkout_url);
      else { setMessage(`Checkout status: ${result.data.state || result.status}. Refresh to check payment.`); }
    });
  }
  async function download() {
    await perform(async signal => {
      const artifact = summary!.data.artifact!;
      const result = await downloadArtifact(migrationId, artifact.artifact_id, signal);
      if (signal.aborted) return;
      const url = URL.createObjectURL(new Blob([result.data.content], { type: 'text/plain;charset=utf-8' }));
      const link = document.createElement('a');
      link.href = url; link.download = `redirects-${artifact.artifact_id}.${artifact.format === 'json' ? 'json' : artifact.format === 'csv' ? 'csv' : 'txt'}`;
      link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }
  const data = summary?.data;
  const paymentRequired = summary?.status === 'payment_required' || summary?.next_action === 'complete_payment';
  const monitorId = search.get('monitoring_id') || data?.monitoring_id;
  return <ToolLayout title="Migration detail"><main className="mx-auto max-w-4xl space-y-8 py-4">
    <header className="space-y-3">
      <Link to="/companion" className="text-sm underline">All migrations</Link>
      <h1 className="break-words text-3xl font-semibold">{data?.migration?.name || 'Migration detail'}</h1>
      <p className="break-all text-sm text-muted-foreground">{migrationId}</p>
      <Button variant="outline" disabled={busy || loading} onClick={() => setRefresh(n => n + 1)}>Refresh status</Button>
      {loading && <p role="status">Loading migration status…</p>}
      {message && <p role="alert">{message}</p>}
      {summary && <><p className="font-medium">Status: {summary.status.replaceAll('_', ' ')}</p><p>{data?.summary}</p></>}
    </header>
    {paymentRequired && data?.quote_id && <section className="space-y-3 border-t pt-6" aria-labelledby="payment-heading">
      <h2 id="payment-heading" className="text-lg font-semibold">Payment required</h2>
      {data.quote && <p>{new Intl.NumberFormat('en-US', { style: 'currency', currency: data.quote.currency }).format(data.quote.amount_cents / 100)} for this migration.</p>}
      <p className="text-sm text-muted-foreground">Payment is confirmed by the server. Returning from checkout does not start work by itself.</p>
      <Button disabled={busy || loading} onClick={() => void pay()}>Continue to checkout</Button>
    </section>}
    {data?.run_id && <section className="space-y-3 border-t pt-6" aria-labelledby="exceptions-heading">
      <h2 id="exceptions-heading" className="text-lg font-semibold">Exception review</h2>
      <p className="text-sm text-muted-foreground">Approve only a relevant destination. Leave uncertain decisions for review with your agent.</p>
      {!loading && matchesLoaded && !rows.length && <p className="text-sm">No exceptions on this page.</p>}
      <ul className="divide-y">{rows.map(row => <li key={row.mapping_id} className="space-y-3 py-4">
        <p className="break-all text-sm font-medium">{row.old_url} → {row.decision_target || row.new_url || 'No destination'}</p>
        <p className="text-sm text-muted-foreground">{row.review_status.replaceAll('_', ' ')}. {row.traffic_observed ? `${row.traffic_clicks ?? 0} observed search clicks` : 'Search traffic not measured'}</p>
        <div className="flex flex-wrap gap-2">
          <Button disabled={busy || loading} onClick={() => void decide(row, 'approve')}>Approve</Button>
          <Button variant="outline" disabled={busy || loading} onClick={() => void decide(row, 'reject')}>Reject</Button>
          <Button variant="outline" disabled={busy || loading} onClick={() => void decide(row, 'defer')}>Defer</Button>
        </div>
        <label className="block space-y-2 text-sm">Destination for {row.old_url}
          <Input type="url" maxLength={8192} value={targets[row.mapping_id] || ''}
            onChange={e => setTargets(current => ({ ...current, [row.mapping_id]: e.target.value }))} />
        </label>
        <Button variant="outline" disabled={busy || loading || !targets[row.mapping_id]} onClick={() => void decide(row, 'set_target')}>Set destination</Button>
      </li>)}</ul>
      {next && <Button variant="outline" disabled={busy || loading} onClick={() => void more()}>Load more exceptions</Button>}
    </section>}
    {data?.artifact && <section className="space-y-3 border-t pt-6">
      <h2 className="text-lg font-semibold">Redirect artifact</h2>
      <p className="text-sm">{data.artifact.included_count} redirects included; {data.artifact.excluded_count} excluded. Format: {data.artifact.format}.</p>
      <Button disabled={busy} onClick={() => void download()}>Download redirects</Button>
      <p className="text-sm text-muted-foreground">Ask your agent to install the artifact and confirm deployment before verification.</p>
      {data.verification && <p>Verification: {data.verification.outcome}. {data.verification.checked} of {data.verification.total} checked.</p>}
    </section>}
    <MonitoringPanel key={`${migrationId}:${monitorId || ''}`} migrationId={migrationId} monitoringId={monitorId} />
    <SearchConsolePanel key={migrationId} migrationId={migrationId} />
    <SubscriptionCheckoutPanel deploymentId={data?.deployment_id} />
  </main></ToolLayout>;
}
