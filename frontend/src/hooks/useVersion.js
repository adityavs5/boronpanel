import { useQuery } from '@tanstack/react-query'
import { get } from '@/lib/api'
import { APP_VERSION } from '@/config/constants'

// Runtime panel version from the backend (authoritative -- it's what is
// actually deployed), falling back to the build-time constant baked into the
// bundle while loading or if the request fails. The value never changes for
// the lifetime of a deployed backend, so cache it for the session.
export function useVersion() {
  const { data } = useQuery({
    queryKey: ['panel-version'],
    queryFn: () => get('/api/v1/version'),
    staleTime: Infinity,
    retry: false,
  })
  return data?.version ? `v${data.version}` : APP_VERSION
}
