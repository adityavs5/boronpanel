import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { FileCode2, Puzzle, RotateCcw, SlidersHorizontal, TriangleAlert } from 'lucide-react'
import { get, patch, put, del } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { PHP_VERSIONS } from '@/config/constants'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Input, FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Switch } from '@/components/ui/Toggle'
import { ConfirmDialog } from '@/components/ui/Dialog'
import { ErrorState } from '@/components/ui/States'
import { CardSkeleton } from '@/components/ui/Skeleton'
import { toast } from '@/components/ui/Toast'

// Friendly labels/help for the server-provided directive descriptors. The
// list of directives, their types, bounds and defaults all come from the
// API (GET /php-ini "directives") — only the copy lives here.
const DIRECTIVE_COPY = {
  memory_limit: { label: 'Memory limit', help: 'Maximum memory one PHP request may use (e.g. 256M).' },
  upload_max_filesize: { label: 'Upload max filesize', help: 'Largest single file an upload may contain (e.g. 64M).' },
  post_max_size: { label: 'Post max size', help: 'Maximum size of a whole POST body — must be at least the upload limit.' },
  max_execution_time: { label: 'Max execution time', help: 'Seconds a script may run before PHP stops it.' },
  display_errors: { label: 'Display errors', help: 'Print PHP errors in the page output. Leave off in production; errors are always written to your PHP error log.' },
  error_reporting: { label: 'Error reporting', help: 'Which error levels PHP reports.' },
  max_input_vars: { label: 'Max input variables', help: 'Maximum number of form/GET/POST variables per request. Large WordPress menus or WooCommerce settings pages may need more than the default 1000.' },
  max_input_time: { label: 'Max input time', help: 'Seconds PHP may spend parsing request input. -1 uses the execution time limit.' },
  max_file_uploads: { label: 'Max file uploads', help: 'Maximum number of files in one upload request.' },
  allow_url_fopen: { label: 'Allow URL fopen', help: 'Let file functions open remote http(s):// URLs. Many plugins expect this on.' },
  'session.gc_maxlifetime': { label: 'Session lifetime', help: 'Seconds an idle PHP session survives before garbage collection.' },
  'date.timezone': { label: 'Default timezone', help: 'IANA timezone name, e.g. Asia/Kolkata or UTC.' },
}

const ERROR_REPORTING_PRESETS = [
  'E_ALL & ~E_DEPRECATED & ~E_STRICT',
  'E_ALL',
  'E_ALL & ~E_NOTICE',
  'E_ALL & ~E_NOTICE & ~E_WARNING & ~E_DEPRECATED',
  'E_ERROR',
]

const EXTENSION_COPY = {
  curl: 'HTTP client library — required by most plugins that call external APIs.',
  igbinary: 'Compact binary serializer, used with Redis/Memcached.',
  imagick: 'ImageMagick image processing (thumbnails, PDFs, HEIC).',
  intl: 'Internationalization: locales, number/date formatting, transliteration.',
  mysqli: 'MySQL/MariaDB driver — WordPress and most CMSs need this.',
  opcache: 'Opcode cache — keeps compiled PHP in memory. Big performance win.',
  pdo_mysql: 'PDO driver for MySQL/MariaDB (Laravel, Symfony, most frameworks).',
  pdo_sqlite: 'PDO driver for SQLite databases.',
  redis: 'Redis client (phpredis) — object caching and sessions.',
  sqlite3: 'SQLite3 driver.',
}

// Disabling these tends to take a site down or visibly slow it — warn inline.
const RISKY_TO_DISABLE = {
  mysqli: 'WordPress and most database-driven sites stop working without it.',
  opcache: 'Sites will noticeably slow down without the opcode cache.',
}

export default function Php() {
  const username = useAccountUsername()
  return (
    <div>
      <PageHeader
        title="PHP"
        description="PHP version, runtime settings, and extensions for this account."
        icon={FileCode2}
      />
      <div className="space-y-6">
        <VersionCard username={username} />
        <SettingsCard username={username} />
        <ExtensionsCard username={username} />
      </div>
    </div>
  )
}

function VersionCard({ username }) {
  const qc = useQueryClient()
  const { data: account } = useQuery({
    queryKey: ['account', username],
    queryFn: () => get(`/api/v1/accounts/${username}`),
    enabled: !!username,
  })
  const [version, setVersion] = useState('')
  useEffect(() => {
    if (account?.php_version) setVersion(account.php_version)
  }, [account?.php_version])

  const mut = useMutation({
    mutationFn: () => patch(`/api/v1/accounts/${username}/php-version`, { php_version: version }),
    onSuccess: () => {
      toast.success('PHP version updated', `This account now runs PHP ${version}.`)
      qc.invalidateQueries({ queryKey: ['account', username] })
    },
    onError: (e) => toast.error('Could not change PHP version', e.message),
  })

  return (
    <Card>
      <CardHeader>
        <CardTitle>PHP version</CardTitle>
        <CardDescription>
          The default runtime for every domain on this account. Individual domains can override it from their domain page.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex items-center gap-2">
          <Select
            aria-label="PHP version"
            value={version}
            onChange={(e) => setVersion(e.target.value)}
            className="max-w-[10rem]"
          >
            {PHP_VERSIONS.map((v) => <option key={v} value={v}>PHP {v}</option>)}
          </Select>
          <Button
            variant="secondary"
            loading={mut.isPending}
            disabled={!version || version === account?.php_version}
            onClick={() => mut.mutate()}
          >
            Change version
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

function SettingsCard({ username }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({})
  const [resetOpen, setResetOpen] = useState(false)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['php-ini', username],
    queryFn: () => get(`/api/v1/accounts/${username}/php-ini`),
    enabled: !!username,
  })

  const directives = data?.directives || []
  useEffect(() => { setForm({}) }, [data])

  const effective = (d) => {
    if (Object.prototype.hasOwnProperty.call(form, d.name)) return form[d.name]
    const raw = d.value ?? d.default
    return d.type === 'bool' ? (raw === true || raw === 'On') : String(raw)
  }
  const serverValue = (d) => {
    const raw = d.value ?? d.default
    return d.type === 'bool' ? (raw === true || raw === 'On') : String(raw)
  }
  const dirty = directives.filter((d) => effective(d) !== serverValue(d))

  const saveMut = useMutation({
    mutationFn: () => {
      const changed = {}
      for (const d of dirty) {
        let v = effective(d)
        if (d.type === 'int') v = parseInt(v, 10)
        // Editing a non-legacy directive back to its exact default unsets
        // the override (null) instead of storing a redundant one.
        const rendered = d.type === 'bool' ? (v ? 'On' : 'Off') : String(v)
        if (!isLegacy(d.name) && rendered === String(d.default)) v = null
        changed[d.name] = v
      }
      return patch(`/api/v1/accounts/${username}/php-ini`, { directives: changed })
    },
    onSuccess: () => {
      toast.success('PHP settings saved', 'Changes apply within a few seconds — PHP workers restart automatically.')
      qc.invalidateQueries({ queryKey: ['php-ini', username] })
    },
    onError: (e) => toast.error('Could not save PHP settings', e.message),
  })

  const resetMut = useMutation({
    mutationFn: () => del(`/api/v1/accounts/${username}/php-ini`),
    onSuccess: () => {
      toast.success('PHP settings reset', 'All values are back to the server defaults.')
      setResetOpen(false)
      qc.invalidateQueries({ queryKey: ['php-ini', username] })
    },
    onError: (e) => { toast.error('Could not reset PHP settings', e.message); setResetOpen(false) },
  })

  const anyOverride = directives.some((d) => d.value !== null && d.value !== undefined)

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <SlidersHorizontal className="h-4 w-4 text-accent-600 dark:text-accent-300" /> Runtime settings
          </CardTitle>
          <CardDescription className="mt-1.5">
            Upload limits, input limits, error handling and more. Values apply to every PHP site on this account.
          </CardDescription>
        </div>
        {anyOverride && (
          <Button variant="ghost" size="sm" onClick={() => setResetOpen(true)}>
            <RotateCcw className="h-3.5 w-3.5" /> Reset all to defaults
          </Button>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <CardSkeleton />
        ) : error ? (
          <ErrorState error={error} onRetry={refetch} />
        ) : (
          <form
            onSubmit={(e) => { e.preventDefault(); if (dirty.length) saveMut.mutate() }}
          >
            <div className="grid grid-cols-1 gap-x-8 gap-y-5 sm:grid-cols-2">
              {directives.map((d) => (
                <DirectiveField
                  key={d.name}
                  descriptor={d}
                  value={effective(d)}
                  overridden={d.value !== null && d.value !== undefined}
                  onChange={(v) => setForm((f) => ({ ...f, [d.name]: v }))}
                />
              ))}
            </div>
            <div className="mt-6 flex items-center gap-3 border-t border-border pt-4">
              <Button type="submit" loading={saveMut.isPending} disabled={!dirty.length}>
                Save changes
              </Button>
              {dirty.length > 0 && (
                <span className="text-sm text-muted-foreground">
                  {dirty.length} unsaved {dirty.length === 1 ? 'change' : 'changes'}
                </span>
              )}
            </div>
          </form>
        )}
      </CardContent>

      <ConfirmDialog
        open={resetOpen}
        onOpenChange={setResetOpen}
        title="Reset PHP settings?"
        description="Every runtime setting goes back to the server default. Your sites keep running; the change applies within a few seconds."
        confirmLabel="Reset to defaults"
        variant="danger"
        loading={resetMut.isPending}
        onConfirm={() => resetMut.mutate()}
      />
    </Card>
  )
}

// Legacy six live on their own settings row server-side; they're never
// auto-unset field-by-field (the reset button clears the whole row).
const LEGACY_NAMES = new Set(['memory_limit', 'upload_max_filesize', 'post_max_size', 'max_execution_time', 'display_errors', 'error_reporting'])
const isLegacy = (name) => LEGACY_NAMES.has(name)

function DirectiveField({ descriptor: d, value, overridden, onChange }) {
  const copy = DIRECTIVE_COPY[d.name] || { label: d.name, help: '' }
  const label = (
    <span className="inline-flex items-center gap-2">
      {copy.label}
      {overridden && <Badge variant="accent">custom</Badge>}
    </span>
  )

  if (d.type === 'bool') {
    return (
      <div className="flex items-start justify-between gap-4 rounded-btn border border-border px-3 py-2.5">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-medium text-foreground">
            {copy.label}
            {overridden && <Badge variant="accent">custom</Badge>}
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground">{copy.help}</p>
        </div>
        <Switch checked={value} onCheckedChange={onChange} aria-label={copy.label} className="mt-1" />
      </div>
    )
  }

  if (d.name === 'error_reporting') {
    const options = ERROR_REPORTING_PRESETS.includes(value) ? ERROR_REPORTING_PRESETS : [value, ...ERROR_REPORTING_PRESETS]
    return (
      <FormField label={label} htmlFor={d.name} hint={copy.help}>
        <Select id={d.name} value={value} onChange={(e) => onChange(e.target.value)}>
          {options.map((o) => <option key={o} value={o}>{o}</option>)}
        </Select>
      </FormField>
    )
  }

  if (d.type === 'int') {
    const unit = d.name.includes('time') || d.name === 'session.gc_maxlifetime' ? 'seconds' : null
    return (
      <FormField label={label} htmlFor={d.name} hint={`${copy.help} Between ${d.min} and ${d.max}.`}>
        <div className="flex items-center gap-2">
          <Input
            id={d.name}
            type="number"
            min={d.min}
            max={d.max}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            className="max-w-[10rem]"
          />
          {unit && <span className="text-sm text-muted-foreground">{unit}</span>}
        </div>
      </FormField>
    )
  }

  // size ("256M") and free-text (timezone)
  return (
    <FormField
      label={label}
      htmlFor={d.name}
      hint={d.type === 'size' ? `${copy.help} Up to ${d.max_mb}M.` : copy.help}
    >
      <Input
        id={d.name}
        value={value}
        placeholder={String(d.default)}
        onChange={(e) => onChange(e.target.value)}
        className="max-w-[12rem]"
      />
    </FormField>
  )
}

function ExtensionsCard({ username }) {
  const qc = useQueryClient()
  const [resetOpen, setResetOpen] = useState(false)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['php-extensions', username],
    queryFn: () => get(`/api/v1/accounts/${username}/php-extensions`),
    enabled: !!username,
  })

  const invalidate = () => qc.invalidateQueries({ queryKey: ['php-extensions', username] })

  const setMut = useMutation({
    mutationFn: (enabled) => put(`/api/v1/accounts/${username}/php-extensions`, { enabled }),
    onSuccess: (_res, enabled) => {
      toast.success('Extensions updated', `${enabled.length} extension${enabled.length === 1 ? '' : 's'} enabled. PHP workers restart automatically.`)
      invalidate()
    },
    onError: (e) => { toast.error('Could not update extensions', e.message); invalidate() },
  })

  const resetMut = useMutation({
    mutationFn: () => del(`/api/v1/accounts/${username}/php-extensions`),
    onSuccess: () => {
      toast.success('Extensions reset', 'Back to the server default selection.')
      setResetOpen(false)
      invalidate()
    },
    onError: (e) => { toast.error('Could not reset extensions', e.message); setResetOpen(false) },
  })

  const toggle = (name, on) => {
    const current = new Set(data?.enabled || [])
    if (on) current.add(name)
    else current.delete(name)
    setMut.mutate([...current].sort())
  }

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <Puzzle className="h-4 w-4 text-accent-600 dark:text-accent-300" /> Extensions
            {data?.overridden && <Badge variant="accent">custom selection</Badge>}
          </CardTitle>
          <CardDescription className="mt-1.5">
            Enable or disable the PHP extensions loaded for this account. Applies to every PHP version you use.
          </CardDescription>
        </div>
        {data?.overridden && (
          <Button variant="ghost" size="sm" onClick={() => setResetOpen(true)}>
            <RotateCcw className="h-3.5 w-3.5" /> Reset to defaults
          </Button>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <CardSkeleton />
        ) : error ? (
          <ErrorState error={error} onRetry={refetch} />
        ) : (
          <ul className="divide-y divide-border">
            {(data?.extensions || []).map((ext) => (
              <li key={ext.name} className="flex items-start justify-between gap-4 py-3 first:pt-0 last:pb-0">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <code className="font-mono text-sm font-medium text-foreground">{ext.name}</code>
                    <Badge variant={ext.stock_enabled ? 'neutral' : 'outline'}>
                      {ext.stock_enabled ? 'default on' : 'default off'}
                    </Badge>
                  </div>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {EXTENSION_COPY[ext.name] || ''}
                    {ext.requires?.length > 0 && ` Requires ${ext.requires.join(', ')}.`}
                  </p>
                  {ext.enabled && RISKY_TO_DISABLE[ext.name] && (
                    <p className="mt-1 flex items-center gap-1 text-xs text-[#B45309] dark:text-warning">
                      <TriangleAlert className="h-3 w-3 shrink-0" /> {RISKY_TO_DISABLE[ext.name]}
                    </p>
                  )}
                </div>
                <Switch
                  checked={ext.enabled}
                  disabled={setMut.isPending}
                  onCheckedChange={(on) => toggle(ext.name, on)}
                  aria-label={`Toggle ${ext.name}`}
                  className="mt-0.5"
                />
              </li>
            ))}
          </ul>
        )}
      </CardContent>

      <ConfirmDialog
        open={resetOpen}
        onOpenChange={setResetOpen}
        title="Reset extensions?"
        description="The account goes back to the server's default extension selection."
        confirmLabel="Reset to defaults"
        variant="danger"
        loading={resetMut.isPending}
        onConfirm={() => resetMut.mutate()}
      />
    </Card>
  )
}
