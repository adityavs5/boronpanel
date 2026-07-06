import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { Wrench, Play, Package, Blocks as WordpressIcon } from 'lucide-react'
import { get, post } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Switch } from '@/components/ui/Toggle'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/Tabs'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { CenteredSpinner } from '@/components/ui/Spinner'
import { EmptyState, ErrorState } from '@/components/ui/States'
import { toast } from '@/components/ui/Toast'

// A live-updating panel for a CommandRun job (WP-CLI or Composer).
function RunOutput({ username, tool, jobId }) {
  const { data: job } = useQuery({
    queryKey: [tool, 'run', username, jobId],
    queryFn: () => get(`/api/v1/accounts/${username}/${tool}/runs/${jobId}`),
    enabled: jobId != null,
    refetchInterval: (q) => {
      const s = q.state.data?.status
      return s === 'pending' || s === 'running' ? 1500 : false
    },
  })
  if (jobId == null) return null
  const output = [(job?.stdout || ''), (job?.stderr || '')].filter(Boolean).join('\n')
  return (
    <div className="mt-4 space-y-2">
      <div className="flex items-center gap-2 text-sm">
        <span className="font-mono text-xs text-muted-foreground">{job?.command}</span>
        {job && <StatusBadge status={job.status} />}
        {job?.exit_code != null && <span className="text-xs text-muted-foreground">exit {job.exit_code}</span>}
      </div>
      {job?.revealed_secret && (
        <p className="rounded-btn border border-warning/40 bg-warning/10 px-3 py-2 text-sm">
          New password (shown once): <span className="font-mono font-semibold">{job.revealed_secret}</span>
        </p>
      )}
      <pre className="max-h-80 overflow-auto rounded-card border border-border bg-[#0b1120] p-3 font-mono text-xs text-slate-200">
        {output || (job?.status === 'running' || job?.status === 'pending' ? 'Running…' : job?.error || '(no output)')}
      </pre>
    </div>
  )
}

const WP_ACTIONS = [
  { value: 'core_update', label: 'Update WordPress core' },
  { value: 'core_check_update', label: 'Check for core updates' },
  { value: 'plugin_list', label: 'List plugins' },
  { value: 'plugin_update', label: 'Update plugin', fields: ['name_or_all'] },
  { value: 'plugin_activate', label: 'Activate plugin', fields: ['name'] },
  { value: 'plugin_deactivate', label: 'Deactivate plugin', fields: ['name'] },
  { value: 'theme_list', label: 'List themes' },
  { value: 'theme_update', label: 'Update theme', fields: ['name_or_all'] },
  { value: 'theme_activate', label: 'Activate theme', fields: ['name'] },
  { value: 'theme_deactivate', label: 'Deactivate theme', fields: ['name'] },
  { value: 'user_reset_password', label: 'Reset user password', fields: ['user'] },
  { value: 'cache_flush', label: 'Flush cache' },
  { value: 'search_replace', label: 'Search-replace (preview first)', fields: ['search', 'replace'] },
  { value: 'maintenance_on', label: 'Enable maintenance mode' },
  { value: 'maintenance_off', label: 'Disable maintenance mode' },
]

function WpCliTab({ username }) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['wpcli-detect', username],
    queryFn: () => get(`/api/v1/accounts/${username}/wordpress/detect`),
    enabled: !!username,
  })
  const installs = data?.installs || []
  const [install, setInstall] = useState('')
  const [action, setAction] = useState('plugin_list')
  const [fields, setFields] = useState({ name: '', all: false, user: '', search: '', replace: '', preview: true })
  const [jobId, setJobId] = useState(null)
  const spec = WP_ACTIONS.find((a) => a.value === action)
  const activeInstall = install || installs[0]?.id || ''

  const runMut = useMutation({
    mutationFn: () => {
      const body = { action }
      if (spec.fields?.includes('name_or_all')) { if (fields.all) body.all = true; else body.name = fields.name }
      if (spec.fields?.includes('name')) body.name = fields.name
      if (spec.fields?.includes('user')) body.user = fields.user
      if (spec.fields?.includes('search')) { body.search = fields.search; body.replace = fields.replace; body.preview = fields.preview }
      return post(`/api/v1/accounts/${username}/wordpress/${activeInstall}/wpcli`, body)
    },
    onSuccess: (job) => { setJobId(job.id); toast.success('WP-CLI command started') },
    onError: (e) => toast.error('Could not run WP-CLI', e.message),
  })

  if (isLoading) return <CenteredSpinner />
  if (error) return <ErrorState error={error} onRetry={refetch} />
  if (installs.length === 0) {
    return <EmptyState icon={WordpressIcon} title="No WordPress installs detected" description="No wp-config.php found in your domains' document roots." />
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>WP-CLI</CardTitle>
        <CardDescription>Run WordPress management commands as your own user.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <FormField label="WordPress install">
            <Select value={activeInstall} onChange={(e) => setInstall(e.target.value)}>
              {installs.map((i) => <option key={i.id} value={i.id}>{i.domain}{i.wp_version ? ` (WP ${i.wp_version})` : ''}</option>)}
            </Select>
          </FormField>
          <FormField label="Command">
            <Select value={action} onChange={(e) => setAction(e.target.value)}>
              {WP_ACTIONS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
            </Select>
          </FormField>
        </div>
        {spec.fields?.includes('name_or_all') && (
          <div className="flex items-end gap-3">
            <FormField label="Plugin/theme slug" className="flex-1">
              <Input value={fields.name} disabled={fields.all} onChange={(e) => setFields((f) => ({ ...f, name: e.target.value }))} placeholder="akismet" />
            </FormField>
            <FormField label="All"><div className="flex h-10 items-center"><Switch checked={fields.all} onCheckedChange={(v) => setFields((f) => ({ ...f, all: v }))} /></div></FormField>
          </div>
        )}
        {spec.fields?.includes('name') && (
          <FormField label="Plugin/theme slug"><Input value={fields.name} onChange={(e) => setFields((f) => ({ ...f, name: e.target.value }))} placeholder="akismet" /></FormField>
        )}
        {spec.fields?.includes('user') && (
          <FormField label="User login"><Input value={fields.user} onChange={(e) => setFields((f) => ({ ...f, user: e.target.value }))} placeholder="admin" /></FormField>
        )}
        {spec.fields?.includes('search') && (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <FormField label="Search for"><Input value={fields.search} onChange={(e) => setFields((f) => ({ ...f, search: e.target.value }))} placeholder="http://old.com" /></FormField>
            <FormField label="Replace with"><Input value={fields.replace} onChange={(e) => setFields((f) => ({ ...f, replace: e.target.value }))} placeholder="https://new.com" /></FormField>
            <FormField label="Preview only (dry-run)"><div className="flex h-10 items-center"><Switch checked={fields.preview} onCheckedChange={(v) => setFields((f) => ({ ...f, preview: v }))} /></div></FormField>
          </div>
        )}
      </CardContent>
      <CardFooter className="flex-col items-stretch">
        <Button loading={runMut.isPending} onClick={() => runMut.mutate()} className="self-start"><Play className="h-4 w-4" /> Run command</Button>
        <RunOutput username={username} tool="wordpress/wpcli" jobId={jobId} />
      </CardFooter>
    </Card>
  )
}

function ComposerTab({ username }) {
  const [form, setForm] = useState({ command: 'install', app_dir: '', package: '' })
  const [jobId, setJobId] = useState(null)
  const runMut = useMutation({
    mutationFn: () => {
      const body = { command: form.command, app_dir: form.app_dir.trim() }
      if (form.command === 'require') body.package = form.package.trim()
      return post(`/api/v1/accounts/${username}/composer`, body)
    },
    onSuccess: (job) => { setJobId(job.id); toast.success('Composer command started') },
    onError: (e) => toast.error('Could not run Composer', e.message),
  })
  return (
    <Card>
      <CardHeader>
        <CardTitle>Composer</CardTitle>
        <CardDescription>Run Composer in an application directory as your own user.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <FormField label="Command">
            <Select value={form.command} onChange={(e) => setForm((f) => ({ ...f, command: e.target.value }))}>
              <option value="install">install</option>
              <option value="update">update</option>
              <option value="require">require</option>
              <option value="dump-autoload">dump-autoload</option>
            </Select>
          </FormField>
          <FormField label="App directory" hint="Relative to your home, e.g. public_html">
            <Input value={form.app_dir} onChange={(e) => setForm((f) => ({ ...f, app_dir: e.target.value }))} placeholder="public_html" />
          </FormField>
          {form.command === 'require' && (
            <FormField label="Package" className="sm:col-span-2">
              <Input value={form.package} onChange={(e) => setForm((f) => ({ ...f, package: e.target.value }))} placeholder="monolog/monolog:^3.0" />
            </FormField>
          )}
        </div>
      </CardContent>
      <CardFooter className="flex-col items-stretch">
        <Button loading={runMut.isPending} onClick={() => runMut.mutate()} className="self-start"><Play className="h-4 w-4" /> Run Composer</Button>
        <RunOutput username={username} tool="composer" jobId={jobId} />
      </CardFooter>
    </Card>
  )
}

export default function DevTools() {
  const username = useAccountUsername()
  return (
    <div>
      <PageHeader title="Dev Tools" description="WP-CLI and Composer, run asynchronously as your own account user." icon={Wrench} />
      <Tabs defaultValue="wpcli">
        <TabsList>
          <TabsTrigger value="wpcli"><WordpressIcon className="h-4 w-4" /> WP-CLI</TabsTrigger>
          <TabsTrigger value="composer"><Package className="h-4 w-4" /> Composer</TabsTrigger>
        </TabsList>
        <TabsContent value="wpcli"><WpCliTab username={username} /></TabsContent>
        <TabsContent value="composer"><ComposerTab username={username} /></TabsContent>
      </Tabs>
    </div>
  )
}
