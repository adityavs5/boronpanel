import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Boxes, Plus, MoreHorizontal, Play, Square, RotateCw, ScrollText, Trash2, RefreshCw,
} from 'lucide-react'
import { get, post, del } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Input, Textarea, FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/Tabs'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
  DialogBody, DialogFooter, ConfirmDialog,
} from '@/components/ui/Dialog'
import {
  DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuSeparator,
} from '@/components/ui/DropdownMenu'
import { CenteredSpinner } from '@/components/ui/Spinner'
import { toast } from '@/components/ui/Toast'

const NODE_VERSIONS = ['22', '20', '18']
const PYTHON_APP_TYPES = [
  { value: 'wsgi', label: 'WSGI (Flask, Django)' },
  { value: 'asgi', label: 'ASGI (FastAPI, Starlette)' },
]

// Parse a "KEY=value" per line textarea into a plain object for the env_vars body.
function parseEnvVars(text) {
  const out = {}
  for (const raw of String(text || '').split('\n')) {
    const line = raw.trim()
    if (!line || line.startsWith('#')) continue
    const idx = line.indexOf('=')
    if (idx === -1) continue
    const key = line.slice(0, idx).trim()
    if (key) out[key] = line.slice(idx + 1).trim()
  }
  return out
}

const EMPTY_FORM = { domain: '', name: '', entry_point: '', node_version: '20', app_type: 'wsgi', env_vars_text: '' }

function AppsPanel({ username, type }) {
  const isNode = type === 'node'
  const base = `/api/v1/accounts/${username}/apps/${type}`
  const queryKey = ['apps', type, username]
  const qc = useQueryClient()

  const [createOpen, setCreateOpen] = useState(false)
  const [form, setForm] = useState(EMPTY_FORM)
  const [toDelete, setToDelete] = useState(null)
  const [logsApp, setLogsApp] = useState(null)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey,
    queryFn: () => get(base),
    enabled: !!username,
    // Poll every 5s while any app is running so status/logs stay live.
    refetchInterval: (query) =>
      (query.state.data?.apps || []).some((a) => a.active) ? 5000 : false,
  })

  const domainsQuery = useQuery({
    queryKey: ['domains', username],
    queryFn: () => get(`/api/v1/accounts/${username}/domains`),
    enabled: !!username && createOpen,
  })
  const domains = domainsQuery.data?.domains || []

  const setField = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }))

  const createMut = useMutation({
    mutationFn: (body) => post(base, body),
    onSuccess: (app) => {
      toast.success('Application created', `${app?.name || form.name} was created. Upload your code, then start it.`)
      qc.invalidateQueries({ queryKey })
      setCreateOpen(false)
      setForm(EMPTY_FORM)
    },
    onError: (e) => toast.error('Could not create application', e.message),
  })

  const actionMut = useMutation({
    mutationFn: ({ app, action }) => post(`${base}/${app.id}/${action}`),
    onSuccess: (_res, { app, action }) => {
      const verb = { start: 'started', stop: 'stopped', restart: 'restarted' }[action] || action
      toast.success(`Application ${verb}`, app.name)
      qc.invalidateQueries({ queryKey })
    },
    onError: (e) => toast.error('Action failed', e.message),
  })

  const deleteMut = useMutation({
    mutationFn: (app) => del(`${base}/${app.id}`),
    onSuccess: (_res, app) => {
      toast.success('Application deleted', `${app.name} and its service unit were removed.`)
      qc.invalidateQueries({ queryKey })
      setToDelete(null)
    },
    onError: (e) => toast.error('Could not delete application', e.message),
  })

  const label = isNode ? 'Node.js' : 'Python'

  const columns = [
    {
      key: 'name',
      header: 'Name',
      sortable: true,
      searchable: true,
      render: (r) => <span className="font-medium text-foreground">{r.name}</span>,
    },
    {
      key: 'domain',
      header: 'Domain',
      sortable: true,
      searchable: true,
      render: (r) => r.domain || <span className="text-muted-foreground">—</span>,
    },
    {
      key: 'port',
      header: 'Port',
      sortable: true,
      cellClassName: 'tabular-nums',
      render: (r) => (r.port ? r.port : <span className="text-muted-foreground">—</span>),
    },
    {
      key: 'status',
      header: 'Status',
      sortable: true,
      sortValue: (r) => (r.active ? 1 : 0),
      render: (r) => <StatusBadge status={r.active ? 'running' : 'stopped'} />,
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      searchable: false,
      render: (r) => (
        <div className="flex justify-end" onClick={(e) => e.stopPropagation()}>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${r.name}`}>
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              <DropdownMenuItem disabled={r.active} onSelect={() => actionMut.mutate({ app: r, action: 'start' })}>
                <Play className="h-4 w-4" /> Start
              </DropdownMenuItem>
              <DropdownMenuItem disabled={!r.active} onSelect={() => actionMut.mutate({ app: r, action: 'stop' })}>
                <Square className="h-4 w-4" /> Stop
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => actionMut.mutate({ app: r, action: 'restart' })}>
                <RotateCw className="h-4 w-4" /> Restart
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={() => setLogsApp(r)}>
                <ScrollText className="h-4 w-4" /> View logs
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem destructive onSelect={() => setToDelete(r)}>
                <Trash2 className="h-4 w-4" /> Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      ),
    },
  ]

  return (
    <div>
      <DataTable
        columns={columns}
        data={data?.apps}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        filterable
        searchPlaceholder={`Search ${label} apps…`}
        pageSize={15}
        initialSort={{ key: 'name', dir: 'asc' }}
        getRowKey={(r) => r.id}
        emptyTitle={`No ${label} apps yet`}
        emptyDescription={`Create a ${label} application to run it behind one of your domains.`}
        emptyIcon={Boxes}
        emptyAction={<Button onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4" /> Create app</Button>}
        toolbar={
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="h-4 w-4" /> Create {label} app
          </Button>
        }
      />

      {/* Create dialog */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent size="md">
          <DialogHeader>
            <DialogTitle>Create {label} application</DialogTitle>
            <DialogDescription>
              Code is not deployed here — upload it to the app directory (via the file manager or git),
              then start the app.
            </DialogDescription>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              const body = {
                domain: form.domain,
                name: form.name.trim(),
                entry_point: form.entry_point.trim(),
                env_vars: parseEnvVars(form.env_vars_text),
              }
              if (isNode) body.node_version = form.node_version
              else body.app_type = form.app_type
              createMut.mutate(body)
            }}
          >
            <DialogBody className="space-y-4">
              <FormField label="Domain" required hint="The domain that will proxy to this application.">
                <Select value={form.domain} onChange={setField('domain')} required disabled={domainsQuery.isLoading}>
                  <option value="" disabled>
                    {domainsQuery.isLoading ? 'Loading domains…' : 'Select a domain'}
                  </option>
                  {domains.map((d) => (
                    <option key={d.id ?? d.domain} value={d.domain}>{d.domain}</option>
                  ))}
                </Select>
              </FormField>
              <FormField label="App name" required hint="Lowercase identifier used for the service unit and directory.">
                <Input value={form.name} onChange={setField('name')} placeholder="my-api" required />
              </FormField>
              <FormField label="Entry point" required hint={isNode ? 'e.g. server.js' : 'e.g. app:app (module:callable) or wsgi.py'}>
                <Input value={form.entry_point} onChange={setField('entry_point')} placeholder={isNode ? 'server.js' : 'app:app'} required />
              </FormField>
              {isNode ? (
                <FormField label="Node version">
                  <Select value={form.node_version} onChange={setField('node_version')}>
                    {NODE_VERSIONS.map((v) => (
                      <option key={v} value={v}>Node {v}</option>
                    ))}
                  </Select>
                </FormField>
              ) : (
                <FormField label="App type">
                  <Select value={form.app_type} onChange={setField('app_type')}>
                    {PYTHON_APP_TYPES.map((t) => (
                      <option key={t.value} value={t.value}>{t.label}</option>
                    ))}
                  </Select>
                </FormField>
              )}
              <FormField label="Environment variables" hint="One KEY=value per line. Optional.">
                <Textarea
                  value={form.env_vars_text}
                  onChange={setField('env_vars_text')}
                  rows={4}
                  placeholder={'NODE_ENV=production\nAPI_KEY=secret'}
                />
              </FormField>
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => setCreateOpen(false)}>Cancel</Button>
              <Button
                type="submit"
                loading={createMut.isPending}
                disabled={!form.domain || !form.name.trim() || !form.entry_point.trim()}
              >
                Create app
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Logs dialog */}
      <LogsDialog base={base} app={logsApp} onOpenChange={(v) => { if (!v) setLogsApp(null) }} />

      {/* Delete confirmation */}
      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(v) => { if (!v) setToDelete(null) }}
        title={toDelete ? `Delete ${toDelete.name}?` : 'Delete application?'}
        description="The application is stopped and its service unit removed. This cannot be undone."
        confirmLabel="Delete app"
        loading={deleteMut.isPending}
        onConfirm={() => toDelete && deleteMut.mutate(toDelete)}
      />
    </div>
  )
}

function LogsDialog({ base, app, onOpenChange }) {
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ['app-logs', base, app?.id],
    queryFn: () => get(`${base}/${app.id}/logs`),
    enabled: !!app,
  })
  const lines = data?.log_lines || []

  return (
    <Dialog open={!!app} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>Logs — {app?.name}</DialogTitle>
          <DialogDescription>Most recent output from the application's service unit.</DialogDescription>
        </DialogHeader>
        <DialogBody>
          {isLoading ? (
            <CenteredSpinner />
          ) : error ? (
            <div className="text-sm text-danger">{error.message}</div>
          ) : lines.length === 0 ? (
            <div className="rounded-btn border border-border bg-muted/40 px-4 py-8 text-center text-sm text-muted-foreground">
              No log output yet.
            </div>
          ) : (
            <pre className="max-h-[55vh] overflow-auto rounded-btn border border-border bg-muted/40 p-4 font-mono text-xs leading-relaxed text-foreground whitespace-pre-wrap">
              {lines.join('\n')}
            </pre>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={() => refetch()} loading={isFetching}>
            <RefreshCw className="h-4 w-4" /> Refresh
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default function Apps({ type }) {
  const username = useAccountUsername()

  return (
    <div>
      <PageHeader
        title={type === "python" ? "Python App" : type === "node" ? "Node.js App" : "Applications"}
        description={type ? `Create and manage ${type === "node" ? "Node.js" : "Python"} applications on your domains.` : "Run Node.js and Python applications behind your domains."}
        icon={Boxes}
      />

      {type ? <AppsPanel key={type} username={username} type={type}/> : <Tabs defaultValue="node">
        <TabsList>
          <TabsTrigger value="node">Node.js</TabsTrigger>
          <TabsTrigger value="python">Python</TabsTrigger>
        </TabsList>
        <TabsContent value="node">
          <AppsPanel username={username} type="node" />
        </TabsContent>
        <TabsContent value="python">
          <AppsPanel username={username} type="python" />
        </TabsContent>
      </Tabs>}
    </div>
  )
}
