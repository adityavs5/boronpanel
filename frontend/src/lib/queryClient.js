import { QueryClient } from '@tanstack/react-query'

// 30s stale time (goal spec). Mutations invalidate explicitly via
// queryClient.invalidateQueries at each call site for immediate freshness.
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: (failureCount, error) => {
        // Never retry auth/permission/not-found — only transient errors.
        const s = error?.status
        if (s === 401 || s === 403 || s === 404 || s === 422) return false
        return failureCount < 2
      },
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: false,
    },
  },
})
