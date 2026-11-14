/**
 * The app's single TanStack Query client and its retry and freshness defaults.
 *
 * Purpose: Client errors (4xx) won't fix themselves, so only transport and server failures are
 * retried, and data stays fresh for 30 seconds while refocusing the window refetches it.
 * How it fits: Provided to the whole app by `App.tsx` through `QueryClientProvider`;
 * layer: config.
 * Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
 */

import { QueryClient } from '@tanstack/react-query'
import { isApiError } from './api'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: true,
      // Client errors (4xx) won't fix themselves; retry only transport/5xx failures.
      retry: (failureCount, error) => {
        if (isApiError(error) && error.status >= 400 && error.status < 500) return false
        return failureCount < 2
      },
    },
    mutations: {
      retry: false,
    },
  },
})
