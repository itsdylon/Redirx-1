import { API_BASE_URL, getAuthHeaders } from './config';
import { throwApiErrorFromResponse } from '../utils/errorHandler';

export interface SubscriptionStatus {
  plan: string;
  has_subscription: boolean;
  credits_limit: number;
  credits_used: number;
  credits_remaining: number;
  is_lifetime: boolean;
  lifetime_credits_total: number;
  lifetime_credits_used: number;
  lifetime_credits_remaining: number;
  quick_match_limit: number;
  quick_match_used: number;
  quick_match_unlimited: boolean;
  max_concurrent_projects: number;
  current_period_end: number | null;
  cancel_at_period_end: boolean;
  cancel_at: number | null;
  trial_expires_at: string | null;
}

export interface PlanInfo {
  id: string;
  name: string;
  description: string;
  monthly_price: number | null;
  annual_price: number | null;
  credits_limit: number;
  lifetime_credits_total: number;
  quick_match_limit: number | null;
  max_concurrent_projects: number;
  price_id_monthly?: string;
  price_id_annual?: string;
  price_id?: string; // for one-time plans like founder
  credits_price_id?: string;
  founder_price_id?: string;
}

export async function getSubscriptionStatus(): Promise<SubscriptionStatus> {
  const res = await fetch(`${API_BASE_URL}/api/billing/subscription`, {
    headers: getAuthHeaders(),
  });
  if (!res.ok) {
    await throwApiErrorFromResponse(res, 'Unable to load subscription details right now.');
  }
  return res.json();
}

export async function getPlans(): Promise<PlanInfo[]> {
  const res = await fetch(`${API_BASE_URL}/api/billing/plans`);
  if (!res.ok) {
    await throwApiErrorFromResponse(res, 'Unable to load billing plans right now.');
  }
  const data = await res.json();
  return data.plans;
}

export async function createCheckoutSession(
  priceId: string,
  options?: {
    success_url?: string;
    cancel_url?: string;
    context_source?: string;
    source_session_id?: string;
  },
): Promise<string> {
  const body: Record<string, string> = { price_id: priceId };
  if (options?.success_url) body.success_url = options.success_url;
  if (options?.cancel_url) body.cancel_url = options.cancel_url;
  if (options?.context_source) body.context_source = options.context_source;
  if (options?.source_session_id) body.source_session_id = options.source_session_id;

  const res = await fetch(`${API_BASE_URL}/api/billing/create-checkout-session`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    await throwApiErrorFromResponse(res, 'Unable to start checkout right now. Please try again.');
  }
  const data = await res.json();
  return data.url;
}

export async function reactivateSubscription(): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/api/billing/reactivate-subscription`, {
    method: 'POST',
    headers: getAuthHeaders(),
  });
  if (!res.ok) {
    await throwApiErrorFromResponse(res, 'Unable to reactivate your subscription right now.');
  }
}

export async function createPortalSession(): Promise<string> {
  const res = await fetch(`${API_BASE_URL}/api/billing/create-portal-session`, {
    method: 'POST',
    headers: getAuthHeaders(),
  });
  if (!res.ok) {
    await throwApiErrorFromResponse(res, 'Unable to open billing portal right now.');
  }
  const data = await res.json();
  return data.url;
}
