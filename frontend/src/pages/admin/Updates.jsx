import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowUpCircle, CheckCircle2, XCircle, Loader2, RefreshCw, ExternalLink,
  Undo2, AlertTriangle, Circle, DatabaseBackup, Eye,
} from 'lucide-react'
import { get, post } from '@/lib/api'
import { formatDate } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { DataTable } from '@/components/ui/Table'
import { ProgressBar } from '@/components/ui/Progress'
import { Select } from '@/components/ui/Select'
import { Input, FormField } from '@/components/ui/Input'
import { Checkbox } from '@/components/ui/Toggle'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
  DialogBody, DialogFooter,
} from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'
import { isUpdateJobActive, useUpdateStatus } from '@/hooks/useUpdateStatus'

// The daemon-side update pipeline in display order; progress = how many
// have reported ok. "finalize" covers swap+restart+healthcheck done by the
// detached finalizer (the API briefly restarts during it).
const UPDATE_STEPS = ['preflight', 'backup', 'download', 'checksum', 'extract', 'venv', 'migrate', 'finalize']
const STEP_LABELS = {
  preflight: 'Pre-flight checks (test suite, disk space)',
  backup: 'Backup panel DB + /etc/boron',
  download: 'Download release from GitHub',
  checksum: 'Verify SHA256 checksum',
  extract: 'Validate + stage new version',
  venv: 'Build Python environment',
  migrate: 'Run database migrations',
  'mail-guard': 'Prepare mailbox recovery',
  finalize: 'Swap, restart panel services, health check',
}

function stepsFor(job) {
  if (job?.kind === 'rollback') return ['preflight', 'finalize']
  return job?.steps?.some(step => step.step === 'mail-guard')
    ? [...UPDATE_STEPS.slice(0, -1), 'mail-guard', 'finalize'] : UPDATE_STEPS
}

function StepList({ job }) {
  const byStep = {}
  for (const s of job?.steps || []) byStep[s.step] = s
  const steps = stepsFor(job)
  return (
    <ul className="space-y-1.5">
      {steps.map((name) => {
        const s = byStep[name]
        const state = s?.status // ok | running | failed | undefined
        return (
          <li key={name} className="flex items-start gap-2 text-sm">
            {state === 'ok' && <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" />}
            {state === 'failed' && <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-danger" />}
            {state === 'running' && <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-accent-600" />}
            {!state && <Circle className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground/40" />}
            <div className="min-w-0">
              <span className={state ? 'text-foreground' : 'text-muted-foreground'}>
                {STEP_LABELS[name] || name}
              </span>
              {s?.detail && state !== 'ok' && (
                <div className="break-words text-xs text-muted-foreground">{s.detail}</div>
              )}
            </div>
          </li>
        )
      })}
    </ul>
  )
}

// Confirm dialog shared by Update and Rollback: explicit confirmation plus a
// TOTP field (required by the backend when the admin has 2FA enabled --
// harmlessly ignored otherwise).
function ConfirmActionDialog({ open, onOpenChange, title, description, confirmLabel,
                               variant = 'primary', mutation, children = null,
                               buildBody = null, submitDisabled = false,
                               impactText = 'The panel services restart during this operation (~10–30s of panel downtime).' }) {
  const [code, setCode] = useState('')
  useEffect(() => {
    if (!open) setCode('')
  }, [open])
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="sm">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            mutation.mutate(buildBody
              ? buildBody({ confirm: true, totp_code: code.trim() || null })
              : { confirm: true, totp_code: code.trim() || null })
          }}
        >
          <DialogBody className="space-y-3">
            <div className="rounded-btn border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-foreground">
              <AlertTriangle className="mr-1.5 inline h-3.5 w-3.5 text-warning" />
              {impactText}
            </div>
            {children}
            <FormField label="2FA code" htmlFor="update-totp"
                       hint="Required if two-factor authentication is enabled on your admin account.">
              <Input
                id="update-totp"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={16}
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder="123456"
                className="font-mono"
              />
            </FormField>
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" variant={variant} loading={mutation.isPending}
                    disabled={submitDisabled || mutation.isPending}>
              {confirmLabel}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export default function Updates() {
  const qc = useQueryClient()
  const { data: status, isLoading, error, refetch } = useUpdateStatus()
  const [updateOpen, setUpdateOpen] = useState(false)
  const [rollbackOpen, setRollbackOpen] = useState(false)
  const [acknowledgeDataPlane, setAcknowledgeDataPlane] = useState(false)
  const [overrideTasks, setOverrideTasks] = useState(false)
  const [restoreBackup, setRestoreBackup] = useState(null)
  const [detailJob, setDetailJob] = useState(null)

  const { data: preflight, isLoading: preflightLoading, error: preflightError } = useQuery({
    queryKey: ['update-preflight', overrideTasks],
    queryFn: () => get(`/api/v1/admin/update/preflight?override_active_tasks=${overrideTasks ? 'true' : 'false'}`),
    enabled: updateOpen,
    refetchOnWindowFocus: false,
  })

  const { data: databaseBackups } = useQuery({
    queryKey: ['update-database-backups'],
    queryFn: () => get('/api/v1/admin/update/database-backups'),
    refetchInterval: 10000,
  })

  const { data: historyData } = useQuery({
    queryKey: ['update-history'],
    queryFn: () => get('/api/v1/admin/update/history'),
    refetchInterval: isUpdateJobActive(status) ? 5000 : false,
  })

  const channelMut = useMutation({
    mutationFn: (channel) => post('/api/v1/admin/update/channel', { channel }),
    onSuccess: (data) => {
      qc.setQueryData(['update-status'], (previous) => ({
        ...previous, channel: data.channel, branch: data.branch,
        latest_version: null, update_available: false, checked_at: null, error: null, release_availability: null,
      }))
      qc.invalidateQueries({ queryKey: ['update-status'] })
      toast.success('Update tier saved', 'Only signed releases from this tier will be offered.')
    },
    onError: (e) => toast.error('Could not change update tier', e.message),
  })

  const checkMut = useMutation({
    mutationFn: () => post('/api/v1/admin/update/check'),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['update-status'] })
      if (data.error) toast.error('Update check failed', data.error)
      else if (data.release_availability === 'unpublished') toast.info('No release published yet', `The ${data.channel} tier is selected. Your current installation is unchanged.`)
      else if (data.update_available) toast.info('Update available', `v${data.latest_version} can be installed.`)
      else toast.success('Up to date', `v${data.current_version} is the latest release.`)
    },
    onError: (e) => toast.error('Update check failed', e.message),
  })

  const startMut = useMutation({
    mutationFn: (body) => post('/api/v1/admin/update/start', body),
    onSuccess: () => {
      setUpdateOpen(false)
      toast.info('Update started', 'Progress is shown below. The panel restarts near the end.')
      qc.invalidateQueries({ queryKey: ['update-status'] })
      qc.invalidateQueries({ queryKey: ['update-history'] })
    },
    onError: (e) => toast.error('Could not start update', e.message),
  })

  const rollbackMut = useMutation({
    mutationFn: (body) => post('/api/v1/admin/update/rollback', body),
    onSuccess: () => {
      setRollbackOpen(false)
      toast.info('Rollback started', 'The panel restarts on the previous version shortly.')
      qc.invalidateQueries({ queryKey: ['update-status'] })
      qc.invalidateQueries({ queryKey: ['update-history'] })
    },
    onError: (e) => toast.error('Could not start rollback', e.message),
  })

  const restoreMut = useMutation({
    mutationFn: (body) => post('/api/v1/admin/update/database-restore', body),
    onSuccess: () => {
      setRestoreBackup(null)
      toast.info('Database restore started', 'The panel will reconnect after the restored database passes its health check.')
      qc.invalidateQueries({ queryKey: ['update-database-backups'] })
    },
    onError: (e) => toast.error('Could not restore panel database', e.message),
  })

  useEffect(() => {
    if (!updateOpen) {
      setAcknowledgeDataPlane(false)
      setOverrideTasks(false)
    }
  }, [updateOpen])

  // Terminal-state toast: fire once when the active job disappears.
  const lastActiveId = useRef(null)
  useEffect(() => {
    const active = status?.active_job
    if (active) {
      lastActiveId.current = active.id
      return
    }
    const last = status?.last_job
    if (lastActiveId.current != null && last?.id === lastActiveId.current) {
      lastActiveId.current = null
      if (last.status === 'completed') {
        toast.success(
          last.kind === 'rollback' ? 'Rollback complete' : 'Update complete',
          `Panel is now on v${last.to_version}.`,
        )
        qc.invalidateQueries({ queryKey: ['panel-version'] })
      } else if (last.status === 'failed') {
        toast.error(
          last.kind === 'rollback' ? 'Rollback failed' : 'Update failed',
          last.rolled_back ? 'The panel was automatically restored to the previous version.' : last.error,
        )
      }
      qc.invalidateQueries({ queryKey: ['update-history'] })
    }
  }, [status, qc])

  const active = status?.active_job
  const last = status?.last_job
  const jobShown = active || null
  const okSteps = (jobShown?.steps || []).filter((s) => s.status === 'ok').length
  const totalSteps = stepsFor(jobShown).length
  const finalizing = jobShown?.status === 'finalizing'
  const dataPlaneReloads = preflight?.release_metadata?.data_plane_reloads
    || status?.release_metadata?.data_plane_reloads || []
  const preflightBlocked = Boolean(preflight?.critical_failures?.length)
  const acknowledgementRequired = dataPlaneReloads.length > 0

  const historyColumns = [
    { key: 'id', header: '#', sortable: true, render: (r) => r.id },
    { key: 'kind', header: 'Type', render: (r) => (r.kind === 'rollback' ? 'Rollback' : 'Update') },
    {
      key: 'versions', header: 'Versions',
      render: (r) => <span className="font-mono text-xs">{r.from_version} → {r.to_version}</span>,
    },
    {
      key: 'status', header: 'Result',
      render: (r) => (
        <span className="flex items-center gap-1.5">
          <StatusBadge status={r.status} />
          {r.rolled_back && r.kind !== 'rollback' && (
            <Badge variant="warning">rolled back</Badge>
          )}
        </span>
      ),
    },
    { key: 'initiated_by', header: 'By', render: (r) => r.initiated_by },
    { key: 'started_at', header: 'Started', sortable: true, render: (r) => (r.started_at ? formatDate(r.started_at) : '—') },
    {
      key: 'duration_seconds', header: 'Duration',
      render: (r) => (r.duration_seconds != null ? `${Math.round(r.duration_seconds)}s` : '—'),
    },
    {
      key: 'evidence', header: 'Evidence',
      render: (r) => (
        <Button size="sm" variant="secondary" onClick={() => setDetailJob(r)}>
          <Eye className="h-4 w-4" /> Details
        </Button>
      ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="Updates"
        description="Panel version, one-click updates and rollback."
        icon={ArrowUpCircle}
        primaryAction={<Button onClick={() => checkMut.mutate()} loading={checkMut.isPending}
                disabled={Boolean(active)}>
          <RefreshCw className="h-4 w-4" /> Check now
        </Button>}
      />

      <Card className="mb-6">
        <CardContent className="py-5">
          <FormField label="Update tier" htmlFor="update-tier">
            <Select id="update-tier" className="max-w-sm" value={status?.channel || 'stable'}
              disabled={isLoading || Boolean(error) || Boolean(active) || channelMut.isPending}
              onChange={(event) => channelMut.mutate(event.target.value)}>
              <option value="stable">Stable — recommended</option>
              <option value="beta">Beta — preview releases</option>
              <option value="development">Development — early testing</option>
            </Select>
          </FormField>
          <p className="mt-2 text-sm text-muted-foreground">
            Stable follows master; Beta follows beta; Development follows development.
            All tiers require signed release packages. Switching tiers does not install or downgrade anything.
            Beta and Development can contain unfinished changes.
          </p>
          {channelMut.isPending && <p role="status">Saving update tier…</p>}
        </CardContent>
      </Card>

      {/* Version / availability card */}
      <Card className="mb-6">
        <CardContent className="py-5">
          {isLoading ? (
            <div className="text-sm text-muted-foreground">Loading update status…</div>
          ) : error ? (
            <div className="text-sm text-danger">Could not load update status: {error.message}</div>
          ) : !status?.configured ? (
            <div className="text-sm text-muted-foreground">
              <div className="mb-1 font-medium text-foreground">Update checks are not configured.</div>
              Set <code className="font-mono text-xs">update_github_repo = "owner/repo"</code> in{' '}
              <code className="font-mono text-xs">/etc/boron/boron.toml</code> and restart the
              daemon to enable release checks against GitHub.
            </div>
          ) : (
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div className="flex flex-wrap items-center gap-8">
                <div>
                  <div className="text-xs uppercase tracking-wide text-muted-foreground">Current version</div>
                  <div className="text-2xl font-semibold text-foreground">v{status.current_version}</div>
                </div>
                <div>
                  <div className="text-xs uppercase tracking-wide text-muted-foreground">Latest release</div>
                  <div className="flex items-center gap-2 text-2xl font-semibold text-foreground">
                    {status.latest_version ? `v${status.latest_version}` : '—'}
                    {status.update_available ? (
                      <Badge variant="accent">Update available</Badge>
                    ) : status.latest_version ? (
                      <Badge variant="success">Up to date</Badge>
                    ) : null}
                  </div>
                </div>
                {status.checked_at && (
                  <div>
                    <div className="text-xs uppercase tracking-wide text-muted-foreground">Last checked</div>
                    <div className="text-sm text-foreground">{formatDate(status.checked_at)}</div>
                  </div>
                )}
              </div>
              <div className="flex items-center gap-2">
                {status.changelog_url && (
                  <Button variant="outline" asChild>
                    <a href={status.changelog_url} target="_blank" rel="noopener noreferrer">
                      <ExternalLink className="h-4 w-4" /> Changelog
                    </a>
                  </Button>
                )}
                {status.rollback_available && (
                  <Button variant="outline" onClick={() => setRollbackOpen(true)} disabled={Boolean(active)}>
                    <Undo2 className="h-4 w-4" /> Roll back to v{status.rollback_to}
                  </Button>
                )}
                <Button onClick={() => setUpdateOpen(true)}
                        disabled={!status.update_available || Boolean(active)}>
                  <ArrowUpCircle className="h-4 w-4" /> {status.update_available ? `Update to v${status.latest_version}` : status.release_availability === 'unpublished' ? 'No release published yet' : 'Panel is up to date'}
                </Button>
              </div>
            </div>
          )}
          {status?.release_availability === 'unpublished' && (
            <p role="status" className="mt-3 text-sm text-muted-foreground">
              No {status.channel === 'development' ? 'Development' : 'Beta'} release published yet. Your tier is saved; your current installation is unchanged. Check again after a signed release is published for this tier.
            </p>
          )}
          {status?.error && (
            <div className="mt-3 text-xs text-danger">Last check error: {status.error}</div>
          )}
          {status?.rollback_blocked_reason && (
            <div className="mt-3 text-sm text-warning">
              Rollback unavailable: {status.rollback_blocked_reason}
            </div>
          )}
          {(status?.release_metadata?.data_plane_reloads || []).length > 0 && (
            <div className="mt-3 text-sm text-warning">
              This release declares reloads for: {status.release_metadata.data_plane_reloads.join(', ')}.
              You will review and acknowledge them before installation.
            </div>
          )}
        </CardContent>
      </Card>

      {/* Live progress */}
      {jobShown && (
        <Card className="mb-6 border-accent/40">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Loader2 className="h-4 w-4 animate-spin text-accent-600" />
              {jobShown.kind === 'rollback' ? 'Rollback' : 'Update'} in progress:{' '}
              v{jobShown.from_version} → v{jobShown.to_version}
            </CardTitle>
            <CardDescription>
              Started {jobShown.started_at ? formatDate(jobShown.started_at) : ''} by {jobShown.initiated_by}.
              {finalizing && ' The panel services are restarting — brief disconnects here are expected.'}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <ProgressBar value={(okSteps / totalSteps) * 100} color="bg-accent" showTrack />
            <StepList job={jobShown} />
          </CardContent>
        </Card>
      )}

      {/* Last failed job surfaced when idle */}
      {!jobShown && last?.status === 'failed' && (
        <Card className="mb-6 border-danger/40">
          <CardContent className="py-4">
            <div className="flex items-start gap-3">
              <XCircle className="mt-0.5 h-5 w-5 shrink-0 text-danger" />
              <div className="min-w-0 text-sm">
                <div className="font-medium text-foreground">
                  Last {last.kind === 'rollback' ? 'rollback' : 'update'} (v{last.from_version} → v{last.to_version}) failed
                  {last.rolled_back && ' — the panel was automatically restored to the previous version'}
                </div>
                <div className="mt-1 break-words text-muted-foreground">{last.error}</div>
                <div className="mt-1 text-xs text-muted-foreground">
                  Full step log: <code className="font-mono">/var/log/boron/updates.log</code>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      <Card className="mb-6">
        <CardHeader>
          <CardTitle>Panel database recovery</CardTitle>
          <CardDescription>
            Restore a database from a validated pre-update snapshot. The current database is preserved as a rescue copy first.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {databaseBackups?.restore_status && (
            <div className="rounded-btn border border-border bg-muted/30 px-3 py-2 text-sm text-foreground">
              Latest restore: <strong>{databaseBackups.restore_status.state}</strong>
              {databaseBackups.restore_status.source && ` · ${databaseBackups.restore_status.source}`}
              {databaseBackups.restore_status.error && (
                <div className="mt-1 text-danger">{databaseBackups.restore_status.error}</div>
              )}
            </div>
          )}
          {(databaseBackups?.backups || []).length === 0 ? (
            <div className="text-sm text-muted-foreground">No pre-update database snapshots are available.</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead><tr className="border-b border-border text-muted-foreground">
                  <th className="px-2 py-2 font-medium">Snapshot</th>
                  <th className="px-2 py-2 font-medium">Created</th>
                  <th className="px-2 py-2 font-medium">Validation</th>
                  <th className="sticky right-0 bg-surface px-2 py-2 text-right font-medium">Action</th>
                </tr></thead>
                <tbody>{databaseBackups.backups.map((backup) => (
                  <tr key={backup.id} className="border-b border-border/70">
                    <td className="px-2 py-2 font-mono text-xs">{backup.id}</td>
                    <td className="px-2 py-2">{formatDate(backup.created_at)}</td>
                    <td className="px-2 py-2">
                      {backup.integrity_ok && backup.schema_compatible
                        ? <Badge variant="success">Ready</Badge>
                        : <span className="text-danger">{backup.compatibility_reason || 'Invalid snapshot'}</span>}
                    </td>
                    <td className="sticky right-0 bg-surface px-2 py-2 text-right">
                      {backup.integrity_ok && backup.schema_compatible && (
                        <Button size="sm" variant="secondary" onClick={() => setRestoreBackup(backup)}>
                          <DatabaseBackup className="h-4 w-4" /> Restore database
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* History */}
      <Card>
        <CardHeader>
          <CardTitle>Update history</CardTitle>
          <CardDescription>Every update and rollback attempt on this server.</CardDescription>
        </CardHeader>
        <CardContent>
          <DataTable
            columns={historyColumns}
            data={historyData?.jobs || []}
            getRowKey={(r) => r.id}
            pageSize={10}
            emptyTitle="No updates yet"
            emptyDescription="Updates applied from this page will appear here."
          />
        </CardContent>
      </Card>

      <ConfirmActionDialog
        open={updateOpen}
        onOpenChange={setUpdateOpen}
        title={`Update to v${status?.latest_version}`}
        description={`The panel updates from v${status?.current_version} to v${status?.latest_version}. A full backup of the panel database and configuration is taken first, and a failed update rolls back automatically.`}
        confirmLabel="Start update"
        mutation={startMut}
        buildBody={(body) => ({
          ...body,
          acknowledge_data_plane: acknowledgeDataPlane,
          override_active_tasks: overrideTasks,
        })}
        submitDisabled={preflightLoading || Boolean(preflightError) || preflightBlocked
          || (acknowledgementRequired && !acknowledgeDataPlane)}
        impactText={acknowledgementRequired
          ? `The panel restarts and this release also reloads: ${dataPlaneReloads.join(', ')}.`
          : 'The Boron API and root helper restart during this operation (~10–30s of panel downtime).'}
      >
        <div className="space-y-2">
          <div className="text-sm font-medium text-foreground">Pre-flight checks</div>
          {preflightLoading && <div className="text-sm text-muted-foreground">Checking the server…</div>}
          {preflightError && <div className="text-sm text-danger">Pre-flight checks could not run: {preflightError.message}</div>}
          {(preflight?.checks || []).map((check) => (
            <div key={check.id} className="flex items-start gap-2 text-sm">
              {check.status === 'pass'
                ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" />
                : <AlertTriangle className={`mt-0.5 h-4 w-4 shrink-0 ${check.critical ? 'text-danger' : 'text-warning'}`} />}
              <div><span className="font-medium text-foreground">{check.id.replaceAll('_', ' ')}</span>
                <div className="text-xs text-muted-foreground">{check.detail}</div></div>
            </div>
          ))}
          {preflight?.override_required && (
            <label className="flex items-start gap-2 text-sm text-foreground">
              <Checkbox checked={overrideTasks} onCheckedChange={setOverrideTasks} />
              <span>Override {preflight.active_tasks.length} active task(s). I understand they may fail.</span>
            </label>
          )}
          {acknowledgementRequired && (
            <label className="flex items-start gap-2 text-sm text-foreground">
              <Checkbox checked={acknowledgeDataPlane} onCheckedChange={setAcknowledgeDataPlane} />
              <span>I acknowledge the declared service reloads: {dataPlaneReloads.join(', ')}.</span>
            </label>
          )}
        </div>
      </ConfirmActionDialog>
      <ConfirmActionDialog
        open={rollbackOpen}
        onOpenChange={setRollbackOpen}
        title={`Roll back to v${status?.rollback_to}`}
        description={`The panel returns to the previous version directory (v${status?.rollback_to}). Boron verified that release against the current database schema before offering this action.`}
        confirmLabel="Roll back"
        variant="danger"
        mutation={rollbackMut}
      />
      <ConfirmActionDialog
        open={Boolean(restoreBackup)}
        onOpenChange={(open) => { if (!open) setRestoreBackup(null) }}
        title="Restore panel database"
        description={`Restore ${restoreBackup?.id || ''}. Boron preserves the current database and automatically restores it if the recovered panel fails its health check.`}
        confirmLabel="Restore database"
        variant="danger"
        mutation={restoreMut}
        buildBody={(body) => ({ ...body, backup_id: restoreBackup?.id })}
        impactText="The Boron API and root helper restart. Hosted websites, mail, DNS and databases are not restarted."
      />
      <Dialog open={Boolean(detailJob)} onOpenChange={(open) => { if (!open) setDetailJob(null) }}>
        <DialogContent size="lg">
          <DialogHeader>
            <DialogTitle>Update evidence</DialogTitle>
            <DialogDescription>
              Redacted pre-flight, readiness and host-configuration evidence for update #{detailJob?.id}.
            </DialogDescription>
          </DialogHeader>
          <DialogBody className="space-y-4">
            <section><h3 className="text-sm font-semibold text-foreground">Advisory checks</h3>
              {(detailJob?.advisories || []).length
                ? <ul className="mt-2 space-y-1 text-sm text-muted-foreground">{detailJob.advisories.map((item, index) => (
                  <li key={`${item.id}-${index}`}>{item.label || item.id}: {item.explanation || item.detail || item.status}</li>
                ))}</ul>
                : <p className="mt-1 text-sm text-muted-foreground">No advisory failures were recorded.</p>}
            </section>
            <section><h3 className="text-sm font-semibold text-foreground">Runtime configuration changes</h3>
              {(detailJob?.runtime_changes || []).length
                ? <div className="mt-2 space-y-3">{detailJob.runtime_changes.map((item, index) => (
                  <div key={`${item.path}-${index}`} className="border-t border-border pt-2">
                    <div className="break-all font-mono text-xs text-foreground">{item.path}</div>
                    {item.diff
                      ? <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap text-xs text-muted-foreground">{item.diff}</pre>
                      : <div className="mt-1 text-xs text-muted-foreground">Content hidden; hashes retained for verification.</div>}
                  </div>
                ))}</div>
                : <p className="mt-1 text-sm text-muted-foreground">No runtime files were changed.</p>}
            </section>
          </DialogBody>
          <DialogFooter><Button variant="secondary" onClick={() => setDetailJob(null)}>Close</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
