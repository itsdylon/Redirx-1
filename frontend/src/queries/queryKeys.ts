export const queryKeys = {
  dashboard: {
    summary: ['dashboard', 'summary'] as const,
  },
  sessions: {
    all: ['sessions', 'all'] as const,
  },
  results: {
    bySession: (sessionId: string) => ['results', 'session', sessionId] as const,
    deepPreview: (sessionId: string) => ['results', 'deep-preview', sessionId] as const,
  },
  billing: {
    subscription: ['billing', 'subscription'] as const,
    plans: ['billing', 'plans'] as const,
  },
  email: {
    preferences: ['email', 'preferences'] as const,
  },
  user: {
    profile: ['user', 'profile'] as const,
  },
};
