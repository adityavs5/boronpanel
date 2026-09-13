import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { RotateCcw, ShieldCheck } from 'lucide-react'
import { get, post } from '@/lib/api'
import { formatDate } from '@/lib/utils'
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

export function SnapshotRestoreHistory({ username }) {
  const qc=useQueryClient()
  const [undo,setUndo]=useState(null)
  const base=`/api/v1/accounts/${encodeURIComponent(username)}/backups/snapshots`
  const history=useQuery({queryKey:['snapshot-restores',username],queryFn:()=>get(`${base}/restores`),enabled:!!username,refetchInterval:q=>(q.state.data?.restores||[]).some(r=>['pending','running'].includes(r.status))?2000:false})
  const reversal=useMutation({mutationFn:()=>post(`${base}/restores/${undo.id}/undo`,{confirmation:username}),onSuccess:()=>{toast.success('Previous-file recovery queued');setUndo(null);qc.invalidateQueries({queryKey:['snapshot-restores',username]})},onError:e=>toast.error('Could not recover previous files',e.message)})
  if(!username)return null
  return <section className="mt-5 space-y-3"><h3 className="text-sm font-semibold">File restore history</h3><DataTable loading={history.isLoading} error={history.error} onRetry={history.refetch} data={history.data?.restores} pageSize={5} emptyTitle="No file restores yet" columns={[
    {key:'id',header:'Restore',render:r=>`#${r.id}`},
    {key:'started_at',header:'Started',render:r=>formatDate(r.started_at)},
    {key:'status',header:'Status',render:r=><Badge variant={r.status==='completed'?'success':r.status==='failed'?'danger':'neutral'}>{r.status}</Badge>},
    {key:'progress_message',header:'Progress',render:r=><div className="text-sm"><p>{r.progress_message}</p>{r.error&&<p className="text-danger">{r.error}</p>}{r.status==='failed'&&r.safety_snapshot_id&&<p className="mt-1 text-muted-foreground">Some files may have changed. You can recover their previous versions using Recover previous files.</p>}</div>},
    {key:'actions',header:'Recovery',render:r=>r.safety_snapshot_id&&!['pending','running'].includes(r.status)?<Button size="sm" variant="secondary" onClick={()=>setUndo(r)}><RotateCcw className="h-3.5 w-3.5"/>Recover previous files</Button>:null},
  ]}/><ConfirmDialog open={!!undo} onOpenChange={open=>!open&&setUndo(null)} title="Recover previous file versions?" description="Restore the file versions saved immediately before this operation. Newer changes to those files will be replaced. Files that had no previous version are retained." confirmationText={username} confirmLabel="Recover previous files" loading={reversal.isPending} onConfirm={()=>reversal.mutate()}/></section>
}
