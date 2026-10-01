import { useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { Network, ExternalLink, ShieldCheck, Globe, Activity, History } from 'lucide-react'
import { get, post, humanErrorMessage } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Checkbox } from '@/components/ui/Toggle'
import { SettingRow } from '@/components/ui/SettingRow'
import { DraftChanges, useDraftSection } from '@/components/ui/DraftChanges'
import { CenteredSpinner } from '@/components/ui/Spinner'
import { ErrorState } from '@/components/ui/States'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { toast } from '@/components/ui/Toast'
const sections=[['access','Panel access',Network],['hostname','Hostname & SSL',Globe],['telemetry','Error telemetry',Activity],['history','Recent changes',History]]
function address(port){const url=new URL(window.location.href);url.protocol='https:';url.port=String(port);url.pathname='/app/panel-settings';url.search='';url.hash='';return url.href}
function Section({title,description,children,status}){return <section className="tool-section"><div className="tool-section-heading"><div><h2>{title}</h2>{description&&<p>{description}</p>}</div>{status}</div>{children}</section>}
function PanelSettingsForm(){
 const [params]=useSearchParams(),initial=params.get('section')
 const [section,setSection]=useState(sections.some(([key])=>key===initial)?initial:'access')
 useEffect(()=>{if(sections.some(([key])=>key===initial))setSection(initial)},[initial])
 const [form,setForm]=useState(null),[confirmed,setConfirmed]=useState(false),[submitted,setSubmitted]=useState(null),[hostname,setHostname]=useState(null),[certificateEmail,setCertificateEmail]=useState(''),[touched,setTouched]=useState({})
 const query=useQuery({queryKey:['panel-config'],queryFn:()=>get('/api/v1/admin/panel-config'),refetchInterval:q=>{const latest=q.state.data?.jobs?.find(job=>job.id===submitted?.id)??submitted;return ['pending','running'].includes(latest?.status)||q.state.data?.jobs?.some(job=>['pending','running'].includes(job.status))?3000:false},retry:false})
 const certificateQuery=useQuery({queryKey:['panel-certificate'],queryFn:()=>get('/api/v1/admin/panel-config/certificate'),retry:false})
 const data=query.data,active=data?.jobs?.find(job=>['pending','running'].includes(job.status)),values=form??{admin_port:data?.admin_port??2222,customer_port:data?.customer_port??2222},hostnameValue=hostname??data?.hostname??''
 const currentJob=data?.jobs?.find(job=>job.id===submitted?.id)??submitted??active
 const mutation=useMutation({mutationFn:body=>post('/api/v1/admin/panel-config/ports',body),onSuccess:job=>{setSubmitted(job);setForm(null);setConfirmed(false);query.refetch()},onError:error=>toast.error('Could not change panel ports',error.message)})
 const hostnameMutation=useMutation({mutationFn:()=>post('/api/v1/admin/panel-config/hostname',{hostname:hostnameValue.trim().toLowerCase()}),onSuccess:result=>{toast.success('Panel hostname updated',result.hostname);setHostname(null);query.refetch();certificateQuery.refetch()},onError:error=>toast.error('Could not change panel hostname',error.message)})
 const certificateMutation=useMutation({mutationFn:()=>post('/api/v1/admin/panel-config/certificate',{email:certificateEmail.trim()}),onSuccess:()=>{toast.success('Panel certificate issued');setCertificateEmail('');certificateQuery.refetch()},onError:error=>toast.error('Could not issue panel certificate',error.message)})
 const valid=[values.admin_port,values.customer_port].every(port=>Number.isInteger(port)&&port>=1024&&port<=65535)
 const changed=!!data&&(values.admin_port!==data.admin_port||values.customer_port!==data.customer_port)
 const hostnameChanged=!!data&&hostnameValue.trim().toLowerCase()!==data.hostname
 const hostnameValid=/^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}$/i.test(hostnameValue.trim())
 const emailValid=/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(certificateEmail.trim())
 const busy=mutation.isPending||!!active||['pending','running'].includes(currentJob?.status)
 useDraftSection('panel-ports',{dirty:changed,busy,disabled:!valid||!confirmed||busy,label:'Panel access',onSave:()=>mutation.mutateAsync({...values,confirm:true}),onDiscard:()=>{setForm(null);setConfirmed(false);setTouched({})}})
 useDraftSection('panel-hostname',{dirty:hostnameChanged,busy:hostnameMutation.isPending,disabled:!hostnameValid,label:'Panel hostname',onSave:()=>hostnameMutation.mutateAsync(),onDiscard:()=>{setHostname(null);setTouched({})}})
 // Issuing SSL is an explicit operation, not part of saving settings.
 useDraftSection('certificate-email',{dirty:!!certificateEmail,busy:certificateMutation.isPending,label:'Certificate request',onDiscard:()=>setCertificateEmail('')})
 if(query.isLoading)return <CenteredSpinner/>
 if(query.error&&!data&&!submitted)return <ErrorState error={query.error} onRetry={query.refetch}/>
 return <div className="reference-page">
  <PageHeader title="Panel settings" description="Choose how administrators and customers access your panel." icon={Network}/>
  <div className="settings-workspace">
   <nav className="settings-subnav" aria-label="Panel settings sections" role="tablist">{sections.map(([key,label,Icon])=><button key={key} id={`settings-tab-${key}`} type="button" role="tab" aria-selected={section===key} tabIndex={section===key?0:-1} onKeyDown={e=>{const keys=sections.map(([value])=>value);const index=keys.indexOf(key);const next=e.key==='Home'?0:e.key==='End'?keys.length-1:['ArrowDown','ArrowRight'].includes(e.key)?(index+1)%keys.length:['ArrowUp','ArrowLeft'].includes(e.key)?(index+keys.length-1)%keys.length:null;if(next!==null){e.preventDefault();setSection(keys[next]);document.getElementById(`settings-tab-${keys[next]}`)?.focus()}}} aria-controls={`settings-pane-${key}`} onClick={()=>setSection(key)}><Icon aria-hidden="true"/>{label}</button>)}</nav>
   <div className="settings-pane" id={`settings-pane-${section}`} role="tabpanel" aria-labelledby={`settings-tab-${section}`}>
    {section==='access'&&<><Section title="Panel access" description="Use one HTTPS port for everyone, or separate administrator and customer access.">
     {query.error&&<p role="status" className="settings-notice">The panel may be restarting. Open the new administrator address below. If verification fails, the previous address is restored.</p>}
     {[['admin_port','Administrator port'],['customer_port','Customer port']].map(([key,label])=><SettingRow key={key} label={label} htmlFor={key} description="Default 2222. Choose a port from 1024 to 65535." error={touched[key]&&(!Number.isInteger(values[key])||values[key]<1024||values[key]>65535)?'Enter a whole port number from 1024 to 65535.':null}><Input type="number" min={1024} max={65535} step={1} disabled={busy} value={values[key]} onBlur={()=>setTouched(t=>({...t,[key]:true}))} onChange={e=>{setForm({...values,[key]:e.target.value===''?'':Number(e.target.value)});setConfirmed(false)}}/></SettingRow>)}
     {valid&&<><SettingRow label="Administrator address" description="Save this address before changing ports."><a className="break-all text-accent" href={address(values.admin_port)}>{address(values.admin_port)}</a></SettingRow><SettingRow label="Customer address" description="Both access levels use HTTPS."><a className="break-all text-accent" href={address(values.customer_port).replace('/panel-settings','/dashboard')}>{address(values.customer_port).replace('/panel-settings','/dashboard')}</a></SettingRow></>}
     <SettingRow label="External firewall confirmed" description="I have saved the new addresses and allowed the ports in any external firewall. Applying restarts the panel briefly." htmlFor="ports-confirmed"><Checkbox checked={confirmed} disabled={busy} onCheckedChange={setConfirmed}/></SettingRow>
     <div className="settings-inline-action"><Button loading={mutation.isPending} disabled={!valid||!changed||!confirmed||busy||!data} onClick={()=>mutation.mutate({...values,confirm:true})}>Apply panel ports</Button>{busy&&<span role="status">Applying ports and checking HTTPS listeners…</span>}</div>
    </Section>{currentJob&&<Section title={currentJob.status==='completed'?'Panel ports updated':currentJob.status==='failed'?'Port change failed':'Applying panel ports'}><div className="settings-inline-action"><p role="status">{(currentJob.error?humanErrorMessage(currentJob.error):null)||(currentJob.status==='completed'?'Both HTTPS listeners passed their health checks.':'Allow up to a minute for the restart and health checks. If verification fails, the previous configuration is restored.')}</p>{currentJob.status!=='failed'&&<a className="text-accent underline" href={address(currentJob.admin_port)}>Open new administrator address <ExternalLink className="inline h-4 w-4"/></a>}</div></Section>}</>}
    {section==='hostname'&&<><Section title="Panel hostname" description="Create its DNS A or AAAA record before issuing SSL."><SettingRow label="Panel hostname" description="Hostname only, without https:// or a port." htmlFor="panel-hostname" error={touched.hostname&&!hostnameValid?'Enter a complete hostname, such as panel.example.com.':null}><Input value={hostnameValue} placeholder="panel.example.com" onBlur={()=>setTouched(t=>({...t,hostname:true}))} onChange={e=>setHostname(e.target.value)}/></SettingRow><div className="settings-inline-action"><Button loading={hostnameMutation.isPending} disabled={!hostnameValid||!hostnameChanged} onClick={()=>hostnameMutation.mutate()}>Save panel hostname</Button></div></Section>
     <Section title="Panel SSL" description="Let’s Encrypt also secures OpenLiteSpeed WebAdmin and FTPS." status={certificateQuery.data&&<StatusBadge status={certificateQuery.data.valid_for_hostname?'active':'warning'}/>}>
      {certificateQuery.error&&<ErrorState error={certificateQuery.error} onRetry={certificateQuery.refetch}/>}
      {certificateQuery.data&&<><SettingRow label="Certificate hostname"><span>{certificateQuery.data.hostname||'Not configured'}</span></SettingRow><SettingRow label="Certificate"><span>{certificateQuery.data.certificate_present?(certificateQuery.data.valid_for_hostname?'Valid for hostname':'Does not match hostname'):'Not installed'}</span></SettingRow><SettingRow label="Expires"><span>{certificateQuery.data.expires_at?`${new Date(certificateQuery.data.expires_at).toLocaleDateString()} (${certificateQuery.data.days_remaining} days)`:'—'}</span></SettingRow></>}
      <SettingRow label="Let’s Encrypt email" htmlFor="certificate-email" description="Used for certificate authority notices." error={touched.email&&certificateEmail&&!emailValid?'Enter a complete email address.':null}><Input type="email" value={certificateEmail} placeholder="admin@example.com" onBlur={()=>setTouched(t=>({...t,email:true}))} onChange={e=>setCertificateEmail(e.target.value)}/></SettingRow><div className="settings-inline-action"><Button loading={certificateMutation.isPending} disabled={!data?.hostname||!emailValid||hostnameChanged} onClick={()=>certificateMutation.mutate()}>{certificateQuery.data?.certificate_present?'Renew panel certificate':'Issue panel certificate'}</Button>{certificateMutation.isPending&&<span role="status">Requesting and deploying the certificate…</span>}</div>
     </Section></>}
    {section==='telemetry'&&<Section title="Error telemetry" description="Optional Sentry reporting; configured secrets and request identity are removed." status={<StatusBadge status={data?.telemetry?.enabled?'active':'disabled'}/>}><SettingRow label="Reporting" description="Local structured error logs work whether telemetry is enabled or disabled."><span>{data?.telemetry?.enabled?`Enabled for ${data.telemetry.environment}`:'Telemetry is off'}</span></SettingRow><div className="settings-inline-action"><p className="setting-row-description">To enable it, add <code>SENTRY_DSN=…</code> and optionally <code>SENTRY_ENVIRONMENT=production</code> to <code>/etc/boron/api-secrets.env</code>, then restart <code>boron-api</code> and <code>boron-provisiond</code>.</p></div></Section>}
    {section==='history'&&<Section title="Recent changes">{data?.jobs?.length?data.jobs.map(job=><SettingRow key={job.id} label={`Admin ${job.admin_port} · Customer ${job.customer_port}`} description={`${new Date(job.created_at).toLocaleString()} · ${job.initiated_by}`}><div><StatusBadge status={job.status}/>{job.error&&<p role="status" className="setting-row-error">{humanErrorMessage(job.error)}</p>}</div></SettingRow>):<div className="settings-inline-action">No panel port changes yet.</div>}</Section>}
   </div>
  </div>
 </div>
}
export default function PanelSettings(){return <DraftChanges><PanelSettingsForm/></DraftChanges>}
