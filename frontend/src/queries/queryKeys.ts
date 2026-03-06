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
    status: ['billing', 'status'] as const,
    quote: (sourceSessionId: string) => ['billing', 'quote', sourceSessionId] as const,
    unlockStatus: (sourceSessionId: string) => ['billing', 'unlock-status', sourceSessionId] as const,
    estimate: (pageCount: number) => ['billing', 'estimate', pageCount] as const,
  },
  email: {
    preferences: ['email', 'preferences'] as const,
  },
  user: {
    profile: ['user', 'profile'] as const,
  },
};
