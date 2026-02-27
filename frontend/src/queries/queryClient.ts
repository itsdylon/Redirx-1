import { QueryClient } from '@tanstack/react-query';
import { isUnauthorizedError } from './auth';

export function createAppQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        gcTime: 15 * 60_000,
        refetchOnWindowFocus: false,
        retry: (failureCount, error) => {
          if (isUnauthorizedError(error)) {
            return false;
          }
          return failureCount < 1;
        },
      },
    },
  });
}

export const appQueryClient = createAppQueryClient();
