import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BadgeCheck, RefreshCw } from 'lucide-react'
import { get, post } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { SettingRow } from '@/components/ui/SettingRow'
import { CenteredSpinner } from '@/components/ui/Spinner'
import { ErrorState } from '@/components/ui/States'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { toast } from '@/components/ui/Toast'
const KEY_PATTERN=/^BRN(-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}){4}$/
const badge={active:'active',warning:'warning',unlicensed:'disabled',locked:'error'}
const stateLabel={active:'Active',warning:'Active, check overdue',unlicensed:'No license',locked:'Not usable'}
function when(value){return value?new Date(value).toLocaleString():'—'}
function Section({title,description,children,status}){return <section className="tool-section"><div className="tool-section-heading"><div><h2>{title}</h2>{description&&<p>{description}</p>}</div>{status}</div>{children}</section>}
export default function License(){
 const queryClient=useQueryClient()
 const [key,setKey]=useState(''),[touched,setTouched]=useState(false)
 const query=useQuery({queryKey:['license'],queryFn:()=>get('/api/v1/admin/license'),retry:false})
 const done=data=>{queryClient.setQueryData(['license'],data);queryClient.invalidateQueries({queryKey:['license-state']})}
 const activate=useMutation({mutationFn:()=>post('/api/v1/admin/license',{license_key:key.trim()}),onSuccess:data=>{done(data);setKey('');setTouched(false);data.usable?toast.success('License activated'):toast.error('License saved but not usable',data.message)},onError:error=>toast.error('Could not activate license',error.message)})
 const refresh=useMutation({mutationFn:()=>post('/api/v1/admin/license/refresh'),onSuccess:data=>{done(data);data.last_error?toast.error('License check failed',data.last_error):toast.success('License checked')},onError:error=>toast.error('Could not check license',error.message)})
 if(query.isLoading)return <CenteredSpinner/>
 const header=<PageHeader title="License" description="This server's Boron license. Hosted websites, mail and DNS keep running whatever its state." icon={BadgeCheck}/>
 if(query.error)return <div className="reference-page">{header}<ErrorState error={query.error} onRetry={query.refetch}/></div>
 const data=query.data,lic=data.license,normalized=key.replace(/\s+/g,'').toUpperCase(),valid=KEY_PATTERN.test(normalized)
 return <div className="reference-page">
  {header}
  <Section title="Status" status={<StatusBadge status={badge[data.state]??'warning'} label={stateLabel[data.state]}/>}>
   {data.message&&<p role="status" className="settings-notice">{data.message}{!data.usable&&' Administration is limited to this page until the license is usable.'}</p>}
   {lic&&<><SettingRow label="License"><span>{`…${lic.key_hint} · ${lic.tier}`}</span></SettingRow>
    <SettingRow label="Hosting accounts" description="Accounts in use against the license limit."><span>{`${data.account_count} of ${lic.max_accounts??'unlimited'}`}</span></SettingRow>
    <SettingRow label="Bound to IP"><span>{lic.bound_ip||'—'}</span></SettingRow>
    <SettingRow label="Paid until" description="Updates are available until this date."><span>{lic.expires_at?when(lic.expires_at):'No expiry'}</span></SettingRow>
    <SettingRow label="Works offline until" description="The panel keeps working on its last check until this time."><span>{when(lic.valid_until)}</span></SettingRow></>}
   <SettingRow label="Last successful check"><span>{when(data.last_success_at)}</span></SettingRow>
   {data.last_error&&<SettingRow label="Last error"><span className="setting-row-error">{data.last_error}</span></SettingRow>}
   <div className="settings-inline-action"><Button variant="secondary" loading={refresh.isPending} disabled={!lic&&!data.last_attempt_at&&data.reason==='no_license'} onClick={()=>refresh.mutate()}><RefreshCw className="h-4 w-4"/>Check now</Button></div>
  </Section>
  <Section title={lic?'Replace license key':'Enter license key'} description="Activating binds the license to this server's public IP address.">
   <SettingRow label="License key" htmlFor="license-key" error={touched&&key&&!valid?'Enter the key in the form BRN-XXXX-XXXX-XXXX-XXXX.':null}><Input id="license-key" autoComplete="off" spellCheck={false} value={key} placeholder="BRN-XXXX-XXXX-XXXX-XXXX" onBlur={()=>setTouched(true)} onChange={e=>setKey(e.target.value)}/></SettingRow>
   <div className="settings-inline-action"><Button loading={activate.isPending} disabled={!valid} onClick={()=>activate.mutate()}>Activate license</Button></div>
  </Section>
 </div>
}
