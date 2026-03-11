interface PlanHolder {
  plan?: string;
}

export function isEnterprisePlan(plan?: string): boolean {
  return plan === 'agency' || plan === 'enterprise';
}

export function isToolPlan(plan?: string): boolean {
  return !isEnterprisePlan(plan);
}

export function isEnterpriseUser(user?: PlanHolder | null): boolean {
  return isEnterprisePlan(user?.plan);
}

export function isToolUser(user?: PlanHolder | null): boolean {
  return isToolPlan(user?.plan);
}

// Backward-compatible alias used across existing components.
export function isAgencyPlan(plan?: string): boolean {
  return isEnterprisePlan(plan);
}
