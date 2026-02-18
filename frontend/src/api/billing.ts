import { API_BASE_URL, getAuthHeaders } from './config';

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
}

export interface PlanInfo {
  id: string;
  name: string;
  description: string;
  monthly_price: number | null;
  credits_limit: number;
  lifetime_credits_total: number;
  quick_match_limit: number | null;
  max_concurrent_projects: number;
  price_id_monthly?: string;
  price_id_annual?: string;
  price_id?: string; // for one-time plans like founder
}

export async function getSubscriptionStatus(): Promise<SubscriptionStatus> {
  const res = await fetch(`${API_BASE_URL}/api/billing/subscription`, {
    headers: getAuthHeaders(),
  });
  if (!res.ok) throw new Error('Failed to fetch subscription status');
  return res.json();
}

export async function getPlans(): Promise<PlanInfo[]> {
  const res = await fetch(`${API_BASE_URL}/api/billing/plans`);
  if (!res.ok) throw new Error('Failed to fetch plans');
  const data = await res.json();
  return data.plans;
}

export async function createCheckoutSession(priceId: string): Promise<string> {
  const res = await fetch(`${API_BASE_URL}/api/billing/create-checkout-session`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({ price_id: priceId }),
  });
  if (!res.ok) {
    const data = await res.json();
    throw new Error(data.error || 'Failed to create checkout session');
  }
  const data = await res.json();
  return data.url;
}

export async function createPortalSession(): Promise<string> {
  const res = await fetch(`${API_BASE_URL}/api/billing/create-portal-session`, {
    method: 'POST',
    headers: getAuthHeaders(),
  });
  if (!res.ok) {
    const data = await res.json();
    throw new Error(data.error || 'Failed to create portal session');
  }
  const data = await res.json();
  return data.url;
}
