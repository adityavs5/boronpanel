import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Boxes, Plus, MoreHorizontal, Play, Square, RotateCw, ScrollText, Trash2, RefreshCw, PackageCheck,
} from 'lucide-react'
import { api, get, post, del } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { useDomainContext } from '@/hooks/useDomainContext'
import { PageHeader } from '@/components/ui/PageHeader'
import { NodeIcon, PythonIcon } from '@/components/icons/BrandIcons'
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
import { InlineError } from './InlineFeedback'

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

const envVarsText = (env) => Object.entries(env || {}).map(([key, value]) => `${key}=${value}`).join('\n')

const EMPTY_FORM = { domain: '', name: '', entry_point: '', node_version: '20', app_type: 'wsgi', env_vars_text: '' }

// Start, restart and a settings change of a running app wait until the
// process stays up. When it does not, the server stops and disables the app
// and answers 400 with its state and the tail of its log. That text is read
// as sent: the shared error normaliser replaces messages that look like
// server internals (a Python traceback, for example), which these are not.
const START_FAILURE = /^The application did not start/
async function appRequest(method, url, data) {
  const response = await api.request({ method, url, data, validateStatus: (status) => (status >= 200 && status < 300) || status === 400 })
  if (response.status !== 400) return response.data
  const detail = typeof response.data?.detail === 'string' ? response.data.detail : 'The request was refused.'
  const reference = response.headers?.['x-boron-error-reference'] || response.data?.reference || null
  const message = reference && !START_FAILURE.test(detail) ? `${detail} (reference: ${reference})` : detail
  throw Object.assign(new Error(message), { status: 400, detail, reference, startFailure: START_FAILURE.test(detail) })
}
function parseStartFailure(detail) {
  const [summary, logs = ''] = detail.split(' Last log lines: ')
  return { summary: summary.replace(/ Its log is empty\.$/, ''), lines: logs ? logs.split(' | ') : [] }
}

function AppsPanel({ username, type }) {
  const isNode = type === 'node'
  const base = `/api/v1/accounts/${username}/apps/${type}`
  const queryKey = ['apps', type, username]
  const qc = useQueryClient()

  const [createOpen, setCreateOpen] = useState(false)
  const [form, setForm] = useState(EMPTY_FORM)
  const [toDelete, setToDelete] = useState(null)
  const [logsApp, setLogsApp] = useState(null)
  const [selectedId, setSelectedId] = useState(null)
  const [startFailure, setStartFailure] = useState(null) // {app, action, summary, lines, reference}
  const [settings, setSettings] = useState(null) // edit form of the selected app

  const { data, isLoading, error, refetch } = useQuery({
    queryKey,
    queryFn: () => get(base),
    enabled: !!username,
    // Poll every 5s while any app is running so status/logs stay live.
    refetchInterval: (query) =>
      (query.state.data?.apps || []).some((a) => a.active === 'active') ? 5000 : false,
  })

  const domainsQuery = useQuery({
    queryKey: ['domains', username],
    queryFn: () => get(`/api/v1/accounts/${username}/domains`),
    enabled: !!username && createOpen,
  })
  const domains = domainsQuery.data?.domains || []
  const [workingDomain, setWorkingDomain] = useDomainContext(username, domains)

  useEffect(() => {
    if (createOpen && workingDomain && form.domain !== workingDomain) {
      setForm((current) => ({ ...current, domain: workingDomain }))
    }
  }, [createOpen, workingDomain, form.domain])

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

  // A start failure leaves the app stopped (enabled: false), so the list is
  // fetched again and the log tail is shown in a dialog.
  const showStartFailure = (error, app, action) => {
    qc.invalidateQueries({ queryKey })
    if (!error.startFailure) return false
    setStartFailure({ app, action, reference: error.reference, ...parseStartFailure(error.detail) })
    return true
  }

  const actionMut = useMutation({
    mutationFn: ({ app, action }) => appRequest('post', `${base}/${app.id}/${action}`),
    onSuccess: (_res, { app, action }) => {
      const verb = { start: 'started', stop: 'stopped', restart: 'restarted' }[action] || action
      toast.success(`Application ${verb}`, app.name)
      qc.invalidateQueries({ queryKey })
    },
    onError: (e, { app, action }) => { if (!showStartFailure(e, app, action)) toast.error('Action failed', e.message) },
  })

  const updateMut = useMutation({
    mutationFn: ({ app, body }) => appRequest('patch', `${base}/${app.id}`, body),
    onSuccess: (_res, { app }) => {
      toast.success('Settings saved', app.active === 'active' ? `${app.name} was restarted with the new settings.` : `${app.name} uses them the next time it starts.`)
      qc.invalidateQueries({ queryKey })
    },
    onError: (e, { app }) => { if (!showStartFailure(e, app, 'update')) toast.error('Could not save settings', e.message) },
  })

  // npm install / pip install -r requirements.txt, as the account. A failure
  // is a 400 whose detail says where the full log is; it stays in the dialog.
  const installMut = useMutation({
    mutationFn: (app) => post(`${base}/${app.id}/${isNode ? 'npm-install' : 'pip-install'}`, {}),
    onSuccess: (result, app) => {
      if (result?.status === 'skipped') toast.info('Nothing to install', `${app.name} has no requirements.txt.`)
      else toast.success('Dependencies installed', `${isNode ? 'npm' : 'pip'} finished for ${app.name}.`)
    },
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
  const selected = data?.apps?.find((app) => app.id === selectedId)
  // Seeded when another app is opened; the 5 s status refresh keeps edits.
  const selectedKey = selected?.id
  useEffect(() => {
    setSettings(selected ? {
      entry_point: selected.entry_point || '', node_version: selected.node_version || '20',
      app_type: selected.app_type || 'wsgi', env_vars_text: envVarsText(selected.env_vars),
    } : null)
    updateMut.reset()
    installMut.reset()
  }, [selectedKey]) // eslint-disable-line react-hooks/exhaustive-deps
  const settingsChanges = () => {
    if (!selected || !settings) return {}
    const body = {}
    if (settings.entry_point.trim() !== (selected.entry_point || '')) body.entry_point = settings.entry_point.trim()
    if (isNode && settings.node_version !== selected.node_version) body.node_version = settings.node_version
    if (!isNode && settings.app_type !== selected.app_type) body.app_type = settings.app_type
    const env = parseEnvVars(settings.env_vars_text)
    if (JSON.stringify(env) !== JSON.stringify(selected.env_vars || {})) body.env_vars = env
    return body
  }
  const pendingChanges = settingsChanges()

  const columns = [
    {
      key: 'name',
      header: 'Name',
      sortable: true,
      searchable: true,
      render: (r) => <button type="button" className="font-medium text-accent hover:underline text-left" onClick={() => setSelectedId(r.id)} aria-label={`Manage application ${r.name}`}>{r.name}</button>,
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
      sortValue: (r) => (r.active === 'active' ? 1 : 0),
      render: (r) => <StatusBadge status={r.active === 'active' ? 'running' : r.active || 'stopped'} />,
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      searchable: false,
      render: (r) => (
        <div className="flex justify-end gap-2" onClick={(e) => e.stopPropagation()}>
          <Button variant="outline" size="sm" onClick={() => setSelectedId(r.id)}>Manage</Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${r.name}`}>
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              <DropdownMenuItem disabled={r.active === 'active' || actionMut.isPending} onSelect={() => actionMut.mutate({ app: r, action: 'start' })}>
                <Play className="h-4 w-4" /> Start
              </DropdownMenuItem>
              <DropdownMenuItem disabled={r.active !== 'active' || actionMut.isPending} onSelect={() => actionMut.mutate({ app: r, action: 'stop' })}>
                <Square className="h-4 w-4" /> Stop
              </DropdownMenuItem>
              <DropdownMenuItem disabled={actionMut.isPending} onSelect={() => actionMut.mutate({ app: r, action: 'restart' })}>
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

      <Dialog open={!!selected} onOpenChange={(open) => { if (!open) setSelectedId(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="break-all">Manage {selected?.name}</DialogTitle>
            <DialogDescription>Control your {label} application and view its output.</DialogDescription>
          </DialogHeader>
          {selected && <DialogBody className="space-y-4">
            <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-3 text-sm">
              <dt className="text-muted-foreground">Status</dt><dd><StatusBadge status={selected.active === 'active' ? 'running' : selected.active || 'stopped'} /></dd>
              <dt className="text-muted-foreground">Domain</dt><dd className="break-all">{selected.domain || '—'}</dd>
              <dt className="text-muted-foreground">Port</dt><dd>{selected.port || '—'}</dd>
              <dt className="text-muted-foreground">Entry point</dt><dd className="break-all font-mono">{selected.entry_point || '—'}</dd>
            </dl>
            <div className="flex flex-wrap gap-2">
              <Button disabled={actionMut.isPending || updateMut.isPending} loading={actionMut.isPending && actionMut.variables?.action !== 'restart'} onClick={() => actionMut.mutate({ app: selected, action: selected.active === 'active' ? 'stop' : 'start' })}>
                {selected.active === 'active' ? <Square className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                {selected.active === 'active' ? 'Stop' : 'Start'}
              </Button>
              <Button variant="outline" disabled={actionMut.isPending || updateMut.isPending} loading={actionMut.isPending && actionMut.variables?.action === 'restart'} onClick={() => actionMut.mutate({ app: selected, action: 'restart' })}><RotateCw className="h-4 w-4" />Restart</Button>
              <Button variant="outline" onClick={() => { setLogsApp(selected); setSelectedId(null) }}><ScrollText className="h-4 w-4" />View logs</Button>
              <Button variant="outline" loading={installMut.isPending} disabled={actionMut.isPending || updateMut.isPending} onClick={() => installMut.mutate(selected)}><PackageCheck className="h-4 w-4" />{isNode ? 'Run npm install' : 'Install requirements'}</Button>
            </div>
            <InlineError error={!installMut.isPending && installMut.error} />
            {settings && (
              <form className="space-y-4 border-t border-border pt-4" aria-labelledby="app-settings-title" onSubmit={(event) => { event.preventDefault(); if (Object.keys(pendingChanges).length && !updateMut.isPending) updateMut.mutate({ app: selected, body: pendingChanges }) }}>
                <div>
                  <h3 id="app-settings-title" className="text-sm font-semibold text-foreground">Settings</h3>
                  <p className="text-sm text-muted-foreground">{selected.active === 'active' ? 'Saving restarts the app with the new settings.' : 'The app uses saved settings the next time it starts.'}</p>
                </div>
                <FormField label="Entry point" htmlFor="app-settings-entry" required hint={isNode ? 'e.g. server.js' : 'e.g. app:app (module:callable) or wsgi.py'}>
                  <Input id="app-settings-entry" value={settings.entry_point} onChange={(event) => setSettings((value) => ({ ...value, entry_point: event.target.value }))} required />
                </FormField>
                {isNode ? (
                  <FormField label="Node version" htmlFor="app-settings-version">
                    <Select id="app-settings-version" value={settings.node_version} onChange={(event) => setSettings((value) => ({ ...value, node_version: event.target.value }))}>
                      {[...new Set([...NODE_VERSIONS, settings.node_version])].map((v) => <option key={v} value={v}>Node {v}</option>)}
                    </Select>
                  </FormField>
                ) : (
                  <FormField label="App type" htmlFor="app-settings-type">
                    <Select id="app-settings-type" value={settings.app_type} onChange={(event) => setSettings((value) => ({ ...value, app_type: event.target.value }))}>
                      {PYTHON_APP_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                    </Select>
                  </FormField>
                )}
                <FormField label="Environment variables" htmlFor="app-settings-env" hint="One KEY=value per line.">
                  <Textarea id="app-settings-env" rows={4} value={settings.env_vars_text} onChange={(event) => setSettings((value) => ({ ...value, env_vars_text: event.target.value }))} />
                </FormField>
                <Button type="submit" variant="secondary" loading={updateMut.isPending} disabled={!settings.entry_point.trim() || !Object.keys(pendingChanges).length || actionMut.isPending}>Save settings</Button>
              </form>
            )}
          </DialogBody>}
          <DialogFooter>
            <Button variant="danger" disabled={actionMut.isPending} onClick={() => { setToDelete(selected); setSelectedId(null) }}><Trash2 className="h-4 w-4" />Delete app</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

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
                <Select value={form.domain} onChange={(event) => { setWorkingDomain(event.target.value); setField('domain')(event) }} required disabled={domainsQuery.isLoading}>
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

      {/* Start failure: the app was stopped; show why, from its own log */}
      <Dialog open={!!startFailure} onOpenChange={(open) => { if (!open) setStartFailure(null) }}>
        <DialogContent size="lg">
          <DialogHeader>
            <DialogTitle className="break-all">{startFailure?.app?.name} did not start</DialogTitle>
            <DialogDescription>
              {startFailure?.action === 'update' ? 'The settings were saved, but the app stopped right after the restart. ' : ''}
              It was stopped so it does not restart in a loop. Fix the cause shown below, then start it again.
            </DialogDescription>
          </DialogHeader>
          <DialogBody className="space-y-3">
            <p className="break-words text-sm text-foreground">{startFailure?.summary}</p>
            {startFailure?.lines?.length ? (
              <pre aria-label="Last log lines" className="max-h-[45vh] overflow-auto whitespace-pre-wrap break-all rounded-btn border border-border bg-muted/40 p-3 font-mono text-xs leading-relaxed text-foreground">{startFailure.lines.join('\n')}</pre>
            ) : (
              <p className="text-sm text-muted-foreground">The app wrote nothing to its log before it stopped.</p>
            )}
            {startFailure?.reference && <p className="text-xs text-muted-foreground">Reference: <code>{startFailure.reference}</code></p>}
          </DialogBody>
          <DialogFooter>
            <Button variant="secondary" onClick={() => { setLogsApp(startFailure.app); setStartFailure(null) }}><ScrollText className="h-4 w-4" /> View full logs</Button>
            <Button onClick={() => setStartFailure(null)}>Done</Button>
          </DialogFooter>
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
        title={type === "python" ? "Python apps" : type === "node" ? "Node.js apps" : "Applications"}
        description={type ? `Create and manage ${type === "node" ? "Node.js" : "Python"} applications on your domains.` : "Run Node.js and Python applications behind your domains."}
        icon={type === "python" ? PythonIcon : type === "node" ? NodeIcon : Boxes}
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
