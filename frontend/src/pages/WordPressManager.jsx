import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Globe, Plus, Search, ExternalLink, ArrowRight, ArrowLeft, Check, RefreshCw, Archive, Copy, LogIn, Settings, ShieldCheck, Loader2, X, AlertCircle } from 'lucide-react'
import { SiWordpress } from 'react-icons/si'
import { get, post } from '@/lib/api'
import { useAuth } from '@/store/auth'
import { PageHeader } from '@/components/ui/PageHeader'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter, ConfirmDialog } from '@/components/ui/Dialog'
import { searchScore } from '@/config/search'

const extensionCache = new Map()
const EXTENSION_CACHE_MS = 30_000
const extensionKey = (site, kind) => `${site.username}:${site.domain}:${site.path || ''}:${kind}`
const base = (site) => `/api/v1/accounts/${encodeURIComponent(site.username)}/domains/${encodeURIComponent(site.domain)}/wordpress`
function password() { const bytes = crypto.getRandomValues(new Uint8Array(22)); return 'Wp1!' + Array.from(bytes, n => 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'[n % 62]).join('') }
function ErrorNotice({ error }) { return error ? <div className="wp-notice wp-error" role="alert"><AlertCircle size={18} /><span>{error.message || String(error)}</span></div> : null }
function Progress({ job }) { return job ? <div className={`wp-notice ${job.status === 'failed' ? 'wp-error' : ''}`} role="status">{['pending','running'].includes(job.status) ? <Loader2 size={18} className="animate-spin" /> : job.status === 'failed' ? <AlertCircle size={18} /> : <Check size={18} />}<div><strong>{job.status === 'completed' ? 'Ready' : job.status === 'failed' ? 'This operation could not finish' : 'Working on your site…'}</strong><p>{job.error || job.message || job.progress_message || (job.status === 'completed' ? 'Your changes have been saved.' : 'You can keep this window open. Larger sites take a little longer.')}</p></div></div> : null }

function parseWpResult(job) {
  if (!job || job.status !== 'completed') return null
  try { return JSON.parse(job.stdout || 'null') }
  catch { throw new Error(job.stderr?.trim() || 'WordPress returned an unreadable response. Review the site PHP error log and try again.') }
}

function InstallWizard({ domains, onClose, onInstalled }) {
  const [step, setStep] = useState(0)
  const [form, setForm] = useState({ choice: '', path: '', address: 'https', title: '', admin_user: 'siteadmin', admin_email: '', admin_password: password() })
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState(null)
  const [pending, setPending] = useState(false)
  const [job, setJob] = useState(null)
  const site = domains.find(d => `${d.username}:${d.domain}` === form.choice)
  const polling = useQuery({ queryKey: ['wp-install', site?.username, job?.id], queryFn: () => get(`${base(site)}/jobs/${job.id}`), enabled: !!job?.id && ['pending','running'].includes(job.status), refetchInterval: 2000 })
  useEffect(() => { if (polling.data) { setJob(polling.data); if (polling.data.status === 'completed') onInstalled() } }, [polling.data])
  const change = (key) => e => setForm(f => ({ ...f, [key]: e.target.value }))
  const url = site ? `${form.address.startsWith('https')?'https':'http'}://${form.address.endsWith('www')?'www.':''}${site.domain}${form.path ? '/' + form.path.replace(/^\/+|\/+$/g,'') : ''}` : ''
  function next(e) { e.preventDefault(); setError(null); if (!site) return setError('Choose a domain first.'); if (form.path && !/^[a-zA-Z0-9_-]+$/.test(form.path)) return setError('Use a single folder name with letters, numbers, hyphens or underscores.'); setStep(s => s + 1) }
  async function install() { setPending(true); setError(null); try { setJob(await post(base(site), { path: form.path, protocol:form.address.startsWith('https')?'https':'http', use_www:form.address.endsWith('www'), title: form.title, admin_user: form.admin_user, admin_email: form.admin_email, admin_password: form.admin_password })) } catch(e) { setError(e) } finally { setPending(false) } }
  return <Dialog open onOpenChange={(open) => !open && !pending && !['pending','running'].includes(job?.status) && onClose()}><DialogContent size="xl" className="wp-dialog"><DialogHeader><DialogTitle>Install WordPress</DialogTitle><DialogDescription>A new website, ready in a few simple steps.</DialogDescription></DialogHeader><DialogBody>
    <ol className="wp-steps">{['Choose a location','Site & account','Review & install'].map((label,i) => <li key={label} className={i === step ? 'current' : i < step ? 'done' : ''}><span>{i < step ? <Check size={14}/> : i+1}</span>{label}</li>)}</ol>
    <ErrorNotice error={error || polling.error}/><Progress job={job}/>
    {job?.status === 'completed' ? <div className="wp-success"><span className="wp-success-icon"><Check/></span><h2>Your website is ready</h2><p>{url}</p><div className="wp-credentials"><span>Administrator username</span><strong>{form.admin_user}</strong><span>Password</span><code>{job.admin_password || form.admin_password}</code></div><p>Save these credentials somewhere private. You can also use Log in from WordPress Manager.</p><Button onClick={onClose}>Manage my website <ArrowRight size={16}/></Button></div> : <form id="wp-install-form" onSubmit={next}>
    {step === 0 && <div className="wp-form"><FormField label="Where should WordPress be installed?"><select required aria-label="Installation domain" value={form.choice} onChange={change('choice')}><option value="">Choose a domain</option>{domains.map(d => <option key={`${d.username}:${d.domain}`} value={`${d.username}:${d.domain}`}>{d.domain} · {d.username}</option>)}</select></FormField><FormField label="Website address" hint="HTTPS needs a certificate covering the selected hostname. Point both your domain and www address to this server when using www."><select aria-label="Website address format" value={form.address} onChange={change('address')}><option value="https">https://</option><option value="https-www">https://www.</option><option value="http">http://</option><option value="http-www">http://www.</option></select></FormField><FormField label="Installation folder (optional)" hint="Leave empty to use the main website. Enter blog to install at example.com/blog."><Input aria-label="Installation folder" placeholder="Leave empty for your main website" value={form.path} onChange={change('path')}/></FormField>{url && <div className="wp-url-preview"><Globe size={18}/><span>{url}</span></div>}<p className="wp-help">The destination must be empty. Existing website files will never be overwritten by an installation.</p></div>}
    {step === 1 && <div className="wp-form"><FormField label="Website name"><Input required aria-label="Website name" placeholder="My beautiful website" value={form.title} onChange={change('title')}/></FormField><div className="wp-form-grid"><FormField label="Administrator username"><Input required pattern="[a-zA-Z0-9_.-]+" aria-label="Administrator username" value={form.admin_user} onChange={change('admin_user')}/></FormField><FormField label="Administrator email"><Input required type="email" aria-label="Administrator email" placeholder="you@example.com" value={form.admin_email} onChange={change('admin_email')}/></FormField></div><FormField label="Administrator password" hint="A strong password is generated for you. Save it before leaving this screen."><div className="wp-password"><Input required minLength={12} aria-label="Administrator password" type={showPassword ? 'text' : 'password'} value={form.admin_password} onChange={change('admin_password')}/><Button type="button" variant="outline" onClick={() => setShowPassword(v=>!v)}>{showPassword ? 'Hide' : 'Show'}</Button><Button type="button" variant="outline" aria-label="Generate password" onClick={() => setForm(f=>({...f,admin_password:password()}))}><RefreshCw size={16}/></Button></div></FormField></div>}
    {step === 2 && <div className="wp-review"><h3>Everything looks ready</h3><dl><dt>Website</dt><dd>{form.title}</dd><dt>Address</dt><dd>{url}</dd><dt>Administrator</dt><dd>{form.admin_user}</dd><dt>Email</dt><dd>{form.admin_email}</dd><dt>Database</dt><dd>Created automatically</dd><dt>WordPress</dt><dd>Latest stable version</dd></dl><div className="wp-notice"><ShieldCheck size={18}/><span>{form.address.startsWith('https')?'Your site uses HTTPS. Make sure the selected hostname points to this server and has a matching SSL certificate.':'Your site uses HTTP. Traffic is not encrypted. You can switch to HTTPS in WordPress after issuing an SSL certificate.'}</span></div></div>}
    </form>}
    </DialogBody>{job?.status !== 'completed' && <DialogFooter><Button variant="outline" disabled={pending || ['pending','running'].includes(job?.status)} onClick={() => step ? setStep(s=>s-1) : onClose()}>{step ? <><ArrowLeft size={15}/> Back</> : 'Cancel'}</Button>{step < 2 ? <Button type="submit" form="wp-install-form">Continue <ArrowRight size={15}/></Button> : <Button disabled={pending || ['pending','running'].includes(job?.status)} onClick={install}>{pending || ['pending','running'].includes(job?.status) ? <Loader2 size={16} className="animate-spin"/> : <Plus size={16}/>} Install WordPress</Button>}</DialogFooter>}</DialogContent></Dialog>
}

function SiteManager({ site, domains, onClose, onChanged }) {
  const [tab, setTab] = useState('overview')
  const [job, setJob] = useState(null)
  const [error, setError] = useState(null)
  const [pending, setPending] = useState(false)
  const [result, setResult] = useState(null)
  const [backups, setBackups] = useState([])
  const [target, setTarget] = useState(site.domain)
  const [targetAddress,setTargetAddress]=useState((site.url.startsWith('https:')?'https':'http')+(new URL(site.url).hostname==='www.'+site.domain?'-www':''))
  const [folder, setFolder] = useState(site.path ? site.path+'-copy' : 'staging')
  const [confirm, setConfirm] = useState(null)
  const [removalMode,setRemovalMode]=useState('soft')
  const [removalConfirmation,setRemovalConfirmation]=useState('')
  const address=site.domain+(site.path?'/'+site.path:'')
  async function removeSite() {
    setPending(true);setError(null)
    try {
      const result=await post(base(site)+'/remove',{path:site.path,mode:removalMode,confirmation:removalConfirmation})
      if(result.status==='removed') {onChanged();onClose()} else setJob(result)
    } catch(e) {setError(e)} finally {setPending(false)}
  }
  const polling = useQuery({ queryKey: ['wp-operation',site.username,job?.id], queryFn: () => get(`${base(site)}/actions/runs/${job.id}`), enabled: !!job?.id && ['pending','running'].includes(job.status), refetchInterval: 2000 })
  useEffect(() => {
    if (!polling.data) return
    setJob(polling.data)
    if (polling.data.status !== 'completed') return
    try {
      const command=polling.data.command || ''
      const changedKind=command.startsWith('wp plugin ')&&!command.startsWith('wp plugin list')?'plugins':command.startsWith('wp theme ')&&!command.startsWith('wp theme list')?'themes':null
      if(changedKind){
        extensionCache.delete(extensionKey(site,changedKind))
        setJob(null)
        act(changedKind==='plugins'?'plugin_list':'theme_list',{},true)
        return
      }
      const data = parseWpResult(polling.data)
      if (['plugins','themes'].includes(tab) && !Array.isArray(data)) {
        throw new Error(`WordPress did not return an installed ${tab} list. Try again or review the site PHP error log.`)
      }
      if(['plugins','themes'].includes(tab))extensionCache.set(extensionKey(site,tab),{data,expires:Date.now()+EXTENSION_CACHE_MS})
      setResult(data)
      if (data?.backups) setBackups(data.backups)
      else if (data?.name && data?.created_at) setBackups(items=>[data,...items.filter(b=>b.name!==data.name)])
      onChanged()
    } catch (e) {
      setResult(null)
      setError(e)
    }
  }, [polling.data])
  const busy = pending || ['pending','running'].includes(job?.status)
  async function act(action, fields={}, cli=false) { setPending(true); setError(null); setResult(null); try { setJob(await post(`${base(site)}/${cli?'actions':'manage'}`,{path:site.path,action,...fields})) } catch(e) { setError(e) } finally { setPending(false) } }
  function choose(value,force=false) { setTab(value);setResult(null);setJob(null); if(value==='backups') act('backups'); if(['plugins','themes'].includes(value)){const cached=extensionCache.get(extensionKey(site,value));if(!force&&cached?.expires>Date.now()){setResult(cached.data);return}extensionCache.delete(extensionKey(site,value));act(value==='plugins'?'plugin_list':'theme_list',{},true)} }
  return <Dialog open onOpenChange={open=>!open && !busy && onClose()}><DialogContent size="xl" className="wp-dialog"><DialogHeader><DialogTitle>{site.domain}{site.path ? '/'+site.path:''}</DialogTitle><DialogDescription>WordPress {site.wp_version || 'detected'} · {site.username}</DialogDescription></DialogHeader><DialogBody><div className="wp-tabs" role="tablist">{[['overview','Overview'],['plugins','Plugins'],['themes','Themes'],['backups','Backups'],['clone','Clone site'],['remove','Remove']].map(([id,label])=><button role="tab" aria-selected={tab===id} disabled={busy} key={id} onClick={()=>choose(id)}>{label}</button>)}</div><ErrorNotice error={error || polling.error}/><Progress job={job}/>
    {tab==='overview' && <><div className="wp-url-preview"><Globe size={18}/><a href={site.url} target="_blank" rel="noopener noreferrer">{site.url} <ExternalLink size={13}/></a></div><div className="wp-action-grid"><button disabled={busy} onClick={()=>setConfirm({action:'core_update',cli:true,label:'Update WordPress core',text:'Create a backup first if you have custom code. Your plugins and themes will be kept.'})}><RefreshCw/><strong>Update WordPress</strong><span>Get the latest core release</span></button><button disabled={busy} onClick={()=>act('cache_flush',{},true)}><RefreshCw/><strong>Clear cache</strong><span>Refresh cached site content</span></button><button disabled={busy} onClick={()=>act('maintenance_on',{},true)}><Settings/><strong>Maintenance on</strong><span>Pause public access while working</span></button><button disabled={busy} onClick={()=>act('maintenance_off',{},true)}><Globe/><strong>Maintenance off</strong><span>Make your website available again</span></button></div></>}
    {['plugins','themes'].includes(tab) && <div className="wp-extension-list"><div className="wp-section-heading"><h3>Installed {tab}</h3><div className="flex gap-2"><Button disabled={busy} size="sm" variant="outline" onClick={()=>choose(tab,true)}><RefreshCw size={14}/> Refresh</Button><Button disabled={busy} size="sm" onClick={()=>setConfirm({action:tab==='plugins'?'plugin_update':'theme_update',fields:{all:true},cli:true,label:`Update all ${tab}`,text:'A recent backup is recommended before updating.'})}>Update all</Button></div></div>{Array.isArray(result) ? result.map(item=><div className="wp-extension" key={item.name}><div><strong>{item.title || item.name}</strong><small>{item.version} · {item.status}</small></div><span className="wp-badge">{item.update === 'available' ? 'Update available' : 'Installed'}</span><Button size="sm" variant="outline" disabled={busy || (tab==='themes' && item.status==='active')} onClick={()=>act(`${tab==='plugins'?'plugin':'theme'}_${item.status==='active' && tab==='plugins'?'deactivate':'activate'}`,{name:item.name},true)}>{item.status==='active' ? (tab==='plugins'?'Deactivate':'Active'):'Activate'}</Button></div>): !busy && <p className="wp-help">No extensions to display. Refresh to try loading the list again.</p>}</div>}
    {tab==='backups' && <><div className="wp-section-heading"><div><h3>Files and database, together</h3><p>Private backups stored outside your public website.</p></div><Button disabled={busy} onClick={()=>act('backup')}><Archive size={16}/> Back up now</Button></div>{result?.name && <div className="wp-notice"><Check size={18}/><span>Backup created successfully. You can restore it from the list below.</span></div>}<Button size="sm" variant="outline" disabled={busy} onClick={()=>act('backups')}>Refresh backups</Button><div className="wp-backup-list">{backups.map(b=><div className="wp-extension" key={b.name}><div><strong>{new Date(b.created_at*1000).toLocaleString()}</strong><small>{(b.size/1024/1024).toFixed(1)} MB · Files & database</small></div><Button variant="outline" size="sm" disabled={busy} onClick={()=>setConfirm({action:'restore',fields:{backup:b.name,confirm:true},label:'Restore this backup',text:'This replaces your current website files and database. A safety backup of the current site is created first.'})}>Restore</Button></div>)}{!backups.length && !busy && <p className="wp-empty-small">No backups yet. Create your first backup before making big changes.</p>}</div></>}
    {tab==='clone' && <div className="wp-form"><h3>Create a separate copy</h3><p className="wp-help">Start with a staging copy on the same domain, or choose another domain. Your source stays in place. The copy has its own database, keeps your WordPress users, and starts with search indexing disabled.</p><FormField label="Destination domain"><select aria-label="Clone destination" value={target} onChange={e=>setTarget(e.target.value)}><option value="">Choose a domain</option>{domains.filter(d=>d.username===site.username).map(d=><option key={d.domain}>{d.domain}</option>)}</select></FormField><FormField label="Clone address format"><select aria-label="Clone address format" value={targetAddress} onChange={e=>setTargetAddress(e.target.value)}><option value="https">https://</option><option value="https-www">https://www.</option><option value="http">http://</option><option value="http-www">http://www.</option></select></FormField><FormField label="Destination folder (optional)"><Input aria-label="Clone folder" value={folder} onChange={e=>setFolder(e.target.value)} placeholder="For example, staging"/></FormField><div className="wp-url-preview"><Globe size={18}/><span>{targetAddress.startsWith('https')?'https':'http'}://{targetAddress.endsWith('www')?'www.':''}{target}{folder ? '/'+folder : ''}</span></div><Button disabled={busy || !target} onClick={()=>act('clone',{target_domain:target,target_path:folder,target_protocol:targetAddress.startsWith('https')?'https':'http',target_www:targetAddress.endsWith('www')})}><Copy size={16}/> Clone website</Button>{result?.url && <a href={result.url} target="_blank" rel="noopener noreferrer">Open cloned website <ExternalLink size={14}/></a>}</div>}
    {tab==='remove' && <div className="wp-form"><h3>Remove this installation</h3><FormField label="What would you like to remove?"><select aria-label="Removal type" value={removalMode} disabled={busy} onChange={e=>{setRemovalMode(e.target.value);setRemovalConfirmation('')}}><option value="soft">Panel record only — keep my website</option><option value="hard">Website files and database permanently</option></select></FormField><p className="wp-help">{removalMode==='soft'?'Your website stays online. Scan for installations to add it back to the manager.':'This permanently deletes this installation’s files and its account-owned database. Separate nested WordPress sites and existing backups are preserved. Create a backup first.'}</p>{removalMode==='hard' && <FormField label={`Type ${address} to confirm permanent deletion`}><Input aria-label="Confirm installation address" autoComplete="off" value={removalConfirmation} onChange={e=>setRemovalConfirmation(e.target.value)}/></FormField>}<Button variant="danger" disabled={busy || job?.status==='completed' || (removalMode==='hard' && removalConfirmation!==address)} onClick={removeSite}>{removalMode==='soft'?'Remove panel record':'Permanently delete installation'}</Button></div>}
    <ConfirmDialog open={!!confirm} onOpenChange={open=>!open&&setConfirm(null)} title={confirm?.label} description={confirm?.text} onConfirm={()=>{act(confirm.action,confirm.fields,confirm.cli);setConfirm(null)}} />
  </DialogBody><DialogFooter><Button variant="outline" disabled={busy} onClick={onClose}>Done</Button></DialogFooter></DialogContent></Dialog>
}

export default function WordPressManager() {
  const {role,username}=useAuth(); const admin=role==='admin'; const qc=useQueryClient()
  const [scanning,setScanning]=useState(false); const [notice,setNotice]=useState(''); const [refreshing,setRefreshing]=useState(null);
  const [query,setQuery]=useState(''); const [install,setInstall]=useState(false); const [selected,setSelected]=useState(null); const [error,setError]=useState(null); const [loggingIn,setLoggingIn]=useState(null)
  const inventory=useQuery({queryKey:['wordpress-inventory',admin?'all':username],queryFn:()=>get('/api/v1/wordpress'+(!admin?'?username='+encodeURIComponent(username):'')),retry:false,staleTime:15000,refetchInterval:q=>q.state.data?.activity?.some(j=>['pending','running'].includes(j.status))?3000:false})
  const data=inventory.data || {installs:[],domains:[],errors:[]}
  const sites=data.installs.filter(site=>searchScore({label:`${site.domain} ${site.path} ${site.username}`},query)>0)
  const refresh=()=>qc.invalidateQueries({queryKey:['wordpress-inventory']})
  async function scan() {
    setScanning(true); setError(null); setNotice('')
    try {
      const result=await post('/api/v1/wordpress/scan',admin?{}:{username})
      setNotice(`Scan complete. ${result.found} WordPress ${result.found===1?'installation':'installations'} refreshed.`)
      if(result.errors?.length) setError(result.errors.map(e=>`${e.domain}: ${e.message}`).join(' · '))
      await refresh()
    } catch(e) {setError(e)} finally {setScanning(false)}
  }
  async function refreshSite(site) {
    setRefreshing(`${site.username}:${site.id}`); setError(null)
    try { await post(base(site)+'/refresh',{path:site.path}); await refresh(); setNotice(`${site.domain} refreshed from WordPress.`) }
    catch(e) {setError(e)} finally {setRefreshing(null)}
  }
  function login(site) {
    const name='boron-wp-'+Date.now()
    const popup=window.open('',name)
    if(!popup) { setError('Allow pop-ups for this panel, then try Log in again.'); return }
    popup.opener=null; setError(null)
    const form=document.createElement('form');form.method='POST';form.action=base(site)+'/login/open';form.target=name
    const input=document.createElement('input');input.type='hidden';input.name='path';input.value=site.path || '';form.append(input)
    document.body.append(form);form.submit();form.remove()
  }

  return <div className="wordpress-workspace"><PageHeader title={admin?'WordPress Installations':'WordPress Manager'} description={admin?'Every WordPress website on your server, in one place.':'Create, manage, and protect your WordPress websites.'} icon={Globe}><Button variant="outline" onClick={scan} disabled={scanning || inventory.isFetching}><RefreshCw size={16} className={scanning ? "animate-spin" : ""}/> {scanning ? "Scanning…" : "Scan for installations"}</Button><Button onClick={()=>setInstall(true)} disabled={!data.domains.length}><Plus size={17}/> Install WordPress</Button></PageHeader>
    <div className="wp-intro"><div className="wp-mark"><SiWordpress aria-label="WordPress" /></div><div><h2>Your websites. Simply managed.</h2><p>From a first idea to your next launch—install in minutes, log in with a click, and keep a backup close by.</p></div><div className="wp-summary"><strong>{data.installs.length}</strong><span>{data.installs.length===1?'website':'websites'} {admin?'across all accounts':'in your account'}</span></div></div>
    <ErrorNotice error={error || inventory.error}/>{notice && <div className="wp-notice" role="status"><Check size={18}/><span>{notice}</span></div>}{data.errors?.map(e=><ErrorNotice key={e.username} error={`${e.username}: ${e.message}`}/>)}
    <div className="wp-toolbar"><label><Search size={18}/><input aria-label="Search WordPress websites" placeholder={admin?'Search domain or account…':'Find your website…'} value={query} onChange={e=>setQuery(e.target.value)}/>{query&&<button aria-label="Clear website search" onClick={()=>setQuery('')}><X size={16}/></button>}</label><span>{sites.length} {sites.length===1?'website':'websites'}</span></div>
    {inventory.isPending ? <div className="wp-empty" role="status"><Loader2 className="animate-spin"/><h2>Finding your WordPress websites…</h2></div>: sites.length ? <div className="wp-sites">{sites.map(site=><article className="wp-site" key={`${site.username}:${site.id}`}><div className="wp-site-heading"><span className="wp-site-mark"><SiWordpress aria-label="WordPress" /></span><div><h2>{site.domain}{site.path?'/'+site.path:''}</h2><a href={site.url} target="_blank" rel="noopener noreferrer">Visit website <ExternalLink size={12}/></a></div><span className="wp-badge">WordPress {site.wp_version || 'detected'}</span></div><div className="wp-site-details"><span><strong>Account</strong>{site.username}{site.account_status && site.account_status!=='active'?' · '+site.account_status:''}</span><span><strong>Location</strong>{site.path || 'Main website'}</span><span><strong>Administrator</strong>{site.admin_user || 'Managed in WordPress'}</span></div><div className="wp-site-actions"><Button onClick={()=>login(site)} disabled={(site.account_status && site.account_status!=='active') || loggingIn===`${site.username}:${site.id}`}><LogIn size={16}/>{loggingIn===`${site.username}:${site.id}`?'Opening…':'Log in'}</Button><Button variant="outline" disabled={!!site.account_status && site.account_status!=='active'} onClick={()=>setSelected(site)}><Settings size={16}/> Manage website</Button><Button variant="outline" aria-label={`Refresh ${site.domain}${site.path?"/"+site.path:""}`} disabled={!!refreshing || (!!site.account_status && site.account_status!=="active")} onClick={()=>refreshSite(site)}><RefreshCw size={16} className={refreshing===`${site.username}:${site.id}`?"animate-spin":""}/> Refresh</Button></div></article>)}</div>:<div className="wp-empty"><Globe size={42}/><h2>{query?'No matching websites':'Your next website starts here'}</h2><p>{query?'Try a domain name or account name.':data.domains.length?'Install a fresh WordPress website on one of your domains. Existing WordPress sites are discovered automatically.':'Add a hosting domain first, then come back to install WordPress.'}</p>{!query&&data.domains.length>0&&<Button onClick={()=>setInstall(true)}><Plus size={16}/> Install your first WordPress site</Button>}</div>}
    {!!data.activity?.length && <details className="wp-activity"><summary>Recent activity <span>{data.activity.filter(j=>['pending','running'].includes(j.status)).length ? 'Operations in progress' : 'Installations and management history'}</span></summary><div>{data.activity.map(j=><div key={j.id} className="wp-activity-row"><div><strong>{j.label}</strong>{j.error && <p>{j.error}</p>}</div><span className={`wp-job-status ${j.status}`}>{j.status==='completed'?'Completed':j.status==='failed'?'Failed':j.status==='pending'?'Queued':'Running'}</span></div>)}</div></details>}
    <div className="wp-footnote"><ShieldCheck size={16}/><span>Back up before major changes. Your private restore points include website files and the database.</span></div>
    {install&&<InstallWizard domains={data.domains} onClose={()=>setInstall(false)} onInstalled={refresh}/>}{selected&&<SiteManager site={selected} domains={data.domains} onClose={()=>setSelected(null)} onChanged={refresh}/>}
  </div>
}
