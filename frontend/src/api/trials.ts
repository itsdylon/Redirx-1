import { API_BASE_URL, getAuthHeaders, getAuthHeadersMultipart } from './config';

// ============================================================================
// Types
// ============================================================================

export interface Campaign {
  id: string;
  name: string;
  slug: string;
  channel?: string;
  template_version?: string;
  invite_type?: 'trial' | 'founder';
  owner_user_id?: string;
  created_at: string;
  stats?: CampaignStats;
}

export interface CampaignStats {
  total: number;
  created: number;
  sent: number;
  redeemed: number;
  expired: number;
  revoked: number;
}

export interface Invite {
  id: string;
  code?: string;
  code_prefix: string;
  recipient_email?: string;
  campaign_id: string;
  status: string;
  invite_type?: 'trial' | 'founder';
  credits_granted: number;
  trial_days: number;
  max_redemptions: number;
  redemptions: number;
  expires_at?: string;
  created_by_user_id?: string;
  sent_at?: string;
  redeemed_at?: string;
  redeemed_by_user_id?: string;
  notes?: string;
  created_at: string;
  updated_at: string;
  trial_campaigns?: { name: string; slug: string; invite_type?: string };
}

export interface GeneratedInvite {
  id: string;
  raw_code: string;
  recipient_email?: string;
  code_prefix: string;
  created_at: string;
}

export interface ValidationResult {
  valid: boolean;
  error?: string;
  invite_type?: 'trial' | 'founder';
  campaign_name?: string;
  campaign_slug?: string;
  trial_days?: number;
  credits_granted?: number;
  requires_email?: boolean;
}

export interface RedemptionResult {
  success: boolean;
  error?: string;
  trial_days?: number;
  credits_granted?: number;
  trial_expires_at?: string;
  campaign_name?: string;
}

export interface OnboardingReportUser {
  id: string;
  email?: string;
  full_name?: string;
  company?: string;
  onboarding_status: string;
  path?: 'sample' | 'real' | null;
  completed_steps: string[];
  onboarding_started_at?: string | null;
  onboarding_last_seen_at?: string | null;
  onboarding_completed_at?: string | null;
  hours_since_last_activity?: number | null;
  hours_in_progress?: number | null;
  non_tutorial_sessions: number;
}

export interface OnboardingReportResponse {
  success: boolean;
  generated_at: string;
  filters: {
    stuck_hours: number;
    limit: number;
    stuck_before_utc: string;
  };
  summary: {
    total_users: number;
    status_counts: Record<string, number>;
    funnel_counts: Record<string, number>;
    stuck_in_progress: number;
  };
  total_stuck_users: number;
  returned_rows: number;
  stuck_users: OnboardingReportUser[];
}

// ============================================================================
// Admin: Campaigns
// ============================================================================

export async function createCampaign(data: {
  name: string;
  slug: string;
  channel?: string;
  template_version?: string;
  invite_type?: 'trial' | 'founder';
}): Promise<Campaign> {
  const res = await fetch(`${API_BASE_URL}/api/admin/trials/campaigns`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || 'Failed to create campaign');
  }
  const json = await res.json();
  return json.campaign;
}

export async function listCampaigns(): Promise<Campaign[]> {
  const res = await fetch(`${API_BASE_URL}/api/admin/trials/campaigns`, {
    headers: getAuthHeaders(),
  });
  if (!res.ok) {
    if (res.status === 403) {
      throw new Error('Admin access required');
    }
    const err = await res.json();
    throw new Error(err.error || 'Failed to list campaigns');
  }
  const json = await res.json();
  return json.campaigns;
}

export async function getOnboardingReport(params?: {
  stuck_hours?: number;
  limit?: number;
}): Promise<OnboardingReportResponse> {
  const searchParams = new URLSearchParams();
  if (typeof params?.stuck_hours === 'number') {
    searchParams.set('stuck_hours', String(params.stuck_hours));
  }
  if (typeof params?.limit === 'number') {
    searchParams.set('limit', String(params.limit));
  }

  const qs = searchParams.toString();
  const url = `${API_BASE_URL}/api/admin/onboarding/report${qs ? '?' + qs : ''}`;
  const res = await fetch(url, {
    headers: getAuthHeaders(),
  });

  if (!res.ok) {
    if (res.status === 403) {
      throw new Error('Admin access required');
    }
    const err = await res.json();
    throw new Error(err.error || 'Failed to load onboarding report');
  }

  return res.json();
}

// ============================================================================
// Admin: Invites
// ============================================================================

export async function generateInvites(data: {
  campaign_id: string;
  count?: number;
  recipient_emails?: string[];
  expires_days?: number;
  credits_granted?: number;
  trial_days?: number;
  invite_type?: 'trial' | 'founder';
}): Promise<GeneratedInvite[]> {
  const res = await fetch(`${API_BASE_URL}/api/admin/trials/invites`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || 'Failed to generate invites');
  }
  const json = await res.json();
  return json.invites;
}

export async function generateInvitesWithCsv(
  campaignId: string,
  csvFile: File,
  options?: { expires_days?: number; credits_granted?: number; trial_days?: number }
): Promise<GeneratedInvite[]> {
  const formData = new FormData();
  formData.append('campaign_id', campaignId);
  formData.append('csv', csvFile);
  if (options?.expires_days) formData.append('expires_days', String(options.expires_days));
  if (options?.credits_granted) formData.append('credits_granted', String(options.credits_granted));
  if (options?.trial_days) formData.append('trial_days', String(options.trial_days));

  const res = await fetch(`${API_BASE_URL}/api/admin/trials/invites`, {
    method: 'POST',
    headers: getAuthHeadersMultipart(),
    body: formData,
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || 'Failed to generate invites from CSV');
  }
  const json = await res.json();
  return json.invites;
}

export async function listInvites(params?: {
  campaign_id?: string;
  status?: string;
}): Promise<Invite[]> {
  const searchParams = new URLSearchParams();
  if (params?.campaign_id) searchParams.set('campaign_id', params.campaign_id);
  if (params?.status) searchParams.set('status', params.status);

  const qs = searchParams.toString();
  const url = `${API_BASE_URL}/api/admin/trials/invites${qs ? '?' + qs : ''}`;

  const res = await fetch(url, {
    headers: getAuthHeaders(),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || 'Failed to list invites');
  }
  const json = await res.json();
  return json.invites;
}

export async function markSent(inviteIds: string[]): Promise<{ updated: number }> {
  const res = await fetch(`${API_BASE_URL}/api/admin/trials/mark-sent`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({ invite_ids: inviteIds }),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || 'Failed to mark invites as sent');
  }
  return res.json();
}

export async function revokeInvite(inviteId: string): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/api/admin/trials/revoke`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({ invite_id: inviteId }),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || 'Failed to revoke invite');
  }
}

// ============================================================================
// Public/User: Validation & Redemption
// ============================================================================

export async function validateTrialCode(code: string): Promise<ValidationResult> {
  const res = await fetch(`${API_BASE_URL}/api/trials/validate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code }),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || 'Validation failed');
  }
  return res.json();
}

export async function redeemTrialCode(code: string): Promise<RedemptionResult> {
  const res = await fetch(`${API_BASE_URL}/api/trials/redeem`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({ code }),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || 'Redemption failed');
  }
  return res.json();
}

// ============================================================================
// Founder: Waitlist
// ============================================================================

export interface WaitlistEntry {
  id: string;
  name: string;
  email: string;
  company?: string;
  status: 'pending' | 'approved' | 'rejected';
  admin_notes?: string;
  approved_by_user_id?: string;
  generated_invite_id?: string;
  trial_invites?: { code?: string };
  ip_address?: string;
  created_at: string;
  updated_at: string;
}

export async function submitWaitlistRequest(data: {
  name: string;
  email: string;
  company?: string;
}): Promise<{ success: boolean; entry: WaitlistEntry }> {
  const res = await fetch(`${API_BASE_URL}/api/founder/waitlist`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || 'Failed to submit request');
  }
  return res.json();
}

export async function listWaitlist(params?: {
  status?: string;
}): Promise<WaitlistEntry[]> {
  const searchParams = new URLSearchParams();
  if (params?.status) searchParams.set('status', params.status);

  const qs = searchParams.toString();
  const url = `${API_BASE_URL}/api/admin/trials/waitlist${qs ? '?' + qs : ''}`;

  const res = await fetch(url, {
    headers: getAuthHeaders(),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || 'Failed to list waitlist');
  }
  const json = await res.json();
  return json.entries;
}

export async function approveWaitlistEntry(data: {
  waitlist_id: string;
  campaign_id: string;
}): Promise<{ success: boolean; raw_code: string }> {
  const res = await fetch(`${API_BASE_URL}/api/admin/trials/waitlist/approve`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || 'Failed to approve request');
  }
  return res.json();
}

export async function rejectWaitlistEntry(data: {
  waitlist_id: string;
  admin_notes?: string;
}): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/api/admin/trials/waitlist/reject`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || 'Failed to reject request');
  }
}

// ============================================================================
// Founder: Checkout
// ============================================================================

export async function createFounderCheckout(code: string): Promise<{ url: string }> {
  const res = await fetch(`${API_BASE_URL}/api/founder/checkout`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({ code }),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || 'Checkout failed');
  }
  return res.json();
}
