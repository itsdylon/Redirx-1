import { useState, useEffect, useRef, useMemo } from 'react';
import { DashboardLayout } from './DashboardLayout';
import { Card, CardHeader, CardTitle, CardAction, CardContent } from './ui/card';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Badge } from './ui/badge';
import { Separator } from './ui/separator';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from './ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from './ui/table';
import { Tabs, TabsList, TabsTrigger } from './ui/tabs';
import {
  Plus,
  RefreshCw,
  Copy,
  Download,
  Upload,
  ShieldAlert,
  Loader2,
  X,
  Ticket,
  Users,
  CheckCircle2,
  Send,
  Megaphone,
  ClipboardList,
  Check,
  XCircle,
} from 'lucide-react';
import {
  listCampaigns,
  createCampaign,
  generateInvites,
  generateInvitesWithCsv,
  listInvites,
  revokeInvite,
  markSent,
  listWaitlist,
  approveWaitlistEntry,
  rejectWaitlistEntry,
  type Campaign,
  type Invite,
  type GeneratedInvite,
  type WaitlistEntry,
} from '../api/trials';
import { toast } from 'sonner';

const STATUS_COLORS: Record<string, string> = {
  created: 'bg-blue-500/10 text-blue-600 border-blue-500/30',
  sent: 'bg-yellow-500/10 text-yellow-600 border-yellow-500/30',
  pending_payment: 'bg-orange-500/10 text-orange-600 border-orange-500/30',
  redeemed: 'bg-green-500/10 text-green-600 border-green-500/30',
  expired: 'bg-gray-500/10 text-gray-500 border-gray-500/30',
  revoked: 'bg-red-500/10 text-red-600 border-red-500/30',
};

export function AdminTrials() {
  // Access check
  const [accessDenied, setAccessDenied] = useState(false);
  const [loading, setLoading] = useState(true);

  // Campaigns
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [showCreateCampaign, setShowCreateCampaign] = useState(false);
  const [campaignForm, setCampaignForm] = useState({ name: '', slug: '', channel: '', template_version: '', invite_type: 'trial' as 'trial' | 'founder' });
  const [creatingCampaign, setCreatingCampaign] = useState(false);

  // Invites
  const [invites, setInvites] = useState<Invite[]>([]);
  const [inviteFilter, setInviteFilter] = useState<{ campaign_id?: string; status?: string }>({});
  const [loadingInvites, setLoadingInvites] = useState(false);

  // Generate dialog
  const [showGenerate, setShowGenerate] = useState(false);
  const [generateMode, setGenerateMode] = useState<'count' | 'csv'>('count');
  const [genCampaignId, setGenCampaignId] = useState('');
  const [genCount, setGenCount] = useState(5);
  const [genCredits, setGenCredits] = useState(50000);
  const [genTrialDays, setGenTrialDays] = useState(14);
  const [genExpiresDays, setGenExpiresDays] = useState(90);
  const [genCsvFile, setGenCsvFile] = useState<File | null>(null);
  const [generating, setGenerating] = useState(false);

  // Generated codes display
  const [generatedCodes, setGeneratedCodes] = useState<GeneratedInvite[]>([]);
  const [generatedCampaignSlug, setGeneratedCampaignSlug] = useState('');
  const [generatedCampaignType, setGeneratedCampaignType] = useState<'trial' | 'founder'>('trial');
  const [showCodes, setShowCodes] = useState(false);

  // Revoke confirmation
  const [revokeTarget, setRevokeTarget] = useState<string | null>(null);
  const [revoking, setRevoking] = useState(false);

  // Waitlist
  const [waitlistEntries, setWaitlistEntries] = useState<WaitlistEntry[]>([]);
  const [waitlistFilter, setWaitlistFilter] = useState<string>('pending');
  const [loadingWaitlist, setLoadingWaitlist] = useState(false);

  // Approve dialog
  const [approveTarget, setApproveTarget] = useState<WaitlistEntry | null>(null);
  const [approveCampaignId, setApproveCampaignId] = useState('');
  const [approving, setApproving] = useState(false);
  const [approvedCode, setApprovedCode] = useState('');

  // Reject dialog
  const [rejectTarget, setRejectTarget] = useState<WaitlistEntry | null>(null);
  const [rejectNotes, setRejectNotes] = useState('');
  const [rejecting, setRejecting] = useState(false);

  const csvInputRef = useRef<HTMLInputElement>(null);

  // ======================================================================
  // Derived stats
  // ======================================================================
  const overviewStats = useMemo(() => {
    const totalInvites = campaigns.reduce((sum, c) => sum + (c.stats?.total || 0), 0);
    const totalRedeemed = campaigns.reduce((sum, c) => sum + (c.stats?.redeemed || 0), 0);
    const totalSent = campaigns.reduce((sum, c) => sum + (c.stats?.sent || 0), 0);
    const totalPending = campaigns.reduce((sum, c) => sum + (c.stats?.created || 0), 0);
    const conversionRate = totalSent + totalRedeemed > 0
      ? Math.round((totalRedeemed / (totalSent + totalRedeemed)) * 100)
      : 0;
    return { totalInvites, totalRedeemed, totalSent, totalPending, conversionRate };
  }, [campaigns]);

  // ======================================================================
  // Data loading
  // ======================================================================
  useEffect(() => {
    loadCampaigns();
    loadWaitlist();
  }, []);

  useEffect(() => {
    loadInvites();
  }, [inviteFilter]);

  useEffect(() => {
    loadWaitlist();
  }, [waitlistFilter]);

  async function loadCampaigns() {
    setLoading(true);
    try {
      const data = await listCampaigns();
      setCampaigns(data);
      setAccessDenied(false);
    } catch (err: any) {
      if (err.message?.includes('Admin access required') || err.message?.includes('403')) {
        setAccessDenied(true);
      }
    } finally {
      setLoading(false);
    }
  }

  async function loadInvites() {
    setLoadingInvites(true);
    try {
      const data = await listInvites({
        campaign_id: inviteFilter.campaign_id,
        status: inviteFilter.status,
      });
      setInvites(data);
    } catch {
      // Silently fail if access denied
    } finally {
      setLoadingInvites(false);
    }
  }

  async function loadWaitlist() {
    setLoadingWaitlist(true);
    try {
      const status = waitlistFilter === 'all' ? undefined : waitlistFilter;
      const data = await listWaitlist({ status });
      setWaitlistEntries(data);
    } catch {
      // Silently fail if access denied
    } finally {
      setLoadingWaitlist(false);
    }
  }

  async function refreshAll() {
    await Promise.all([loadCampaigns(), loadInvites(), loadWaitlist()]);
    toast.success('Data refreshed');
  }

  // ======================================================================
  // Handlers
  // ======================================================================
  async function handleCreateCampaign() {
    if (!campaignForm.name || !campaignForm.slug) return;
    setCreatingCampaign(true);
    try {
      await createCampaign({
        name: campaignForm.name,
        slug: campaignForm.slug,
        channel: campaignForm.channel || undefined,
        template_version: campaignForm.template_version || undefined,
        invite_type: campaignForm.invite_type,
      });
      setShowCreateCampaign(false);
      setCampaignForm({ name: '', slug: '', channel: '', template_version: '', invite_type: 'trial' });
      await loadCampaigns();
      toast.success('Campaign created');
    } catch (err: any) {
      toast.error(err.message);
    } finally {
      setCreatingCampaign(false);
    }
  }

  async function handleGenerate() {
    if (!genCampaignId) return;
    setGenerating(true);
    try {
      const campaign = campaigns.find((c) => c.id === genCampaignId);
      const campType = (campaign?.invite_type || 'trial') as 'trial' | 'founder';
      let codes: GeneratedInvite[];
      if (generateMode === 'csv' && genCsvFile) {
        codes = await generateInvitesWithCsv(genCampaignId, genCsvFile, {
          credits_granted: genCredits,
          trial_days: genTrialDays,
          expires_days: genExpiresDays,
        });
      } else {
        codes = await generateInvites({
          campaign_id: genCampaignId,
          count: genCount,
          credits_granted: genCredits,
          trial_days: genTrialDays,
          expires_days: genExpiresDays,
          invite_type: campType,
        });
      }
      setGeneratedCampaignSlug(campaign?.slug || '');
      setGeneratedCampaignType(campType);
      setGeneratedCodes(codes);
      setShowGenerate(false);
      setShowCodes(true);
      await loadCampaigns();
      await loadInvites();
    } catch (err: any) {
      toast.error(err.message);
    } finally {
      setGenerating(false);
    }
  }

  async function handleRevoke() {
    if (!revokeTarget) return;
    setRevoking(true);
    try {
      await revokeInvite(revokeTarget);
      setRevokeTarget(null);
      await loadInvites();
      toast.success('Invite revoked');
    } catch (err: any) {
      toast.error(err.message);
    } finally {
      setRevoking(false);
    }
  }

  async function handleApproveWaitlist() {
    if (!approveTarget || !approveCampaignId) return;
    setApproving(true);
    try {
      const result = await approveWaitlistEntry({
        waitlist_id: approveTarget.id,
        campaign_id: approveCampaignId,
      });
      setApprovedCode(result.raw_code);
      toast.success('Request approved — invite code generated');
      await loadWaitlist();
    } catch (err: any) {
      toast.error(err.message);
    } finally {
      setApproving(false);
    }
  }

  async function handleRejectWaitlist() {
    if (!rejectTarget) return;
    setRejecting(true);
    try {
      await rejectWaitlistEntry({
        waitlist_id: rejectTarget.id,
        admin_notes: rejectNotes || undefined,
      });
      setRejectTarget(null);
      setRejectNotes('');
      toast.success('Request rejected');
      await loadWaitlist();
    } catch (err: any) {
      toast.error(err.message);
    } finally {
      setRejecting(false);
    }
  }

  function _buildInviteUrl(rawCode: string, slug: string, type: 'trial' | 'founder') {
    const origin = window.location.origin;
    const path = type === 'founder' ? '/founder' : '/trial';
    return `${origin}${path}?code=${encodeURIComponent(rawCode)}&cid=${encodeURIComponent(slug)}`;
  }

  function handleCopyAll() {
    const text = generatedCodes
      .map((c) => _buildInviteUrl(c.raw_code, generatedCampaignSlug, generatedCampaignType))
      .join('\n');
    navigator.clipboard.writeText(text);
    toast.success(`${generatedCodes.length} invite links copied to clipboard`);
  }

  function handleCopyInviteLink(code: string) {
    const url = _buildInviteUrl(code, generatedCampaignSlug, generatedCampaignType);
    navigator.clipboard.writeText(url);
    toast.success('Invite link copied');
  }

  function handleExportCsv() {
    const slug = generatedCampaignSlug;

    const header = 'code,recipient_email,redeem_url,campaign_slug,created_at';
    const rows = generatedCodes.map((c) => {
      const redeemUrl = _buildInviteUrl(c.raw_code, slug, generatedCampaignType);
      return `${c.raw_code},${c.recipient_email || ''},${redeemUrl},${slug},${c.created_at}`;
    });

    const csvContent = [header, ...rows].join('\n');
    const blob = new Blob([csvContent], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `invites-${slug}-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
    toast.success('CSV exported');
  }

  function autoSlug(name: string) {
    return name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  }

  // ======================================================================
  // Access denied state
  // ======================================================================
  if (accessDenied) {
    return (
      <DashboardLayout title="Admin">
        <div className="flex-1 flex items-center justify-center">
          <Card className="p-8 text-center max-w-md">
            <ShieldAlert className="h-12 w-12 mx-auto text-destructive mb-4" />
            <h1 className="text-xl font-bold text-foreground mb-2">Admin Access Required</h1>
            <p className="text-muted-foreground">
              You do not have permission to access this page.
            </p>
          </Card>
        </div>
      </DashboardLayout>
    );
  }

  // ======================================================================
  // Loading state
  // ======================================================================
  if (loading) {
    return (
      <DashboardLayout title="Admin">
        <div className="flex-1 flex items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      </DashboardLayout>
    );
  }

  // ======================================================================
  // Main dashboard
  // ======================================================================
  return (
    <DashboardLayout title="Trial Invite Admin">
      {/* Overview Stats */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4 mb-6">
        <Card className="p-5">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm text-muted-foreground">Campaigns</span>
            <Megaphone className="h-4 w-4 text-muted-foreground" />
          </div>
          <div className="text-2xl font-semibold text-foreground">{campaigns.length}</div>
        </Card>
        <Card className="p-5">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm text-muted-foreground">Total Codes</span>
            <Ticket className="h-4 w-4 text-muted-foreground" />
          </div>
          <div className="text-2xl font-semibold text-foreground">{overviewStats.totalInvites}</div>
        </Card>
        <Card className="p-5">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm text-muted-foreground">Sent</span>
            <Send className="h-4 w-4 text-muted-foreground" />
          </div>
          <div className="text-2xl font-semibold text-foreground">{overviewStats.totalSent}</div>
        </Card>
        <Card className="p-5">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm text-muted-foreground">Redeemed</span>
            <CheckCircle2 className="h-4 w-4 text-green-500" />
          </div>
          <div className="text-2xl font-semibold text-foreground">{overviewStats.totalRedeemed}</div>
        </Card>
        <Card className="p-5">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm text-muted-foreground">Conversion</span>
            <Users className="h-4 w-4 text-muted-foreground" />
          </div>
          <div className="text-2xl font-semibold text-foreground">{overviewStats.conversionRate}%</div>
        </Card>
      </div>

      {/* Campaigns Section */}
      <Card className="mb-6">
        <CardHeader className="border-b border-border">
          <CardTitle className="flex items-center gap-2">
            <Megaphone className="h-5 w-5" />
            Campaigns
          </CardTitle>
          <CardAction className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={refreshAll}>
              <RefreshCw className="h-4 w-4" />
            </Button>
            <Button size="sm" onClick={() => setShowCreateCampaign(true)}>
              <Plus className="h-4 w-4 mr-1" />
              New Campaign
            </Button>
          </CardAction>
        </CardHeader>
        <CardContent className="pt-0">
          {campaigns.length === 0 ? (
            <div className="py-8 text-center text-muted-foreground">
              No campaigns yet. Create one to get started.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Slug</TableHead>
                  <TableHead>Channel</TableHead>
                  <TableHead className="text-center">Created</TableHead>
                  <TableHead className="text-center">Sent</TableHead>
                  <TableHead className="text-center">Redeemed</TableHead>
                  <TableHead className="text-center">Total</TableHead>
                  <TableHead>Date</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {campaigns.map((c) => (
                  <TableRow key={c.id}>
                    <TableCell className="font-medium">{c.name}</TableCell>
                    <TableCell>
                      <Badge
                        variant="outline"
                        className={c.invite_type === 'founder'
                          ? 'bg-yellow-100 text-yellow-800 border-yellow-300'
                          : 'bg-blue-50 text-blue-500 border-blue-500'}
                      >
                        {c.invite_type === 'founder' ? 'Founder' : 'Trial'}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{c.slug}</code>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{c.channel || '-'}</TableCell>
                    <TableCell className="text-center">{c.stats?.created || 0}</TableCell>
                    <TableCell className="text-center">{c.stats?.sent || 0}</TableCell>
                    <TableCell className="text-center">
                      <span className="text-green-600 font-medium">{c.stats?.redeemed || 0}</span>
                    </TableCell>
                    <TableCell className="text-center">{c.stats?.total || 0}</TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {new Date(c.created_at).toLocaleDateString()}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Generate Invites Quick Action */}
      <Card className="mb-6">
        <CardContent className="py-6">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="font-medium text-foreground">Generate Invite Codes</h3>
              <p className="text-sm text-muted-foreground mt-1">
                Create codes by count or upload a CSV of recipient emails. Codes are shown once.
              </p>
            </div>
            <Button
              onClick={() => setShowGenerate(true)}
              disabled={campaigns.length === 0}
            >
              <Ticket className="h-4 w-4 mr-2" />
              Generate Codes
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Invites Table Section */}
      <Card>
        <CardHeader className="border-b border-border">
          <CardTitle className="flex items-center gap-2">
            <Ticket className="h-5 w-5" />
            Invite Codes
          </CardTitle>
          <CardAction className="flex items-center gap-2">
            <Select
              value={inviteFilter.campaign_id || 'all'}
              onValueChange={(v) =>
                setInviteFilter((f) => ({ ...f, campaign_id: v === 'all' ? undefined : v }))
              }
            >
              <SelectTrigger className="w-[180px]">
                <SelectValue placeholder="All campaigns" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All campaigns</SelectItem>
                {campaigns.map((c) => (
                  <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button variant="outline" size="sm" onClick={loadInvites}>
              <RefreshCw className="h-4 w-4" />
            </Button>
          </CardAction>
        </CardHeader>
        <CardContent className="pt-0">
          {/* Status filter tabs */}
          <div className="py-3">
            <Tabs
              value={inviteFilter.status || 'all'}
              onValueChange={(v) =>
                setInviteFilter((f) => ({ ...f, status: v === 'all' ? undefined : v }))
              }
            >
              <TabsList>
                <TabsTrigger value="all">All</TabsTrigger>
                <TabsTrigger value="created">Created</TabsTrigger>
                <TabsTrigger value="sent">Sent</TabsTrigger>
                <TabsTrigger value="redeemed">Redeemed</TabsTrigger>
                <TabsTrigger value="revoked">Revoked</TabsTrigger>
              </TabsList>
            </Tabs>
          </div>

          {loadingInvites ? (
            <div className="py-8 text-center">
              <Loader2 className="h-6 w-6 animate-spin mx-auto text-muted-foreground" />
            </div>
          ) : invites.length === 0 ? (
            <div className="py-8 text-center text-muted-foreground">
              No invites found.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Code Prefix</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Recipient</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Campaign</TableHead>
                  <TableHead>Credits</TableHead>
                  <TableHead>Trial Days</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead>Redeemed By</TableHead>
                  <TableHead>Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {invites.map((inv) => {
                  const invType = inv.invite_type || inv.trial_campaigns?.invite_type || 'trial';
                  return (
                  <TableRow key={inv.id}>
                    <TableCell>
                      <code className="text-xs bg-muted px-1.5 py-0.5 rounded">
                        rx_{inv.code_prefix}...
                      </code>
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant="outline"
                        className={invType === 'founder'
                          ? 'bg-yellow-100 text-yellow-800 border-yellow-300'
                          : 'bg-blue-50 text-blue-500 border-blue-500'}
                      >
                        {invType === 'founder' ? 'Founder' : 'Trial'}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-sm">{inv.recipient_email || '-'}</TableCell>
                    <TableCell>
                      <Badge
                        variant="outline"
                        className={STATUS_COLORS[inv.status] || ''}
                      >
                        {inv.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {inv.trial_campaigns?.name || '-'}
                    </TableCell>
                    <TableCell className="text-sm">
                      {(inv.credits_granted || 0).toLocaleString()}
                    </TableCell>
                    <TableCell className="text-sm">{inv.trial_days}d</TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {new Date(inv.created_at).toLocaleDateString()}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {inv.redeemed_by_user_id
                        ? inv.redeemed_by_user_id.slice(0, 8) + '...'
                        : '-'}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1">
                        {inv.code && ['created', 'sent', 'pending_payment'].includes(inv.status) && (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => {
                              const slug = inv.trial_campaigns?.slug || '';
                              const path = invType === 'founder' ? '/founder' : '/trial';
                              const url = `${window.location.origin}${path}?code=${encodeURIComponent(inv.code!)}&cid=${encodeURIComponent(slug)}`;
                              navigator.clipboard.writeText(url);
                              toast.success('Invite link copied');
                            }}
                          >
                            <Copy className="h-3.5 w-3.5 mr-1" />
                            Copy Link
                          </Button>
                        )}
                        {inv.status === 'created' && (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={async () => {
                              try {
                                await markSent([inv.id]);
                                toast.success('Marked as sent');
                                await loadInvites();
                              } catch (err: any) {
                                toast.error(err.message);
                              }
                            }}
                          >
                            <Send className="h-3.5 w-3.5 mr-1" />
                            Sent
                          </Button>
                        )}
                        {['created', 'sent'].includes(inv.status) && (
                          <Button
                            variant="outline"
                            size="sm"
                            className="text-destructive"
                            onClick={() => setRevokeTarget(inv.id)}
                          >
                            Revoke
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Founder Waitlist Section */}
      <Card className="mt-6">
        <CardHeader className="border-b border-border">
          <CardTitle className="flex items-center gap-2">
            <ClipboardList className="h-5 w-5" />
            Founder Waitlist
          </CardTitle>
          <CardAction>
            <Button variant="outline" size="sm" onClick={loadWaitlist}>
              <RefreshCw className="h-4 w-4" />
            </Button>
          </CardAction>
        </CardHeader>
        <CardContent className="pt-0">
          {/* Status filter tabs */}
          <div className="py-3">
            <Tabs
              value={waitlistFilter}
              onValueChange={setWaitlistFilter}
            >
              <TabsList>
                <TabsTrigger value="pending">Pending</TabsTrigger>
                <TabsTrigger value="approved">Approved</TabsTrigger>
                <TabsTrigger value="rejected">Rejected</TabsTrigger>
                <TabsTrigger value="all">All</TabsTrigger>
              </TabsList>
            </Tabs>
          </div>

          {loadingWaitlist ? (
            <div className="py-8 text-center">
              <Loader2 className="h-6 w-6 animate-spin mx-auto text-muted-foreground" />
            </div>
          ) : waitlistEntries.length === 0 ? (
            <div className="py-8 text-center text-muted-foreground">
              No waitlist entries found.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Email</TableHead>
                  <TableHead>Company</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Requested</TableHead>
                  <TableHead>Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {waitlistEntries.map((entry) => (
                  <TableRow key={entry.id}>
                    <TableCell className="font-medium">{entry.name}</TableCell>
                    <TableCell className="text-sm">{entry.email}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {entry.company || '-'}
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant="outline"
                        className={
                          entry.status === 'pending'
                            ? 'bg-yellow-500/10 text-yellow-600 border-yellow-500/30'
                            : entry.status === 'approved'
                            ? 'bg-green-500/10 text-green-600 border-green-500/30'
                            : 'bg-red-500/10 text-red-600 border-red-500/30'
                        }
                      >
                        {entry.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {new Date(entry.created_at).toLocaleDateString()}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1">
                        {entry.status === 'pending' && (
                          <>
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => {
                                setApproveTarget(entry);
                                setApproveCampaignId('');
                                setApprovedCode('');
                              }}
                            >
                              <Check className="h-3.5 w-3.5 mr-1" />
                              Approve
                            </Button>
                            <Button
                              variant="outline"
                              size="sm"
                              className="text-destructive"
                              onClick={() => {
                                setRejectTarget(entry);
                                setRejectNotes('');
                              }}
                            >
                              <XCircle className="h-3.5 w-3.5 mr-1" />
                              Reject
                            </Button>
                          </>
                        )}
                        {entry.status === 'approved' && entry.trial_invites?.code && (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => {
                              const url = `${window.location.origin}/founder?code=${encodeURIComponent(entry.trial_invites!.code!)}`;
                              navigator.clipboard.writeText(url);
                              toast.success('Invite link copied');
                            }}
                          >
                            <Copy className="h-3.5 w-3.5 mr-1" />
                            Copy Link
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* ================================================================ */}
      {/* Approve Waitlist Dialog */}
      {/* ================================================================ */}
      <Dialog
        open={!!approveTarget}
        onOpenChange={(open) => {
          if (!open) {
            setApproveTarget(null);
            setApprovedCode('');
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Approve Waitlist Request</DialogTitle>
          </DialogHeader>
          {approvedCode ? (
            <div className="space-y-4 py-2">
              <div className="rounded bg-green-50 border border-green-300 p-4 text-center">
                <CheckCircle2 className="h-8 w-8 text-green-600 mx-auto mb-2" />
                <p className="text-sm font-medium text-green-800 mb-3">
                  Invite code generated for {approveTarget?.email}
                </p>
                <div className="flex items-center gap-2 justify-center">
                  <code className="text-xs bg-white px-3 py-1.5 rounded border break-all">
                    {`${window.location.origin}/founder?code=${encodeURIComponent(approvedCode)}`}
                  </code>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      const url = `${window.location.origin}/founder?code=${encodeURIComponent(approvedCode)}`;
                      navigator.clipboard.writeText(url);
                      toast.success('Invite link copied');
                    }}
                  >
                    <Copy className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
              <DialogFooter>
                <Button onClick={() => { setApproveTarget(null); setApprovedCode(''); }}>
                  Done
                </Button>
              </DialogFooter>
            </div>
          ) : (
            <div className="space-y-4 py-2">
              <p className="text-sm text-muted-foreground">
                Approving <span className="font-medium text-foreground">{approveTarget?.name}</span> ({approveTarget?.email}).
                Select a founder campaign to generate an invite code.
              </p>
              <div>
                <label className="block text-sm font-medium text-foreground mb-1">
                  Founder Campaign
                </label>
                <Select value={approveCampaignId} onValueChange={setApproveCampaignId}>
                  <SelectTrigger>
                    <SelectValue placeholder="Select campaign" />
                  </SelectTrigger>
                  <SelectContent>
                    {campaigns
                      .filter((c) => c.invite_type === 'founder')
                      .map((c) => (
                        <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>
                      ))}
                  </SelectContent>
                </Select>
                {campaigns.filter((c) => c.invite_type === 'founder').length === 0 && (
                  <p className="text-xs text-destructive mt-1">
                    No founder campaigns exist. Create one first.
                  </p>
                )}
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={() => setApproveTarget(null)}>Cancel</Button>
                <Button
                  onClick={handleApproveWaitlist}
                  disabled={approving || !approveCampaignId}
                >
                  {approving ? (
                    <>
                      <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                      Generating...
                    </>
                  ) : 'Approve & Generate Code'}
                </Button>
              </DialogFooter>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* ================================================================ */}
      {/* Reject Waitlist Dialog */}
      {/* ================================================================ */}
      <Dialog open={!!rejectTarget} onOpenChange={() => setRejectTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reject Waitlist Request</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <p className="text-sm text-muted-foreground">
              Rejecting request from <span className="font-medium text-foreground">{rejectTarget?.name}</span> ({rejectTarget?.email}).
            </p>
            <div>
              <label className="block text-sm font-medium text-foreground mb-1">
                Internal Notes <span className="text-muted-foreground text-xs">(optional)</span>
              </label>
              <Input
                value={rejectNotes}
                onChange={(e) => setRejectNotes(e.target.value)}
                placeholder="Reason for rejection (internal only)"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRejectTarget(null)}>Cancel</Button>
            <Button variant="destructive" onClick={handleRejectWaitlist} disabled={rejecting}>
              {rejecting ? 'Rejecting...' : 'Reject'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ================================================================ */}
      {/* Create Campaign Dialog */}
      {/* ================================================================ */}
      <Dialog open={showCreateCampaign} onOpenChange={setShowCreateCampaign}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create Campaign</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div>
              <label className="block text-sm font-medium text-foreground mb-1">Name</label>
              <Input
                value={campaignForm.name}
                onChange={(e) => {
                  const name = e.target.value;
                  setCampaignForm((f) => ({ ...f, name, slug: autoSlug(name) }));
                }}
                placeholder="e.g. SEO Agency Outreach Q1"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-foreground mb-1">Slug</label>
              <Input
                value={campaignForm.slug}
                onChange={(e) => setCampaignForm((f) => ({ ...f, slug: e.target.value }))}
                placeholder="e.g. seo-agency-q1"
              />
              <p className="text-xs text-muted-foreground mt-1">
                Used in the ?cid= URL parameter
              </p>
            </div>
            <div>
              <label className="block text-sm font-medium text-foreground mb-1">Channel</label>
              <Input
                value={campaignForm.channel}
                onChange={(e) => setCampaignForm((f) => ({ ...f, channel: e.target.value }))}
                placeholder="e.g. email, linkedin, conference"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-foreground mb-1">Template Version</label>
              <Input
                value={campaignForm.template_version}
                onChange={(e) => setCampaignForm((f) => ({ ...f, template_version: e.target.value }))}
                placeholder="e.g. v1"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-foreground mb-1">Invite Type</label>
              <Select
                value={campaignForm.invite_type}
                onValueChange={(v) => setCampaignForm((f) => ({ ...f, invite_type: v as 'trial' | 'founder' }))}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="trial">Trial (free activation)</SelectItem>
                  <SelectItem value="founder">Founder ($999 purchase)</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowCreateCampaign(false)}>Cancel</Button>
            <Button onClick={handleCreateCampaign} disabled={creatingCampaign || !campaignForm.name || !campaignForm.slug}>
              {creatingCampaign ? 'Creating...' : 'Create Campaign'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ================================================================ */}
      {/* Generate Invites Dialog */}
      {/* ================================================================ */}
      <Dialog open={showGenerate} onOpenChange={setShowGenerate}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Generate Invite Codes</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div>
              <label className="block text-sm font-medium text-foreground mb-1">Campaign</label>
              <Select value={genCampaignId} onValueChange={setGenCampaignId}>
                <SelectTrigger>
                  <SelectValue placeholder="Select campaign" />
                </SelectTrigger>
                <SelectContent>
                  {campaigns.map((c) => (
                    <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div>
              <label className="block text-sm font-medium text-foreground mb-1">Mode</label>
              <Tabs value={generateMode} onValueChange={(v) => setGenerateMode(v as 'count' | 'csv')}>
                <TabsList className="w-full">
                  <TabsTrigger value="count" className="flex-1">Generate N codes</TabsTrigger>
                  <TabsTrigger value="csv" className="flex-1">Upload CSV</TabsTrigger>
                </TabsList>
              </Tabs>
            </div>

            {generateMode === 'count' ? (
              <div>
                <label className="block text-sm font-medium text-foreground mb-1">Number of codes</label>
                <Input
                  type="number"
                  min={1}
                  max={500}
                  value={genCount}
                  onChange={(e) => setGenCount(parseInt(e.target.value) || 1)}
                />
              </div>
            ) : (
              <div>
                <label className="block text-sm font-medium text-foreground mb-1">CSV with emails</label>
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => csvInputRef.current?.click()}
                  >
                    <Upload className="h-4 w-4 mr-1" />
                    {genCsvFile ? genCsvFile.name : 'Choose file'}
                  </Button>
                  {genCsvFile && (
                    <Button variant="ghost" size="sm" onClick={() => setGenCsvFile(null)}>
                      <X className="h-4 w-4" />
                    </Button>
                  )}
                </div>
                <input
                  ref={csvInputRef}
                  type="file"
                  accept=".csv"
                  className="hidden"
                  onChange={(e) => setGenCsvFile(e.target.files?.[0] || null)}
                />
              </div>
            )}

            <Separator />

            {(() => {
              const selectedCampaign = campaigns.find((c) => c.id === genCampaignId);
              const isFounder = selectedCampaign?.invite_type === 'founder';
              return isFounder ? (
                <div className="rounded bg-yellow-50 border border-yellow-300 p-3 text-sm text-yellow-800">
                  Founder invites redirect users to Stripe Checkout ($999 one-time).
                  Credits, trial days, and plan limits are set automatically after payment.
                </div>
              ) : (
                <div className="grid grid-cols-3 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-muted-foreground mb-1">Credits</label>
                    <Input
                      type="number"
                      value={genCredits}
                      onChange={(e) => setGenCredits(parseInt(e.target.value) || 50000)}
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-muted-foreground mb-1">Trial days</label>
                    <Input
                      type="number"
                      value={genTrialDays}
                      onChange={(e) => setGenTrialDays(parseInt(e.target.value) || 14)}
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-muted-foreground mb-1">Code expiry (days)</label>
                    <Input
                      type="number"
                      value={genExpiresDays}
                      onChange={(e) => setGenExpiresDays(parseInt(e.target.value) || 90)}
                    />
                  </div>
                </div>
              );
            })()}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowGenerate(false)}>Cancel</Button>
            <Button
              onClick={handleGenerate}
              disabled={generating || !genCampaignId || (generateMode === 'csv' && !genCsvFile)}
            >
              {generating ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Generating...
                </>
              ) : 'Generate'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ================================================================ */}
      {/* Generated Codes Display Dialog */}
      {/* ================================================================ */}
      <Dialog open={showCodes} onOpenChange={setShowCodes}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Generated Invite Codes</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-destructive font-medium">
            These codes are shown once. Copy or export them now.
          </p>
          <div className="max-h-72 overflow-y-auto border rounded bg-muted/50">
            <table className="w-full text-xs font-mono">
              <thead className="sticky top-0 bg-muted border-b">
                <tr>
                  <th className="text-left p-2 font-medium text-muted-foreground">Invite Link</th>
                  <th className="text-left p-2 font-medium text-muted-foreground">Recipient</th>
                  <th className="text-right p-2 font-medium text-muted-foreground"></th>
                </tr>
              </thead>
              <tbody>
                {generatedCodes.map((c, i) => {
                  const link = _buildInviteUrl(c.raw_code, generatedCampaignSlug, generatedCampaignType);
                  return (
                    <tr key={i} className="border-b border-border/50">
                      <td className="p-2 max-w-[320px] truncate" title={link}>{link}</td>
                      <td className="p-2 text-muted-foreground">{c.recipient_email || '-'}</td>
                      <td className="p-2 text-right">
                        <button
                          onClick={() => {
                            navigator.clipboard.writeText(link);
                            toast.success('Invite link copied');
                          }}
                          className="inline-flex items-center gap-1 text-primary hover:underline"
                          title="Copy invite link"
                        >
                          <Copy className="h-3.5 w-3.5" />
                          <span>Copy</span>
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="text-xs text-muted-foreground">
            {generatedCodes.length} code{generatedCodes.length !== 1 ? 's' : ''} generated
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={handleCopyAll}>
              <Copy className="h-4 w-4 mr-1" />
              Copy All Links
            </Button>
            <Button variant="outline" onClick={handleExportCsv}>
              <Download className="h-4 w-4 mr-1" />
              Export CSV
            </Button>
            <Button onClick={() => setShowCodes(false)}>Done</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ================================================================ */}
      {/* Revoke Confirmation Dialog */}
      {/* ================================================================ */}
      <Dialog open={!!revokeTarget} onOpenChange={() => setRevokeTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Revoke Invite</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Are you sure you want to revoke this invite code? This cannot be undone.
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRevokeTarget(null)}>Cancel</Button>
            <Button variant="destructive" onClick={handleRevoke} disabled={revoking}>
              {revoking ? 'Revoking...' : 'Revoke'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </DashboardLayout>
  );
}
