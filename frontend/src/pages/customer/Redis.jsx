import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Server, Activity, MemoryStick, Gauge, Plug, Power, Zap, Copy } from 'lucide-react'
import { get, post, patch, del } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { copyToClipboard } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Input, FormField } from '@/components/ui/Input'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter,
  ConfirmDialog,
} from '@/components/ui/Dialog'
import { EmptyState, ErrorState } from '@/components/ui/States'
import { CardSkeleton } from '@/components/ui/Skeleton'
import { CenteredSpinner } from '@/components/ui/Spinner'
import { toast } from '@/components/ui/Toast'

const MIN_MEM = 16
const MAX_MEM = 4096

export default function Redis() {
  const username = useAccountUsername()
  const qc = useQueryClient()

  const [connOpen, setConnOpen] = useState(false)
  const [flushOpen, setFlushOpen] = useState(false)
  const [disableOpen, setDisableOpen] = useState(false)
  const [memValue, setMemValue] = useState('')
  const [memError, setMemError] = useState('')

  const { data: status, isLoading, error, refetch } = useQuery({
    queryKey: ['redis', username],
    queryFn: () => get(`/api/v1/accounts/${username}/redis`),
    enabled: !!username,
  })

  const provisioned = !!status?.provisioned

  useEffect(() => {
    if (status?.mem_mb != null) setMemValue(String(status.mem_mb))
  }, [status?.mem_mb])

  const invalidate = () => qc.invalidateQueries({ queryKey: ['redis', username] })

  const enableMut = useMutation({
    mutationFn: (body) => post(`/api/v1/accounts/${username}/redis`, body),
    onSuccess: () => { toast.success('Redis enabled', 'Your Redis instance is being provisioned.'); invalidate() },
    onError: (e) => toast.error('Could not enable Redis', e.message),
  })

  const setMemMut = useMutation({
    mutationFn: (mem_mb) => patch(`/api/v1/accounts/${username}/redis`, { mem_mb }),
    onSuccess: (_res, mem_mb) => { toast.success('Memory limit updated', `Redis is now capped at ${mem_mb} MB.`); invalidate() },
    onError: (e) => toast.error('Could not update memory limit', e.message),
  })

  const flushMut = useMutation({
    mutationFn: () => post(`/api/v1/accounts/${username}/redis/flush`),
    onSuccess: () => { toast.success('Flushed', 'All keys were removed from this Redis instance.'); setFlushOpen(false); invalidate() },
    onError: (e) => { toast.error('Could not flush Redis', e.message); setFlushOpen(false) },
  })

  const disableMut = useMutation({
    mutationFn: () => del(`/api/v1/accounts/${username}/redis`),
    onSuccess: () => { toast.success('Redis disabled', 'The instance was stopped and removed.'); setDisableOpen(false); invalidate() },
    onError: (e) => { toast.error('Could not disable Redis', e.message); setDisableOpen(false) },
  })

  const copyValue = async (text, label) => {
    const ok = await copyToClipboard(text)
    if (ok) toast.success(`${label} copied to clipboard`)
    else toast.error('Could not copy', 'Copy the value manually.')
  }

  const saveMem = (e) => {
    e.preventDefault()
    const n = parseInt(memValue, 10)
    if (Number.isNaN(n) || n < MIN_MEM || n > MAX_MEM) {
      setMemError(`Enter a value between ${MIN_MEM} and ${MAX_MEM} MB.`)
      return
    }
    setMemError('')
    if (n === status?.mem_mb) return
    setMemMut.mutate(n)
  }

  return (
    <div>
      <PageHeader title="Redis" description="Per-account in-memory data store for caching and sessions." icon={Server}>
        {provisioned && (
          <>
            <Button variant="secondary" onClick={() => setConnOpen(true)}>
              <Plug className="h-4 w-4" /> Connection info
            </Button>
            <Button variant="danger" onClick={() => setDisableOpen(true)}>
              <Power className="h-4 w-4" /> Disable
            </Button>
          </>
        )}
      </PageHeader>

      {isLoading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => <CardSkeleton key={i} />)}
        </div>
      ) : error ? (
        <ErrorState error={error} onRetry={refetch} />
      ) : !provisioned ? (
        <EmptyState
          icon={Server}
          title="Redis is not enabled"
          description="Enable a private Redis instance for this account. It runs over a Unix socket with no network exposure."
          action={(
            <Button loading={enableMut.isPending} onClick={() => enableMut.mutate({ mem_mb: 256 })}>
              <Zap className="h-4 w-4" /> Enable Redis
            </Button>
          )}
        />
      ) : (
        <div className="space-y-6">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <StatCard icon={Activity} label="Status" value={<StatusBadge status={status.active} />} />
            <StatCard icon={MemoryStick} label="Memory used" value={status.used_memory_human || 'unknown'} />
            <StatCard icon={Gauge} label="Memory limit" value={`${status.mem_mb} MB`} />
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Settings</CardTitle>
              <CardDescription>Adjust the memory cap or clear all cached data.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              <form onSubmit={saveMem}>
                <FormField
                  label="Memory limit"
                  htmlFor="mem_mb"
                  hint={`Between ${MIN_MEM} and ${MAX_MEM} MB.`}
                  error={memError}
                >
                  <div className="flex items-center gap-2">
                    <Input
                      id="mem_mb"
                      type="number"
                      min={MIN_MEM}
                      max={MAX_MEM}
                      value={memValue}
                      invalid={!!memError}
                      onChange={(e) => setMemValue(e.target.value)}
                      className="max-w-[10rem]"
                    />
                    <span className="text-sm text-muted-foreground">MB</span>
                    <Button type="submit" variant="secondary" loading={setMemMut.isPending}>Update limit</Button>
                  </div>
                </FormField>
              </form>

              <div className="border-t border-border pt-4">
                <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">Socket path</div>
                <div className="flex items-center gap-2">
                  <code className="flex-1 truncate rounded-btn border border-border bg-muted px-3 py-2 font-mono text-sm text-foreground">{status.socket_path}</code>
                  <Button variant="outline" size="icon" aria-label="Copy socket path" onClick={() => copyValue(status.socket_path, 'Socket path')}>
                    <Copy className="h-4 w-4" />
                  </Button>
                </div>
              </div>

              <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
                <div>
                  <div className="text-sm font-medium text-foreground">Flush all keys</div>
                  <div className="text-sm text-muted-foreground">Permanently delete every key in this instance. This cannot be undone.</div>
                </div>
                <Button variant="warning" onClick={() => setFlushOpen(true)}>
                  <Zap className="h-4 w-4" /> Flush
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      <ConnectionDialog open={connOpen} onOpenChange={setConnOpen} username={username} onCopy={copyValue} />

      <ConfirmDialog
        open={flushOpen}
        onOpenChange={setFlushOpen}
        title="Flush all keys?"
        description="This permanently removes every key in this Redis instance. This cannot be undone."
        confirmLabel="Flush all keys"
        variant="danger"
        confirmationText="FLUSH"
        loading={flushMut.isPending}
        onConfirm={() => flushMut.mutate()}
      />

      <ConfirmDialog
        open={disableOpen}
        onOpenChange={setDisableOpen}
        title="Disable Redis?"
        description="This stops the Redis instance and removes its data. You can re-enable it later, but the cached data will be gone."
        confirmLabel="Disable Redis"
        variant="danger"
        confirmationText={username}
        loading={disableMut.isPending}
        onConfirm={() => disableMut.mutate()}
      />
    </div>
  )
}

function StatCard({ icon: Icon, label, value }) {
  return (
    <Card>
      <CardContent className="flex items-center gap-4 py-5">
        <div className="flex h-11 w-11 items-center justify-center rounded-btn bg-accent-50 text-accent-600 dark:bg-accent-950 dark:text-accent-300">
          <Icon className="h-5 w-5" />
        </div>
        <div className="min-w-0">
          <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</div>
          <div className="mt-0.5 truncate text-lg font-semibold text-foreground">{value}</div>
        </div>
      </CardContent>
    </Card>
  )
}

function ConnectionDialog({ open, onOpenChange, username, onCopy }) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['redis-connection', username],
    queryFn: () => get(`/api/v1/accounts/${username}/redis/connection-info`),
    enabled: open && !!username,
  })

  const path = data?.socket_path
  const predisSnippet = path ? `new Predis\\Client(['scheme' => 'unix', 'path' => '${path}']);` : ''
  const phpredisSnippet = path ? `$redis = new Redis();\n$redis->connect('${path}');` : ''

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>Connection info</DialogTitle>
          <DialogDescription>Connect from PHP over this account's private Unix socket.</DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-4">
          {isLoading ? (
            <CenteredSpinner />
          ) : error ? (
            <ErrorState error={error} onRetry={refetch} />
          ) : (
            <>
              <CredRow label="Socket path" value={path} onCopy={() => onCopy(path, 'Socket path')} />
              <CredRow
                label="predis parameters"
                value={`scheme=unix, path=${data?.predis_parameters?.path ?? path}`}
                onCopy={() => onCopy(JSON.stringify(data?.predis_parameters ?? {}), 'predis parameters')}
              />
              <CodeBlock label="predis (PHP)" code={predisSnippet} onCopy={() => onCopy(predisSnippet, 'predis snippet')} />
              <CodeBlock label="phpredis (native extension)" code={phpredisSnippet} onCopy={() => onCopy(phpredisSnippet, 'phpredis snippet')} />
            </>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>Close</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function CredRow({ label, value, onCopy }) {
  return (
    <div>
      <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="flex items-center gap-2">
        <code className="flex-1 truncate rounded-btn border border-border bg-muted px-3 py-2 font-mono text-sm text-foreground">{value}</code>
        <Button variant="outline" size="icon" aria-label={`Copy ${label.toLowerCase()}`} onClick={onCopy}>
          <Copy className="h-4 w-4" />
        </Button>
      </div>
    </div>
  )
}

function CodeBlock({ label, code, onCopy }) {
  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</span>
        <Button variant="ghost" size="sm" onClick={onCopy}>
          <Copy className="h-3.5 w-3.5" /> Copy
        </Button>
      </div>
      <pre className="overflow-x-auto rounded-btn border border-border bg-muted px-3 py-2 font-mono text-xs text-foreground">{code}</pre>
    </div>
  )
}
