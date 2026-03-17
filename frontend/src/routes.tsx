import { isEnterprisePlan, isToolPlan } from './lib/plans';

export const ROUTES = {
  root: '/',
  login: '/login',
  signup: '/signup',
  authCallback: '/auth/callback',
  urlMatch: '/url-match',
  contentMatch: '/content-match',
  quickMatch: '/quick-match',
  dashboard: '/dashboard',
  projects: '/projects',
  upload: '/upload',
  review: '/review/:sessionId',
  settings: '/settings',
  pricing: '/pricing',
  account: '/account',
  demo: '/demo',
} as const;

export function getAuthedHomeRoute(plan?: string): string {
  return isEnterprisePlan(plan) ? ROUTES.dashboard : ROUTES.urlMatch;
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

export function canAccessPricing(_plan?: string, _sourceSessionId?: string | null): boolean {
  return true;
}

export function getRetryRouteForPlan(plan?: string): string {
  return isEnterprisePlan(plan) ? ROUTES.upload : ROUTES.urlMatch;
}
