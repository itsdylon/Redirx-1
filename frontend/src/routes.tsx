import { isEnterprisePlan, isToolPlan } from './lib/plans';

export const ROUTES = {
  root: '/',
  login: '/login',
  signup: '/signup',
  authCallback: '/auth/callback',
  quickMatch: '/quick-match',
  dashboard: '/dashboard',
  projects: '/projects',
  upload: '/upload',
  review: '/review/:sessionId',
  settings: '/settings',
  pricing: '/pricing',
  account: '/account',
  demo: '/demo',
  watch: '/watch/:watchId',
  apiKeys: '/api-keys',
} as const;

export function getAuthedHomeRoute(plan?: string): string {
  return isEnterprisePlan(plan) ? ROUTES.dashboard : ROUTES.quickMatch;
}

export function canAccessDashboard(plan?: string): boolean {
  return isEnterprisePlan(plan);
}

export function canAccessUpload(plan?: string): boolean {
  return isEnterprisePlan(plan);
}

export function canAccessQuickMatch(plan?: string): boolean {
  return isToolPlan(plan);
}

export function canAccessSettingsAndAccount(plan?: string): boolean {
  return isEnterprisePlan(plan);
}

export function canAccessPricing(plan?: string, sourceSessionId?: string | null): boolean {
  if (isEnterprisePlan(plan)) return true;
  return !!sourceSessionId;
}

export function getRetryRouteForPlan(plan?: string): string {
  return isEnterprisePlan(plan) ? ROUTES.upload : ROUTES.quickMatch;
}
