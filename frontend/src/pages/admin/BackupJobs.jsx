import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Archive, Plus, Play, Server, ShieldCheck, Clock, Download, Copy, Pencil } from 'lucide-react'
import { get, post, put } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardContent } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { DataTable } from '@/components/ui/Table'
import { Badge } from '@/components/ui/Badge'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/Tabs'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter } from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'
import { SnapshotHistory } from '@/components/backups/SnapshotHistory'

const API = '/api/v1/backups/snapshots'
const textAreaClass = 'min-h-24 w-full rounded-btn border border-border bg-background p-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring'
const emptyPolicy = { name: '', destination_id: '', frequency: 'daily', enabled: true, mode: 'incremental', accounts: [], excluded_accounts: [], components: ['files','databases','mail','config'], include_paths: [], exclude_patterns: [], notification_channels: [], retention_count: 7 }
const lines = text => text.split('\n').map(s => s.trim()).filter(Boolean)

function Check({ checked, onChange, children }) {
  return <label className="flex cursor-pointer items-start gap-2 text-sm"><input type="checkbox" className="mt-0.5 h-4 w-4 accent-accent" checked={checked} onChange={e => onChange(e.target.checked)} /><span>{children}</span></label>
}
function ErrorNotice({ error }) { return error ? <p role="alert" className="rounded-btn border border-danger/30 bg-danger/5 p-3 text-sm text-danger">{error.message}</p> : null }
function toggle(items, value, enabled) { return enabled ? [...new Set([...items,value])] : items.filter(item => item!==value) }

function PolicyDialog({ policy, destinations, accounts, onClose, onSaved }) {
  const [form,setForm] = useState(policy.id ? {...emptyPolicy,...policy,...policy.options} : {...emptyPolicy,destination_id:destinations.find(d=>d.status==='ready')?.id || ''})
  const [allAccounts,setAllAccounts] = useState(!form.accounts.length)
  const [includeText,setIncludeText] = useState(form.include_paths.join('\n'))
  const [excludeText,setExcludeText] = useState(form.exclude_patterns.join('\n'))
  const set = (key,value) => setForm(prev=>({...prev,[key]:value}))
  const save = useMutation({ mutationFn: () => {
    if (!allAccounts && !form.accounts.length) throw new Error('Select at least one account or choose all active accounts.')
    const body={...form,accounts:allAccounts?[]:form.accounts,include_paths:lines(includeText),exclude_patterns:lines(excludeText),destination_id:Number(form.destination_id),retention_count:Number(form.retention_count)}
    return policy.id ? put(`${API}/policies/${policy.id}`,body) : post(`${API}/policies`,body)
  }, onSuccess:()=>{toast.success('Backup job saved');onSaved();onClose()} })
  return <Dialog open onOpenChange={open=>!open && !save.isPending && onClose()}><DialogContent size="xl"><DialogHeader><DialogTitle>{policy.id?'Edit backup job':'Create backup job'}</DialogTitle><DialogDescription>Choose what to protect, where to keep it, and how often to run.</DialogDescription></DialogHeader>
    <form className="flex min-h-0 flex-1 flex-col" onSubmit={e=>{e.preventDefault();save.mutate()}}><DialogBody className="space-y-6"><ErrorNotice error={save.error}/>
      <div className="grid gap-4 sm:grid-cols-2"><FormField label="Job name" htmlFor="job-name"><Input id="job-name" required maxLength={100} value={form.name} onChange={e=>set('name',e.target.value)} placeholder="Daily website protection"/></FormField>
        <FormField label="Backup destination" htmlFor="job-destination"><Select id="job-destination" required value={form.destination_id} onChange={e=>set('destination_id',e.target.value)}><option value="">Choose a ready destination</option>{destinations.filter(d=>d.status==='ready').map(d=><option key={d.id} value={d.id}>{d.name}</option>)}</Select></FormField></div>
      <fieldset className="space-y-3"><legend className="mb-2 text-sm font-semibold">Accounts</legend><Check checked={allAccounts} onChange={setAllAccounts}>All active accounts, including accounts created later</Check>
        {!allAccounts && <div className="grid max-h-40 gap-2 overflow-auto rounded-btn border border-border p-3 sm:grid-cols-2">{accounts.map(a=><Check key={a.username} checked={form.accounts.includes(a.username)} onChange={v=>set('accounts',toggle(form.accounts,a.username,v))}>{a.username}</Check>)}</div>}
        <details><summary className="cursor-pointer text-sm text-muted-foreground">Exclude specific accounts</summary><div className="mt-3 grid max-h-40 gap-2 overflow-auto sm:grid-cols-2">{accounts.map(a=><Check key={a.username} checked={form.excluded_accounts.includes(a.username)} onChange={v=>set('excluded_accounts',toggle(form.excluded_accounts,a.username,v))}>{a.username}</Check>)}</div></details>
      </fieldset>
      <fieldset><legend className="mb-3 text-sm font-semibold">What to back up</legend><div className="grid gap-3 sm:grid-cols-2">{[['files','Website and account files'],['databases','Databases'],['mail','Email messages'],['config','Account configuration, DNS and cron']].map(([id,label])=><Check key={id} checked={form.components.includes(id)} onChange={v=>set('components',toggle(form.components,id,v))}>{label}</Check>)}</div></fieldset>
      <div className="grid gap-4 sm:grid-cols-2"><FormField label="Schedule" htmlFor="job-frequency"><Select id="job-frequency" value={form.frequency} onChange={e=>set('frequency',e.target.value)}>{['hourly','daily','weekly','manual'].map(v=><option key={v} value={v}>{v==='manual'?'Run manually':v[0].toUpperCase()+v.slice(1)}</option>)}</Select></FormField>
        <FormField label="Backup mode" htmlFor="job-mode" hint="Both modes create complete recovery points. Incremental reuses unchanged data; full scans every file."><Select id="job-mode" value={form.mode} onChange={e=>set('mode',e.target.value)}><option value="incremental">Incremental (recommended)</option><option value="full">Full scan</option></Select></FormField>
        <FormField label="Recovery points to keep" htmlFor="job-retention" hint="Per account and job. Older points expire after a successful backup."><Input id="job-retention" required type="number" min="1" max="365" value={form.retention_count} onChange={e=>set('retention_count',e.target.value)}/></FormField>
        <div className="self-center"><Check checked={form.enabled} onChange={v=>set('enabled',v)}>Enable scheduled backups</Check></div></div>
      <details className="rounded-btn border border-border p-4"><summary className="cursor-pointer text-sm font-semibold">File filters (optional)</summary><div className="mt-4 grid gap-4 sm:grid-cols-2"><FormField label="Include only these paths" htmlFor="job-includes" hint="One path per line, relative to each account’s home. Leave empty for all files."><textarea id="job-includes" className={textAreaClass} value={includeText} onChange={e=>setIncludeText(e.target.value)} placeholder={'public_html\nexample.com'}/></FormField><FormField label="Exclude matching paths" htmlFor="job-excludes" hint="One pattern per line. Patterns apply across the snapshot, including database exports."><textarea id="job-excludes" className={textAreaClass} value={excludeText} onChange={e=>setExcludeText(e.target.value)} placeholder={'**/cache/**\n**/node_modules/**'}/></FormField></div></details>
      <fieldset><legend className="mb-3 text-sm font-semibold">Completion and failure notifications</legend><div className="flex gap-6">{['email','webhook'].map(channel=><Check key={channel} checked={form.notification_channels.includes(channel)} onChange={v=>set('notification_channels',toggle(form.notification_channels,channel,v))}>{channel==='email'?'Email':'Webhooks'}</Check>)}</div><p className="mt-2 text-xs text-muted-foreground">Uses configured notification channels and account preferences. Delivery results appear in run details.</p></fieldset>
    </DialogBody><DialogFooter><Button type="button" variant="ghost" disabled={save.isPending} onClick={onClose}>Cancel</Button><Button type="submit" loading={save.isPending} disabled={!form.components.length}>Save job</Button></DialogFooter></form>
  </DialogContent></Dialog>
}

function DestinationDialog({ destination, onClose, onSaved }) {
  const [form,setForm]=useState({name:'',kind:'local',path:'/var/backups/boron',ssh_host:'',ssh_user:'',ssh_port:22,ssh_host_key:''})
  const [saved,setSaved]=useState(destination.id?destination:null)
  const [recovery,setRecovery]=useState(null)
  const set=(key,value)=>setForm(prev=>({...prev,[key]:value}))
  const create=useMutation({mutationFn:()=>post(`${API}/destinations`,{...form,ssh_port:Number(form.ssh_port)}),onSuccess:row=>{setSaved(row);onSaved()}})
  const initialize=useMutation({mutationFn:()=>post(`${API}/destinations/${saved.id}/initialize`),onSuccess:row=>{setSaved(row);onSaved();toast.success('Destination ready','Backups can now be stored here.')}})
  const exportKey=useMutation({mutationFn:()=>post(`${API}/destinations/${saved.id}/recovery-key`),onSuccess:setRecovery})
  const copy=async(text)=>{try{await navigator.clipboard.writeText(text);toast.success('Copied')}catch{toast.error('Could not copy','Select and copy the text below.')}}
  const download=()=>{const blob=new Blob([JSON.stringify({...recovery,name:saved.name,connection:saved.connection},null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=`boron-backup-recovery-${saved.id}.json`;link.click();URL.revokeObjectURL(url)}
  const busy=create.isPending||initialize.isPending||exportKey.isPending
  return <Dialog open onOpenChange={open=>!open&&!busy&&onClose()}><DialogContent size="lg"><DialogHeader><DialogTitle>{saved?saved.name:'Add backup destination'}</DialogTitle><DialogDescription>{saved?'Connection setup and recovery information.':'Store encrypted backups on this server or a separate SSH server.'}</DialogDescription></DialogHeader>
    <form className="flex min-h-0 flex-1 flex-col" onSubmit={e=>{e.preventDefault();create.mutate()}}><DialogBody className="space-y-5"><ErrorNotice error={create.error||initialize.error||exportKey.error}/>
      {!saved?<><FormField label="Destination name" htmlFor="destination-name"><Input id="destination-name" required maxLength={100} value={form.name} onChange={e=>set('name',e.target.value)} placeholder="Offsite backup server"/></FormField><FormField label="Storage type" htmlFor="destination-kind"><Select id="destination-kind" value={form.kind} onChange={e=>set('kind',e.target.value)}><option value="local">Local disk</option><option value="ssh">Remote server over SSH</option></Select></FormField>
        {form.kind==='ssh'&&<><div className="grid gap-4 sm:grid-cols-2"><FormField label="SSH hostname or IP" htmlFor="ssh-host"><Input id="ssh-host" required value={form.ssh_host} onChange={e=>set('ssh_host',e.target.value)}/></FormField><FormField label="SSH port" htmlFor="ssh-port"><Input id="ssh-port" required type="number" min="1" max="65535" value={form.ssh_port} onChange={e=>set('ssh_port',e.target.value)}/></FormField></div><FormField label="SSH username" htmlFor="ssh-user"><Input id="ssh-user" required value={form.ssh_user} onChange={e=>set('ssh_user',e.target.value)}/></FormField><FormField label="Server public host key" htmlFor="ssh-host-key" hint="Obtain this from the backup server’s administrator or console, for example /etc/ssh/ssh_host_ed25519_key.pub. This verifies the server’s identity."><textarea id="ssh-host-key" required className={textAreaClass} value={form.ssh_host_key} onChange={e=>set('ssh_host_key',e.target.value)} placeholder="ssh-ed25519 AAAA…"/></FormField></>}
        <FormField label="Repository directory" htmlFor="destination-path" hint="An absolute, empty directory on the selected server. The backup user needs write access."><Input id="destination-path" required value={form.path} onChange={e=>set('path',e.target.value)}/></FormField>
        {form.kind==='local'&&<p className="text-sm text-muted-foreground">A separate SSH server also protects against loss of this server or disk.</p>}</>:<>
        <div className="rounded-btn border border-border bg-muted/30 p-4"><Badge variant={saved.status==='ready'?'success':'neutral'}>{saved.status==='ready'?'Ready for backups':'Setup required'}</Badge><p className="mt-2 break-all text-sm">{saved.kind==='ssh'?`${saved.connection.user}@${saved.connection.host}:`:''}{saved.path}</p></div>
        {saved.ssh_public_key&&<div className="space-y-3"><h3 className="text-sm font-semibold">1. Authorize this panel on the backup server</h3><p className="text-sm text-muted-foreground">Add this public key to the backup user’s <code>~/.ssh/authorized_keys</code> file. Enable the server’s SFTP subsystem.</p><textarea aria-label="Backup SSH public key" readOnly className={textAreaClass} value={saved.ssh_public_key}/><Button type="button" variant="secondary" onClick={()=>copy(saved.ssh_public_key)}><Copy className="h-4 w-4"/>Copy public key</Button></div>}
        <div><h3 className="mb-2 text-sm font-semibold">{saved.ssh_public_key?'2. ':''}Initialize and verify storage</h3><Button type="button" variant="secondary" loading={initialize.isPending} onClick={()=>initialize.mutate()}><ShieldCheck className="h-4 w-4"/>{saved.status==='ready'?'Verify connection':'Initialize destination'}</Button></div>
        <div className="space-y-3 border-t border-border pt-4"><h3 className="text-sm font-semibold">Keep your recovery key somewhere safe</h3><p className="text-sm text-muted-foreground">You need this key to recover encrypted backups if this panel is lost. Store it separately from the server.</p>{!recovery?<Button type="button" variant="secondary" loading={exportKey.isPending} onClick={()=>exportKey.mutate()}>Reveal recovery key</Button>:<><textarea readOnly aria-label="Backup recovery key" className={textAreaClass} value={recovery.password}/><Button type="button" variant="secondary" onClick={download}><Download className="h-4 w-4"/>Download recovery information</Button></>}</div>
      </>}
    </DialogBody><DialogFooter><Button type="button" variant="ghost" disabled={busy} onClick={onClose}>{saved?'Done':'Cancel'}</Button>{!saved&&<Button type="submit" loading={create.isPending}>Save destination</Button>}</DialogFooter></form>
  </DialogContent></Dialog>
}

export default function BackupJobs() {
  const qc=useQueryClient()
  const [tab,setTab]=useState('jobs')
  const [policy,setPolicy]=useState(null)
  const [destination,setDestination]=useState(null)
  const destinations=useQuery({queryKey:['snapshot-destinations'],queryFn:()=>get(`${API}/destinations`)})
  const policies=useQuery({queryKey:['snapshot-policies'],queryFn:()=>get(`${API}/policies`)})
  const accounts=useQuery({queryKey:['accounts'],queryFn:()=>get('/api/v1/accounts')})
  const refresh=()=>{qc.invalidateQueries({queryKey:['snapshot-destinations']});qc.invalidateQueries({queryKey:['snapshot-policies']});qc.invalidateQueries({queryKey:['snapshot-runs']})}
  const run=useMutation({mutationFn:id=>post(`${API}/policies/${id}/run`),onSuccess:data=>{toast.success(data.run_ids.length?'Backup queued':'No backups queued',`${data.run_ids.length} account(s) queued. ${data.skipped_busy_accounts?.length||0} busy account(s) skipped.`);refresh();setTab('history')},onError:e=>toast.error('Could not start backup',e.message)})
  const ready=(destinations.data?.destinations||[]).filter(d=>d.status==='ready')
  const destinationName=id=>destinations.data?.destinations.find(d=>d.id===id)?.name||`Destination #${id}`
  return <div><PageHeader title="Backup Manager" description="Protect hosting accounts with scheduled, encrypted recovery points." icon={Archive}><Button variant="secondary" onClick={()=>setDestination({})}><Server className="h-4 w-4"/>Add destination</Button><Button disabled={!ready.length} onClick={()=>setPolicy({})}><Plus className="h-4 w-4"/>Create job</Button></PageHeader>
    <div className="mb-6 grid gap-4 sm:grid-cols-3">{[[Server,'Ready destinations',ready.length],[Clock,'Scheduled jobs',(policies.data?.policies||[]).filter(p=>p.enabled&&p.frequency!=='manual').length],[ShieldCheck,'Storage protection','Encrypted']].map(([Icon,label,value])=><Card key={label}><CardContent className="flex items-center gap-4 pt-5"><Icon className="h-6 w-6 text-accent"/><div><p className="text-xs text-muted-foreground">{label}</p><p className="text-xl font-semibold">{value}</p></div></CardContent></Card>)}</div>
    <Tabs value={tab} onValueChange={setTab}><TabsList><TabsTrigger value="jobs">Backup jobs</TabsTrigger><TabsTrigger value="destinations">Destinations</TabsTrigger><TabsTrigger value="history">Run history</TabsTrigger></TabsList>
      <TabsContent value="jobs"><DataTable data={policies.data?.policies} loading={policies.isLoading} error={policies.error} onRetry={policies.refetch} onRowClick={setPolicy} filterable pageSize={15} emptyTitle="Create your first backup job" emptyDescription="Add and initialize a destination, then choose accounts and a schedule." emptyAction={<Button onClick={()=>ready.length?setPolicy({}):setDestination({})}>{ready.length?'Create job':'Add destination'}</Button>} columns={[
        {key:'name',header:'Job',render:r=><button className="font-semibold text-accent-600 dark:text-accent-300 hover:underline" onClick={e=>{e.stopPropagation();setPolicy(r)}}>{r.name}</button>},
        {key:'destination_id',header:'Destination',render:r=>destinationName(r.destination_id)},
        {key:'frequency',header:'Schedule',render:r=><span className="capitalize">{r.enabled?r.frequency:'Paused'}</span>},
        {key:'mode',header:'Protection',render:r=><span className="capitalize">{r.options.mode} · {r.options.retention_count} points</span>},
        {key:'actions',header:'Actions',render:r=><div className="flex gap-2" onClick={e=>e.stopPropagation()}><Button size="sm" variant="secondary" onClick={()=>setPolicy(r)}><Pencil className="h-3.5 w-3.5"/>Edit</Button><Button size="sm" loading={run.isPending&&run.variables===r.id} disabled={run.isPending} onClick={()=>run.mutate(r.id)}><Play className="h-3.5 w-3.5"/>Run now</Button></div>},
      ]}/></TabsContent>
      <TabsContent value="destinations"><DataTable data={destinations.data?.destinations} loading={destinations.isLoading} error={destinations.error} onRetry={destinations.refetch} onRowClick={setDestination} emptyTitle="No backup destinations" emptyDescription="Add local disk storage or connect an offsite SSH server." emptyAction={<Button onClick={()=>setDestination({})}>Add destination</Button>} columns={[
        {key:'name',header:'Destination',render:r=><button className="font-semibold text-accent-600 dark:text-accent-300 hover:underline" onClick={()=>setDestination(r)}>{r.name}</button>},
        {key:'kind',header:'Storage',render:r=>r.kind==='ssh'?'SSH server':'Local disk'},
        {key:'path',header:'Directory',render:r=><span className="break-all">{r.path}</span>},
        {key:'status',header:'Status',render:r=><Badge variant={r.status==='ready'?'success':'neutral'}>{r.status==='ready'?'Ready':'Setup required'}</Badge>},
        {key:'actions',header:'Actions',render:r=><Button size="sm" variant="secondary" onClick={e=>{e.stopPropagation();setDestination(r)}}>Manage</Button>},
      ]}/></TabsContent>
      <TabsContent value="history"><SnapshotHistory admin/></TabsContent>
    </Tabs>
    {policy&&<PolicyDialog key={policy.id||'new'} policy={policy} destinations={destinations.data?.destinations||[]} accounts={accounts.data?.accounts||[]} onClose={()=>setPolicy(null)} onSaved={refresh}/>}
    {destination&&<DestinationDialog key={destination.id||'new'} destination={destination} onClose={()=>setDestination(null)} onSaved={refresh}/>}
  </div>
}
