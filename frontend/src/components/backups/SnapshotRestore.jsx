import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { RotateCcw, ShieldCheck } from 'lucide-react'
import { get, post } from '@/lib/api'
import { formatBytes, formatDate } from '@/lib/utils'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { DataTable } from '@/components/ui/Table'
import { Badge } from '@/components/ui/Badge'
import { ConfirmDialog } from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'

export function SnapshotFileRestore({ username, run, paths, onPathsChange }) {
  const [open,setOpen]=useState(false)
  const [confirmation,setConfirmation]=useState('')
  const qc=useQueryClient()
  useEffect(()=>{if(paths)setOpen(true)},[paths])
  const restore=useMutation({mutationFn:()=>post(`/api/v1/accounts/${encodeURIComponent(username)}/backups/snapshots/runs/${run.id}/restore`,{kind:'files',confirmation,paths:paths.split('\n').map(p=>p.trim()).filter(Boolean)}),onSuccess:()=>{toast.success('Restore queued','Progress appears in restore history.');setConfirmation('');setOpen(false);onPathsChange('');qc.invalidateQueries({queryKey:['snapshot-restores',username]})}})
  if(!username||!run.snapshot_id||run.status==='expired'||!run.options?.components?.includes('files'))return null
  return <section className="space-y-3 border-t border-border pt-4">
    <Button variant="secondary" onClick={()=>setOpen(v=>!v)}><RotateCcw className="h-4 w-4"/>Restore account files</Button>
    {open&&<form className="space-y-4 rounded-btn border border-border bg-muted/20 p-4" onSubmit={e=>{e.preventDefault();restore.mutate()}}>
      <div><h3 className="font-semibold">Restore from recovery point #{run.id}</h3><p className="mt-1 text-sm text-muted-foreground">Matching files will be replaced with their backed-up versions. Other files stay in place. Databases and email are not changed by this file restore.</p></div>
      <p className="flex gap-2 text-sm text-muted-foreground"><ShieldCheck className="mt-0.5 h-4 w-4 shrink-0"/>Current files are backed up first. Restore history lets you recover their previous versions; newly restored files are retained.</p>
      <FormField label="Selected files or folders" htmlFor="snapshot-restore-paths" hint="One path per line, relative to your account home. Leave empty to restore all account files captured by this recovery point, or select items in the browser above."><textarea id="snapshot-restore-paths" value={paths} onChange={e=>onPathsChange(e.target.value)} className="min-h-20 w-full rounded-btn border border-border bg-background p-3 text-sm" placeholder={'public_html/wp-config.php\npublic_html/wp-content/themes'}/></FormField>
      <FormField label={`Type ${username} to confirm`} htmlFor="snapshot-restore-confirm"><Input id="snapshot-restore-confirm" value={confirmation} onChange={e=>setConfirmation(e.target.value)} autoComplete="off" spellCheck={false}/></FormField>
      {restore.error&&<p role="alert" className="text-sm text-danger">{restore.error.message}</p>}
      <div className="flex flex-wrap gap-2"><Button type="submit" loading={restore.isPending} disabled={confirmation!==username}>Restore selected files</Button><Button type="button" variant="ghost" disabled={restore.isPending} onClick={()=>setOpen(false)}>Cancel</Button></div>
    </form>}
  </section>
}

export function SnapshotDatabaseRestore({ username, run }) {
  const [open,setOpen]=useState(false)
  const [selected,setSelected]=useState([])
  const [confirmation,setConfirmation]=useState('')
  const qc=useQueryClient()
  const base=`/api/v1/accounts/${encodeURIComponent(username)}/backups/snapshots/runs/${run.id}`
  const catalog=useQuery({queryKey:['snapshot-databases',username,run.id],queryFn:()=>get(`${base}/databases`),enabled:open&&!!username})
  const available=catalog.data?.databases||[]
  const restore=useMutation({mutationFn:()=>post(`${base}/restore`,{kind:'databases',databases:selected,confirmation}),onSuccess:()=>{
    toast.success('Database restore queued','Progress appears in restore history.');setOpen(false);setSelected([]);setConfirmation('');qc.invalidateQueries({queryKey:['snapshot-restores',username]})
  }})
  if(!username||!run.snapshot_id||run.status==='expired'||!run.options?.components?.includes('databases'))return null
  return <section className="space-y-3 border-t border-border pt-4">
    <Button variant="secondary" onClick={()=>setOpen(v=>!v)}><RotateCcw className="h-4 w-4"/>Restore databases</Button>
    {open&&<form aria-label="Database restore" className="space-y-4 rounded-btn border border-border bg-muted/20 p-4" onSubmit={e=>{e.preventDefault();restore.mutate()}}>
      <div><h3 className="font-semibold">Choose databases to restore</h3><p className="mt-1 text-sm text-muted-foreground">All current tables in the selected databases will be replaced by the backup, including removal of tables created afterward. Database users and passwords remain unchanged.</p></div>
      <p className="flex gap-2 text-sm text-muted-foreground"><ShieldCheck className="mt-0.5 h-4 w-4 shrink-0"/>A recovery copy of each selected database is saved first. Pause writes to your website while restoring to avoid losing new changes.</p>
      {catalog.isLoading?<p role="status">Loading backed-up databases…</p>:catalog.error?<div role="alert"><p className="text-sm text-danger">{catalog.error.message}</p><Button type="button" variant="secondary" onClick={()=>catalog.refetch()}>Try again</Button></div>:available.length?<fieldset disabled={restore.isPending} className="space-y-2"><legend className="mb-2 text-sm font-medium">Databases in this recovery point</legend>{available.map(db=><label key={db.name} className="flex items-start gap-3 rounded-btn border border-border bg-background p-3"><input type="checkbox" className="mt-1 h-4 w-4 accent-accent" checked={selected.includes(db.name)} disabled={!db.available} onChange={e=>setSelected(names=>e.target.checked?[...names,db.name]:names.filter(n=>n!==db.name))}/><span className="min-w-0 flex-1"><span className="block break-all text-sm font-medium">{db.name}</span><span className="text-xs text-muted-foreground">{formatBytes(db.size)}{db.reason?` · ${db.reason}`:''}</span></span></label>)}</fieldset>:<p className="text-sm text-muted-foreground">No database exports were captured in this recovery point.</p>}
      <FormField label={`Type ${username} to confirm database restore`} htmlFor="snapshot-database-confirm"><Input id="snapshot-database-confirm" value={confirmation} onChange={e=>setConfirmation(e.target.value)} autoComplete="off" spellCheck={false}/></FormField>
      {restore.error&&<p role="alert" className="text-sm text-danger">{restore.error.message}</p>}
      <div className="flex flex-wrap gap-2"><Button type="submit" loading={restore.isPending} disabled={confirmation!==username||!selected.length||catalog.isFetching||!!catalog.error}>Restore selected databases</Button><Button type="button" variant="ghost" disabled={restore.isPending} onClick={()=>setOpen(false)}>Cancel</Button></div>
    </form>}
  </section>
}

export function SnapshotRestoreHistory({ username }) {
  const qc=useQueryClient()
  const [undo,setUndo]=useState(null)
  const base=`/api/v1/accounts/${encodeURIComponent(username)}/backups/snapshots`
  const history=useQuery({queryKey:['snapshot-restores',username],queryFn:()=>get(`${base}/restores`),enabled:!!username,refetchInterval:q=>(q.state.data?.restores||[]).some(r=>['pending','running'].includes(r.status))?2000:false})
  const reversal=useMutation({mutationFn:()=>post(`${base}/restores/${undo.id}/undo`,{confirmation:username}),onSuccess:()=>{toast.success('Previous-version recovery queued');setUndo(null);qc.invalidateQueries({queryKey:['snapshot-restores',username]})},onError:e=>toast.error('Could not recover previous version',e.message)})
  if(!username)return null
  return <section className="mt-5 space-y-3"><h3 className="text-sm font-semibold">Restore history</h3><DataTable loading={history.isLoading} error={history.error} onRetry={history.refetch} data={history.data?.restores} pageSize={5} emptyTitle="No restores yet" columns={[
    {key:'id',header:'Restore',render:r=>`#${r.id}`},
    {key:'kind',header:'Contents',render:r=>r.selection?.kind==='databases'?'Databases':'Files'},
    {key:'started_at',header:'Started',render:r=>formatDate(r.started_at)},
    {key:'status',header:'Status',render:r=><Badge variant={r.status==='completed'?'success':r.status==='failed'?'danger':'neutral'}>{r.status}</Badge>},
    {key:'progress_message',header:'Progress',render:r=><div className="text-sm"><p>{r.progress_message}</p>{r.summary?.safety_snapshot_expired&&<p className="text-muted-foreground">The previous-version copy expired under this backup job’s retention policy.</p>}{r.error&&<p className="text-danger">{r.error}</p>}{r.status==='failed'&&r.safety_snapshot_id&&<p className="mt-1 text-muted-foreground">Some selected data may have changed. Use the recovery copy to recover the previous version.</p>}</div>},
    {key:'actions',header:'Recovery',render:r=>r.safety_snapshot_id&&!['pending','running'].includes(r.status)?<Button size="sm" variant="secondary" onClick={()=>setUndo(r)}><RotateCcw className="h-3.5 w-3.5"/>{r.selection?.kind==='databases'?'Recover previous databases':'Recover previous files'}</Button>:null},
  ]}/><ConfirmDialog open={!!undo} onOpenChange={open=>!open&&setUndo(null)} title={undo?.selection?.kind==='databases'?'Recover previous database versions?':'Recover previous file versions?'} description={undo?.selection?.kind==='databases'?'Import the database versions saved immediately before this operation. Current tables will be replaced, including removal of tables created since that recovery copy.':'Restore the file versions saved immediately before this operation. Newer changes to those files will be replaced. Files that had no previous version are retained.'} confirmationText={username} confirmLabel={undo?.selection?.kind==='databases'?'Recover previous databases':'Recover previous files'} loading={reversal.isPending} onConfirm={()=>reversal.mutate()}/></section>
}
