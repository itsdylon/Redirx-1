import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ArrowRight, ExternalLink, RefreshCw, ShieldCheck } from 'lucide-react';
import { listPivotMigrations, type PivotMigration } from '../api/pivot';
import { ToolLayout } from './ToolLayout';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { SubscriptionCheckoutPanel } from './SubscriptionCheckoutPanel';

export function PivotCompanionPage() {
  const [search] = useSearchParams(); const navigate = useNavigate();
  const { migrationId } = useParams();
  const [items, setItems] = useState<PivotMigration[]>([]);
  const [state, setState] = useState<'loading' | 'ready' | 'unavailable'>('loading');
  const load = async () => { setState('loading'); try { const result = await listPivotMigrations(); setItems(Array.isArray(result.data.items) ? result.data.items : []); setState('ready'); } catch { setState('unavailable'); } };
  useEffect(() => { void load(); }, []);
  return <ToolLayout title="Migration companion"><div className="mx-auto max-w-4xl space-y-8">
    <section className="flex flex-col gap-5 border-b border-border pb-8 sm:flex-row sm:items-end sm:justify-between"><div className="max-w-2xl"><p className="text-sm font-medium text-primary">MCP connection &amp; account</p><h1 className="mt-2 text-3xl font-semibold tracking-tight text-foreground">Keep a migration moving, without leaving the thread.</h1><p className="mt-3 text-muted-foreground">Review exceptions, complete an approved handoff, and return to the same durable migration.</p></div><Button onClick={() => navigate('/api-keys')}>Connect MCP <ArrowRight className="size-4" /></Button></section>
    {(search.get('resume') || search.get('monitoring_id')) && <Card className="border-primary/30 bg-primary/5 p-4"><div className="flex gap-3"><ShieldCheck className="mt-0.5 size-5 text-primary" /><div><p className="font-medium">Return received</p><p className="text-sm text-muted-foreground">Refresh the migration below to resume its server-recorded next action{search.get('monitoring_id') ? ', including monitoring details' : ''}.</p></div></div></Card>}
    {migrationId && <p className="text-sm text-muted-foreground">Migration {migrationId}</p>}
    <SubscriptionCheckoutPanel />
    <section className="grid gap-4 md:grid-cols-2"><Card className="p-5"><h2 className="font-semibold">Search Console</h2><p className="mt-2 text-sm text-muted-foreground">Connect in Google&rsquo;s consent screen, then return here. Credentials are never entered into RedirX.</p><Button className="mt-4" variant="outline" onClick={() => navigate('/quick-match')}>Continue to connection <ExternalLink className="size-4" /></Button></Card><Card className="p-5"><h2 className="font-semibold">Monitoring</h2><p className="mt-2 text-sm text-muted-foreground">Coverage and redirect issues appear after a deployed artifact is acknowledged.</p><Button className="mt-4" variant="outline" disabled>Choose a migration first</Button></Card></section>
    <section aria-labelledby="history-heading"><div className="mb-3 flex items-center justify-between"><div><h2 id="history-heading" className="text-lg font-semibold">Migration history</h2><p className="text-sm text-muted-foreground">Only migrations returned by your signed-in account are shown.</p></div><Button variant="ghost" size="sm" onClick={() => void load()} disabled={state === 'loading'}><RefreshCw className="size-4" /> Refresh</Button></div>
      {state === 'loading' && <Card className="p-5 text-sm text-muted-foreground">Loading your migration history…</Card>}
      {state === 'unavailable' && <Card className="p-5"><p className="font-medium">Migration companion is waiting for its browser bridge.</p><p className="mt-1 text-sm text-muted-foreground">No migration data was loaded. Existing Quick Match links remain available.</p></Card>}
      {state === 'ready' && (items.length ? <div className="divide-y rounded-xl border border-border">{items.map(item => <Link key={item.id} to={`/migrations/${item.id}`} className="flex items-center justify-between gap-4 p-4 hover:bg-muted/50"><div className="min-w-0"><p className="truncate font-medium">{item.name || item.old_origin || 'Untitled migration'}</p><p className="truncate text-sm text-muted-foreground">{item.old_origin && item.new_origin ? `${item.old_origin} → ${item.new_origin}` : item.id}</p></div><span className="shrink-0 text-sm text-muted-foreground">{item.next_action || item.status || 'Open'}</span></Link>)}</div> : <Card className="p-5 text-sm text-muted-foreground">No v2 migrations yet. Start one from Quick Match or your MCP client.</Card>)}</section>
  </div></ToolLayout>;
}
