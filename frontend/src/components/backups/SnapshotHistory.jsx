import { SnapshotFileRestore, SnapshotDatabaseRestore, SnapshotRestoreHistory } from './SnapshotRestore'
import { SnapshotMailRestore } from './SnapshotMailRestore'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Archive, ArrowUp, Folder, File, RefreshCw } from 'lucide-react'
import { get } from '@/lib/api'
import { formatBytes, formatDate } from '@/lib/utils'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/Card'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter } from '@/components/ui/Dialog'

const statusLabel={pending:'Queued',running:'In progress',completed:'Ready',failed:'Failed',expired:'Expired'}
function RunStatus({ status }) { return <Badge variant={status==='completed'?'success':status==='failed'?'danger':'neutral'}>{statusLabel[status]||status}</Badge> }
function RunDialog({ run, username, onClose }) {
  const [directory,setDirectory]=useState('/')
  const [browsing,setBrowsing]=useState(false)
  const [restorePaths,setRestorePaths]=useState('')
  const files=useQuery({queryKey:['snapshot-files',username,run.id,directory],queryFn:()=>get(`/api/v1/accounts/${encodeURIComponent(username)}/backups/snapshots/runs/${run.id}/browse?directory=${encodeURIComponent(directory)}`),enabled:!!username&&browsing&&!!run.snapshot_id&&run.status!=='expired'})
  return <Dialog open onOpenChange={open=>!open&&onClose()}><DialogContent size="xl"><DialogHeader><DialogTitle>Recovery point #{run.id}</DialogTitle><DialogDescription>{username} · {formatDate(run.started_at)}</DialogDescription></DialogHeader><DialogBody className="space-y-5">
    <div className="flex flex-wrap items-center gap-3"><RunStatus status={run.status}/><span className="text-sm text-muted-foreground">{run.progress_message}</span></div>
    {run.error&&<p role="alert" className="rounded-btn bg-danger/5 p-3 text-sm text-danger">{run.error}</p>}
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">{[['Files processed',run.summary?.total_files_processed??'—'],['Total size',formatBytes(run.summary?.total_bytes_processed||0)],['New data stored',formatBytes(run.summary?.data_added||0)],['Unchanged files',run.summary?.files_unmodified??'—']].map(([label,value])=><div key={label} className="rounded-btn border border-border p-3"><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 text-lg font-semibold">{value}</p></div>)}</div>
    <div><h3 className="mb-2 text-sm font-semibold">Included components</h3><div className="flex flex-wrap gap-2">{(run.options?.components||[]).map(c=><Badge key={c} variant="outline">{({files:'Files',databases:'Databases',mail:'Email messages',config:'Configuration'})[c]||c}</Badge>)}</div></div>
    {!!Object.keys(run.notification_results||{}).length&&<div><h3 className="mb-2 text-sm font-semibold">Notifications</h3>{Object.entries(run.notification_results).map(([channel,result])=><p key={channel} className="text-sm text-muted-foreground"><span className="capitalize">{channel}</span>: {result}</p>)}</div>}
    {run.snapshot_id&&run.status!=='expired'&&username&&<div className="space-y-3 border-t border-border pt-4"><Button variant="secondary" onClick={()=>setBrowsing(true)}><Folder className="h-4 w-4"/>Browse backed-up files</Button>
      {browsing&&<><div className="flex items-center gap-3"><Button size="sm" variant="ghost" disabled={directory==='/'} onClick={()=>setDirectory(directory.split('/').slice(0,-1).join('/')||'/')}><ArrowUp className="h-4 w-4"/>Up</Button><code className="min-w-0 break-all text-xs text-muted-foreground">{directory}</code></div><DataTable loading={files.isLoading} error={files.error} onRetry={files.refetch} data={files.data?.entries} getRowKey={r=>r.path} pageSize={20} emptyTitle="This folder is empty" onRowClick={r=>r.type==='dir'&&setDirectory(r.path)} columns={[
        {key:'name',header:'Name',render:r=><span className="flex items-center gap-2">{r.type==='dir'?<Folder className="h-4 w-4 text-accent"/>:<File className="h-4 w-4 text-muted-foreground"/>}{r.type==='dir'?<button className="text-accent-600 dark:text-accent-300 hover:underline" onClick={()=>setDirectory(r.path)}>{r.name}</button>:r.name}</span>},
        {key:'type',header:'Type',render:r=>r.type==='dir'?'Folder':r.type==='symlink'?'Symbolic link':'File'},
        {key:'size',header:'Size',render:r=>r.type==='file'?formatBytes(r.size||0):'—'},
        {key:'restore',header:'Restore',render:r=>r.restore_path!=null?<Button size="sm" variant="secondary" onClick={e=>{e.stopPropagation();setRestorePaths(p=>[...new Set([...p.split('\n').filter(Boolean),r.restore_path])].join('\n'))}}>Select for restore</Button>:null},
      ]}/></>}
    </div>}
    <SnapshotFileRestore username={username} run={run} paths={restorePaths} onPathsChange={setRestorePaths}/>
    <SnapshotDatabaseRestore username={username} run={run}/>
    <SnapshotMailRestore username={username} run={run}/>
    <SnapshotRestoreHistory username={username}/>
  </DialogBody><DialogFooter><Button variant="secondary" onClick={onClose}>Done</Button></DialogFooter></DialogContent></Dialog>
}

export function SnapshotHistory({ admin=false, username }) {
  const [selected,setSelected]=useState(null)
  const history=useQuery({queryKey:['snapshot-runs',admin?'admin':username],queryFn:()=>get(admin?'/api/v1/backups/snapshots/runs':`/api/v1/accounts/${encodeURIComponent(username)}/backups/snapshots/runs`),enabled:admin||!!username,refetchInterval:query=>(query.state.data?.runs||[]).some(r=>['pending','running'].includes(r.status))?3000:false})
  const columns=[
    {key:'id',header:'Recovery point',render:r=><button className="font-semibold text-accent-600 dark:text-accent-300 hover:underline" onClick={()=>setSelected(r)}>Backup #{r.id}</button>},
    ...(admin?[{key:'username',header:'Account',render:r=>r.username||`Account #${r.account_id}`}]:[]),
    {key:'started_at',header:'Created',sortable:true,render:r=>formatDate(r.started_at)},
    {key:'status',header:'Status',render:r=><RunStatus status={r.status}/>},
    {key:'size',header:'New data',render:r=>r.summary?.data_added!=null?formatBytes(r.summary.data_added):'—'},
    {key:'progress_message',header:'Progress',render:r=><span className={r.status==='failed'?'text-danger':'text-muted-foreground'}>{r.error||r.progress_message}</span>},
    {key:'actions',header:'Actions',render:r=><Button size="sm" variant="secondary" onClick={e=>{e.stopPropagation();setSelected(r)}}>View details</Button>},
  ]
  const contents=<><DataTable data={history.data?.runs} loading={history.isLoading} error={history.error} onRetry={history.refetch} onRowClick={setSelected} columns={columns} filterable pageSize={15} emptyIcon={Archive} emptyTitle="No scheduled recovery points yet" emptyDescription={admin?'Run a backup job to create the first recovery point.':'Recovery points from your hosting provider’s backup jobs will appear here.'}/>{selected&&<RunDialog run={history.data?.runs.find(r=>r.id===selected.id)||selected} username={username||selected.username} onClose={()=>setSelected(null)}/>}</>
  if (admin) return contents
  return <Card className="mb-6"><CardHeader><div><CardTitle>Scheduled recovery points</CardTitle><CardDescription>Encrypted snapshots created by your hosting provider.</CardDescription></div><Button size="sm" variant="ghost" loading={history.isFetching} onClick={()=>history.refetch()}><RefreshCw className="h-4 w-4"/>Refresh</Button></CardHeader><CardContent>{contents}<SnapshotRestoreHistory username={username}/></CardContent></Card>
}
