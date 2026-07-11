import { useEffect, useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { BellRing, Save, AtSign, AlertTriangle } from 'lucide-react'
import { get, patch } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { formatDate, titleCase } from '@/lib/utils'
import { NOTIFICATION_EVENTS } from '@/config/constants'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '@/components/ui/Card'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { Switch } from '@/components/ui/Toggle'
import { CardSkeleton } from '@/components/ui/Skeleton'
import { ErrorState } from '@/components/ui/States'
import { toast } from '@/components/ui/Toast'

// Friendly label for an event key: prefer the known catalog, otherwise
// humanize whatever key the backend returned (event maps can drift ahead of
// the frontend catalog).
function eventLabel(key) {
  return NOTIFICATION_EVENTS.find((e) => e.key === key)?.label || titleCase(key.replace(/[._]+/g, ' '))
}

export default function NotificationSettings() {
  // Admin settings live under /admin/notifications, but keep the account context
  // in the cache key so the page stays correct if reused per-identity.
  const username = useAccountUsername()
  const qc = useQueryClient()

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['admin-notification-settings', username],
    queryFn: () => get('/api/v1/admin/notifications/settings'),
  })

  // Editable form state, seeded from the server once (and on any refetch).
  const [senderAddress, setSenderAddress] = useState('')
  const [events, setEvents] = useState({})

  useEffect(() => {
    if (!data) return
    setSenderAddress(data.sender_address || '')
    // Resolve every known + returned event to a concrete boolean. A missing key
    // defaults to enabled, mirroring the legacy `events.get(event, True)`.
    const resolved = {}
    for (const { key } of NOTIFICATION_EVENTS) resolved[key] = data.events?.[key] ?? true
    for (const [key, val] of Object.entries(data.events || {})) resolved[key] = !!val
    setEvents(resolved)
  }, [data])

  // One row per event: the full known catalog plus any extra keys the backend
  // sent that we don't have a hardcoded label for.
  const eventRows = useMemo(() => {
    const known = NOTIFICATION_EVENTS.map((e) => e.key)
    const extra = Object.keys(events).filter((k) => !known.includes(k))
    return [...known, ...extra].map((key) => ({ key, label: eventLabel(key) }))
  }, [events])

  const dirty = useMemo(() => {
    if (!data) return false
    if ((data.sender_address || '') !== senderAddress) return true
    return eventRows.some((r) => (data.events?.[r.key] ?? true) !== !!events[r.key])
  }, [data, senderAddress, events, eventRows])

  const saveMut = useMutation({
    mutationFn: (body) => patch('/api/v1/admin/notifications/settings', body),
    onSuccess: () => {
      toast.success('Notification settings saved')
      qc.invalidateQueries({ queryKey: ['admin-notification-settings', username] })
    },
    onError: (e) => toast.error('Could not save settings', e.message),
  })

  function handleSave(e) {
    e.preventDefault()
    saveMut.mutate({ sender_address: senderAddress.trim(), events })
  }

  const senderEmpty = !!data && !senderAddress.trim()

  const columns = [
    {
      key: 'event',
      header: 'Event',
      sortable: true,
      searchable: true,
      sortValue: (r) => r.label,
      searchValue: (r) => `${r.label} ${r.key}`,
      render: (r) => (
        <div>
          <div className="font-medium text-foreground">{r.label}</div>
          <code className="text-xs text-muted-foreground">{r.key}</code>
        </div>
      ),
    },
    {
      key: 'enabled',
      header: 'Enabled',
      align: 'right',
      searchable: false,
      render: (r) => (
        <div className="flex justify-end">
          <Switch
            checked={!!events[r.key]}
            onCheckedChange={(next) => setEvents((prev) => ({ ...prev, [r.key]: next }))}
            aria-label={`Toggle ${r.label}`}
          />
        </div>
      ),
    },
  ]

  return (
    <form onSubmit={handleSave}>
      <PageHeader
        title="Notification settings"
        description="Server-wide email notifications, relayed via local Postfix. A message is only sent when both this global switch and the account's own preference are enabled for that event."
        icon={BellRing}
      />

      {error ? (
        <Card>
          <CardContent>
            <ErrorState error={error} onRetry={refetch} />
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          {/* Sender address */}
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Sender address</CardTitle>
                <CardDescription>
                  The &ldquo;From&rdquo; address for every notification. Leave blank to disable all notification
                  email server-wide.
                </CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              {isLoading ? (
                <CardSkeleton className="border-0 p-0 shadow-none" />
              ) : (
                <FormField
                  label="From address"
                  htmlFor="sender_address"
                  hint="e.g. boron@example.com"
                >
                  <div className="relative">
                    <AtSign className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      id="sender_address"
                      type="email"
                      className="pl-8"
                      value={senderAddress}
                      onChange={(e) => setSenderAddress(e.target.value)}
                      placeholder="boron@example.com"
                    />
                  </div>
                </FormField>
              )}
              {senderEmpty && (
                <div className="mt-3 flex items-start gap-2 rounded-btn border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-foreground">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
                  <span>No sender address set — all notification email is disabled server-wide until you add one.</span>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Per-event switches */}
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Events</CardTitle>
                <CardDescription>Choose which events trigger a notification email.</CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              <DataTable
                columns={columns}
                data={eventRows}
                loading={isLoading}
                error={error}
                onRetry={refetch}
                getRowKey={(r) => r.key}
                initialSort={{ key: 'event', dir: 'asc' }}
                emptyTitle="No notification events"
                emptyDescription="There are no notification events to configure."
                emptyIcon={BellRing}
              />
            </CardContent>
            <CardFooter className="justify-between">
              <span className="text-sm text-muted-foreground">
                {data?.updated_at ? `Last updated ${formatDate(data.updated_at)}` : 'Not yet saved'}
              </span>
              <Button type="submit" loading={saveMut.isPending} disabled={isLoading || !dirty}>
                <Save className="h-4 w-4" /> Save changes
              </Button>
            </CardFooter>
          </Card>
        </div>
      )}
    </form>
  )
}
