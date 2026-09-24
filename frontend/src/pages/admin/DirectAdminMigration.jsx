import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { post } from '@/lib/api'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { toast } from '@/components/ui/Toast'

const initial = { mode: 'admin', host: '', port: 2222, login: 'admin', password: '', host_key: '' }

export default function DirectAdminMigration({ onClose, onQueued }) {
  const qc = useQueryClient()
  const [connection, setConnection] = useState(initial)
  const [preview, setPreview] = useState(null)
  const [selected, setSelected] = useState({})
  const [compatibility, setCompatibility] = useState('strict')
  const [queued, setQueued] = useState([])
  const [failures, setFailures] = useState([])
  const change = (name, value) => {
    setConnection(current => ({ ...current, [name]: value }))
    setPreview(null)
    setSelected({})
  }
  const inspect = useMutation({
    mutationFn: () => post('/api/v1/admin/import/accounts/directadmin/inspect', connection),
    onSuccess: result => { setPreview(result); setFailures([]) },
    onError: error => toast.error('Could not connect', error.message),
  })
  const migrate = useMutation({
    mutationFn: async () => {
      const errors = [], jobs = []
      for (const [remote_user, username] of Object.entries(selected)) {
        try {
          const job = await post('/api/v1/admin/import/accounts/directadmin/migrate', {
            remote: connection, remote_user, username, db_compatibility: compatibility,
          })
          jobs.push(job)
          setSelected(current => { const next = { ...current }; delete next[remote_user]; return next })
        } catch (error) { errors.push(`${remote_user}: ${error.message}`) }
      }
      return { jobs, errors }
    },
    onSuccess: ({ jobs, errors }) => {
      setQueued(current => [...current, ...jobs])
      setFailures(errors)
      qc.invalidateQueries({ queryKey: ['account-imports'] })
      if (jobs.length) toast.success(`${jobs.length} migration(s) queued`, 'Follow progress in the migration history.')
      if (!errors.length) {
        setConnection(current => ({ ...current, password: '' }))
        setPreview(null)
        if (jobs.length) onQueued?.(jobs[0])
      }
    },
  })
  const busy = inspect.isPending || migrate.isPending
  const selectedNames = Object.values(selected)
  const valid = selectedNames.length > 0 && new Set(selectedNames).size === selectedNames.length && selectedNames.every(name => /^[a-z][a-z0-9]{0,15}$/.test(name))

  return <section className="mb-6 rounded-panel border border-border bg-card p-5 space-y-5" aria-label="DirectAdmin server migration">
    <div className="flex items-center justify-between gap-4"><div><h2 className="text-lg font-semibold">Import from a DirectAdmin server</h2><p className="text-sm text-muted-foreground">Connect, select accounts, then let Boron create and transfer their backups.</p></div><Button variant="secondary" onClick={onClose} disabled={busy}>Close</Button></div>
    <form autoComplete="off" data-bwignore="true" onSubmit={event => { event.preventDefault(); inspect.mutate() }}>
      <fieldset disabled={busy} className="grid gap-4 md:grid-cols-2">
        <FormField label="Connection method" htmlFor="da-mode"><Select id="da-mode" value={connection.mode} onChange={event => { setConnection(current => ({ ...current, mode: event.target.value, port: event.target.value === 'root' ? 22 : 2222 })); setPreview(null); setSelected({}) }}><option value="admin">DirectAdmin administrator · HTTPS</option><option value="root">Server root · SSH</option></Select></FormField>
        <FormField label="Source server" htmlFor="da-host" hint={connection.mode === 'admin' ? 'Use the hostname covered by the source TLS certificate.' : 'Public hostname or IPv4 address.'}><Input id="da-host" value={connection.host} onChange={event => change('host', event.target.value)} placeholder="server.example.com" required autoComplete="off" /></FormField>
        <FormField label="Port" htmlFor="da-port"><Input id="da-port" type="number" min="1" max="65535" value={connection.port} onChange={event => change('port', Number(event.target.value))} required /></FormField>
        {connection.mode === 'admin' && <FormField label="DirectAdmin administrator" htmlFor="da-login"><Input id="da-login" value={connection.login} onChange={event => change('login', event.target.value)} required autoComplete="off" /></FormField>}
        <FormField label={connection.mode === 'root' ? 'Root SSH password' : 'Admin password or login key'} htmlFor="da-password"><Input id="da-password" data-bwignore="true" data-1p-ignore="true" data-lpignore="true" type="password" value={connection.password} onChange={event => change('password', event.target.value)} required autoComplete="off" /></FormField>
        {connection.mode === 'root' && <FormField label="SSH host-key fingerprint" htmlFor="da-host-key" hint="Obtain the SHA256 fingerprint from your source server console (ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub)."><Input id="da-host-key" value={connection.host_key} onChange={event => change('host_key', event.target.value)} placeholder="SHA256:…" required autoComplete="off" /></FormField>}
      </fieldset>
      <p className="mt-3 text-sm text-muted-foreground">Passwords are used for this migration only and are not saved. Restarting Boron interrupts transfers; reconnect to retry. Source accounts and backups are retained.</p>
      <Button className="mt-4" type="submit" loading={inspect.isPending} disabled={busy}>Connect and list accounts</Button>
    </form>
    {preview && <div className="space-y-4">
      <p className="font-medium">Destination database: {preview.destination_database}</p>
      <ul className="list-disc pl-5 text-sm text-muted-foreground space-y-1">{preview.notes.map(note => <li key={note}>{note}</li>)}</ul>
      <div className="flex gap-3"><Button variant="secondary" disabled={busy} onClick={() => setSelected(Object.fromEntries(preview.accounts.filter(row => row.available).map(row => [row.username, row.username])))}>Select available</Button><Button variant="secondary" disabled={busy} onClick={() => setSelected({})}>Clear selection</Button></div>
      <div className="max-h-80 overflow-auto rounded-btn border border-border"><table className="w-full text-left"><thead className="bg-muted"><tr><th className="p-3">Source account</th><th className="p-3">New Boron username</th></tr></thead><tbody>{preview.accounts.map(row => <tr key={row.username} className="border-t border-border"><td className="p-3"><label className="flex items-center gap-2"><input type="checkbox" disabled={busy} checked={Object.hasOwn(selected, row.username)} onChange={event => setSelected(current => { const next = { ...current }; if (event.target.checked) next[row.username] = row.available ? row.username : ''; else delete next[row.username]; return next })} />{row.username}</label>{!row.available && <span className="text-sm text-muted-foreground">Destination name is occupied or has an active import; choose a different name.</span>}</td><td className="p-3">{Object.hasOwn(selected, row.username) && <Input aria-label={`Destination username for ${row.username}`} disabled={busy} value={selected[row.username]} onChange={event => setSelected(current => ({ ...current, [row.username]: event.target.value }))} />}</td></tr>)}</tbody></table>{!preview.accounts.length && <p className="p-4">No source accounts found.</p>}</div>
      <FormField label="Database compatibility" htmlFor="da-compat"><Select id="da-compat" disabled={busy} value={compatibility} onChange={event => setCompatibility(event.target.value)}><option value="strict">Strict — stop before account creation on detected incompatibilities</option><option value="adapt">Allow supported collation mappings and destination object definers</option></Select></FormField>
      {compatibility === 'adapt' && <p className="rounded-btn border border-warning/40 bg-warning/10 p-3 text-sm">Collation conversions can change sorting and unique-key comparisons. Views, routines and triggers will use the destination database user. Review the per-database report and verify your applications before changing DNS. Other unsupported SQL is never silently skipped.</p>}
      <Button onClick={() => migrate.mutate()} disabled={!valid || busy} loading={migrate.isPending}>Import {selectedNames.length || ''} selected account{selectedNames.length === 1 ? '' : 's'}</Button>
    </div>}
    {!!failures.length && <div role="alert" className="text-danger space-y-1">{failures.map(message => <p key={message}>{message}</p>)}</div>}
    {!!queued.length && <p role="status">Submitted: {queued.map(job => `${job.username} (#${job.id})`).join(', ')}. Check current status in the migration history below. To retry a failed import, re-enter the source password, connect again and select the account.</p>}
  </section>
}
