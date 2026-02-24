import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { DashboardLayout } from './DashboardLayout';
import { Card, CardContent, CardHeader, CardTitle, CardAction } from './ui/card';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Badge } from './ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './ui/table';
import { ArrowLeft, Download, Loader2, RefreshCw } from 'lucide-react';
import { getOnboardingReport, type OnboardingReportResponse } from '../api/trials';
import { toast } from 'sonner';

function formatDateTime(value?: string | null): string {
  if (!value) return '-';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '-';
  return parsed.toLocaleString();
}

function statusBadgeClass(status: string): string {
  if (status === 'completed') return 'bg-green-500/10 text-green-600 border-green-500/30';
  if (status === 'in_progress') return 'bg-yellow-500/10 text-yellow-600 border-yellow-500/30';
  if (status === 'dismissed') return 'bg-gray-500/10 text-gray-600 border-gray-500/30';
  return 'bg-blue-500/10 text-blue-600 border-blue-500/30';
}

export function AdminOnboardingReport() {
  const navigate = useNavigate();
  const [stuckHours, setStuckHours] = useState(24);
  const [limit, setLimit] = useState(100);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<OnboardingReportResponse | null>(null);

  async function loadReport(nextHours = stuckHours, nextLimit = limit) {
    setLoading(true);
    setError(null);

    try {
      const data = await getOnboardingReport({
        stuck_hours: nextHours,
        limit: nextLimit,
      });
      setReport(data);
    } catch (err: any) {
      const message = err?.message || 'Failed to load onboarding report';
      setError(message);
      toast.error(message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadReport();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function exportJson() {
    if (!report) return;
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `onboarding-report-${new Date().toISOString().slice(0, 10)}.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  const statusCounts = report?.summary.status_counts || {};
  const funnelCounts = report?.summary.funnel_counts || {};

  return (
    <DashboardLayout title="Onboarding Report">
      <Card className="mb-6">
        <CardHeader className="border-b border-border">
          <CardTitle>Query Controls</CardTitle>
          <CardAction className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => navigate('/admin/trials')}>
              <ArrowLeft className="h-4 w-4 mr-1" />
              Back to Admin
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={exportJson}
              disabled={!report}
            >
              <Download className="h-4 w-4 mr-1" />
              Export JSON
            </Button>
          </CardAction>
        </CardHeader>
        <CardContent className="pt-6">
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1">
                Stuck Threshold (hours)
              </label>
              <Input
                type="number"
                min={1}
                max={720}
                value={stuckHours}
                onChange={(e) => setStuckHours(Math.max(1, Math.min(720, Number(e.target.value) || 24)))}
                className="w-[180px]"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1">
                Max Rows
              </label>
              <Input
                type="number"
                min={1}
                max={500}
                value={limit}
                onChange={(e) => setLimit(Math.max(1, Math.min(500, Number(e.target.value) || 100)))}
                className="w-[140px]"
              />
            </div>
            <Button onClick={() => loadReport()} disabled={loading}>
              {loading ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <RefreshCw className="h-4 w-4 mr-2" />}
              Run Query
            </Button>
          </div>
          {report && (
            <p className="text-xs text-muted-foreground mt-3">
              Generated: {formatDateTime(report.generated_at)} | Cutoff: {formatDateTime(report.filters.stuck_before_utc)}
            </p>
          )}
        </CardContent>
      </Card>

      {error && (
        <Card className="mb-6 border-destructive/40">
          <CardContent className="py-4 text-sm text-destructive">{error}</CardContent>
        </Card>
      )}

      {loading ? (
        <div className="py-16 flex items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      ) : report ? (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
            <Card className="p-5">
              <div className="text-sm text-muted-foreground mb-1">Total Users</div>
              <div className="text-2xl font-semibold text-foreground">{report.summary.total_users}</div>
            </Card>
            <Card className="p-5">
              <div className="text-sm text-muted-foreground mb-1">In Progress</div>
              <div className="text-2xl font-semibold text-foreground">{statusCounts.in_progress || 0}</div>
            </Card>
            <Card className="p-5">
              <div className="text-sm text-muted-foreground mb-1">Completed</div>
              <div className="text-2xl font-semibold text-foreground">{statusCounts.completed || 0}</div>
            </Card>
            <Card className="p-5">
              <div className="text-sm text-muted-foreground mb-1">Stuck</div>
              <div className="text-2xl font-semibold text-destructive">{report.summary.stuck_in_progress}</div>
            </Card>
          </div>

          <Card className="mb-6">
            <CardHeader className="border-b border-border">
              <CardTitle>Funnel Snapshot</CardTitle>
            </CardHeader>
            <CardContent className="pt-5">
              <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 text-sm">
                <div className="rounded-md border border-border p-3">
                  <div className="text-muted-foreground">Started</div>
                  <div className="font-semibold text-lg">{funnelCounts.started || 0}</div>
                </div>
                <div className="rounded-md border border-border p-3">
                  <div className="text-muted-foreground">Path Selected</div>
                  <div className="font-semibold text-lg">{funnelCounts.path_selected || 0}</div>
                </div>
                <div className="rounded-md border border-border p-3">
                  <div className="text-muted-foreground">Mappings Generated</div>
                  <div className="font-semibold text-lg">{funnelCounts.mapping_generated || 0}</div>
                </div>
                <div className="rounded-md border border-border p-3">
                  <div className="text-muted-foreground">Review Opened</div>
                  <div className="font-semibold text-lg">{funnelCounts.review_opened || 0}</div>
                </div>
                <div className="rounded-md border border-border p-3">
                  <div className="text-muted-foreground">Export Downloaded</div>
                  <div className="font-semibold text-lg">{funnelCounts.export_downloaded || 0}</div>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="border-b border-border">
              <CardTitle>
                Stuck In-Progress Users ({report.returned_rows}/{report.total_stuck_users})
              </CardTitle>
            </CardHeader>
            <CardContent className="pt-0">
              {report.stuck_users.length === 0 ? (
                <div className="py-8 text-center text-muted-foreground">
                  No stuck users for this threshold.
                </div>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>User</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Path</TableHead>
                      <TableHead>Hours Idle</TableHead>
                      <TableHead>Hours In Flow</TableHead>
                      <TableHead>Non-Tutorial Sessions</TableHead>
                      <TableHead>Started</TableHead>
                      <TableHead>Last Seen</TableHead>
                      <TableHead>Completed Steps</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {report.stuck_users.map((row) => (
                      <TableRow key={row.id}>
                        <TableCell>
                          <div className="font-medium">{row.full_name || row.email || row.id}</div>
                          <div className="text-xs text-muted-foreground">{row.email || '-'}</div>
                        </TableCell>
                        <TableCell>
                          <Badge variant="outline" className={statusBadgeClass(row.onboarding_status)}>
                            {row.onboarding_status}
                          </Badge>
                        </TableCell>
                        <TableCell>{row.path || '-'}</TableCell>
                        <TableCell>{row.hours_since_last_activity ?? '-'}</TableCell>
                        <TableCell>{row.hours_in_progress ?? '-'}</TableCell>
                        <TableCell>{row.non_tutorial_sessions}</TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {formatDateTime(row.onboarding_started_at)}
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {formatDateTime(row.onboarding_last_seen_at)}
                        </TableCell>
                        <TableCell className="text-xs">
                          {row.completed_steps.length > 0 ? row.completed_steps.join(', ') : '-'}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </>
      ) : null}
    </DashboardLayout>
  );
}
