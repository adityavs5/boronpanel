import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Cpu, Database, Gauge, Layers, Mail, Save, Server } from 'lucide-react'
import { get, patch, post } from '@/lib/api'
import { PLAN_PRESETS } from './Plans'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Switch } from '@/components/ui/Toggle'
import { CenteredSpinner } from '@/components/ui/Spinner'
import { ErrorState } from '@/components/ui/States'
import { toast } from '@/components/ui/Toast'

const EMPTY = { name: '', cpu_cores: .5, mem_mb: 1024, io_mb: 50, pids_max: 100, quota_soft_mb: 10240, quota_hard_mb: 12288, bandwidth_limit_mb: '', database_limit: '', email_account_limit: '', subdomain_limit: '', ftp_account_limit: '', app_limit: '', redis_enabled: true }
const optional = ['bandwidth_limit_mb', 'database_limit', 'email_account_limit', 'subdomain_limit', 'ftp_account_limit', 'app_limit']
function NumberField({ form, setForm, name, label, min = 1, step = 1, placeholder, hint }) { return <FormField label={label} hint={hint}><Input type="number" min={min} step={step} placeholder={placeholder} value={form[name]} onChange={event => setForm(value => ({ ...value, [name]: event.target.value }))} /></FormField> }
function Section({ icon: Icon, title, description, children }) { return <Card className="plan-editor-section"><CardHeader><div><CardTitle className="flex items-center gap-2"><Icon className="h-5 w-5" />{title}</CardTitle><CardDescription>{description}</CardDescription></div></CardHeader><CardContent><div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">{children}</div></CardContent></Card> }
function fromPlan(plan) { const result = { ...EMPTY, ...plan, cpu_cores: plan.cpu_cores ?? plan.cpu_pct / 100 }; optional.forEach(key => { result[key] = plan[key] ?? '' }); return result }
function payload(form) { const body = { name: form.name.trim(), cpu_pct: Math.round(Number(form.cpu_cores) * 100), mem_mb: Number(form.mem_mb), io_mb: Number(form.io_mb), pids_max: Number(form.pids_max), quota_soft_mb: Number(form.quota_soft_mb), quota_hard_mb: Number(form.quota_hard_mb), redis_enabled: !!form.redis_enabled }; optional.forEach(key => { body[key] = form[key] === '' ? null : Number(form[key]) }); return body }

export default function PlanEditor() {
  const { planId } = useParams(); const editing = !!planId; const navigate = useNavigate(); const qc = useQueryClient()
  const [form, setForm] = useState({ ...EMPTY }); const [template, setTemplate] = useState('wordpress')
  const query = useQuery({ queryKey: ['plan', planId], queryFn: () => get(`/api/v1/admin/plans/${planId}`), enabled: editing })
  useEffect(() => { if (query.data) setForm(fromPlan(query.data)); else if (!editing) setForm(fromPlan(PLAN_PRESETS.wordpress.values)) }, [query.data, editing])
  const save = useMutation({ mutationFn: body => editing ? patch(`/api/v1/admin/plans/${planId}`, body) : post('/api/v1/admin/plans', body), onSuccess: () => { toast.success(editing ? 'Plan updated' : 'Plan created'); qc.invalidateQueries({ queryKey: ['plans'] }); navigate('/plans') }, onError: error => toast.error('Could not save plan', error.message) })
  if (editing && query.isLoading) return <CenteredSpinner />
  if (editing && query.error) return <ErrorState error={query.error} onRetry={query.refetch} />
  return <form className="plan-editor" onSubmit={event => { event.preventDefault(); save.mutate(payload(form)) }}>
    <PageHeader title={editing ? `Edit ${query.data?.name || 'plan'}` : 'Create Hosting Plan'} description="Set account resources and service limits in clearly separated sections." icon={Layers}><Button asChild variant="secondary"><Link to="/plans"><ArrowLeft className="h-4 w-4" /> Plans</Link></Button><Button type="submit" loading={save.isPending} disabled={!form.name.trim()}><Save className="h-4 w-4" /> Save plan</Button></PageHeader>
    {!editing && <Section icon={Layers} title="Plan template" description="Start with sensible values, then adjust any limit."><FormField label="Starting template"><Select value={template} onChange={event => { const id = event.target.value; setTemplate(id); if (PLAN_PRESETS[id]) setForm(fromPlan(PLAN_PRESETS[id].values)) }}>{Object.entries(PLAN_PRESETS).map(([id, item]) => <option key={id} value={id}>{item.label} — {item.description}</option>)}</Select></FormField><FormField label="Plan name"><Input autoFocus required value={form.name} onChange={event => setForm(value => ({ ...value, name: event.target.value }))} /></FormField></Section>}
    {editing && <Section icon={Layers} title="Plan identity" description="The name administrators see when assigning this package."><FormField label="Plan name"><Input autoFocus required value={form.name} onChange={event => setForm(value => ({ ...value, name: event.target.value }))} /></FormField></Section>}
    <Section icon={Cpu} title="Compute resources" description="Limits enforced for this account’s application processes."><NumberField form={form} setForm={setForm} name="cpu_cores" label="CPU cores" min={.01} step={.25} /><NumberField form={form} setForm={setForm} name="mem_mb" label="Memory (MB)" min={64} /><NumberField form={form} setForm={setForm} name="pids_max" label="Maximum processes" min={10} /></Section>
    <Section icon={Gauge} title="Storage and traffic" description="Disk allocation, throughput, and monthly transfer."><NumberField form={form} setForm={setForm} name="quota_soft_mb" label="Disk soft quota (MB)" /><NumberField form={form} setForm={setForm} name="quota_hard_mb" label="Disk hard quota (MB)" /><NumberField form={form} setForm={setForm} name="io_mb" label="Disk I/O (MB/s)" /><NumberField form={form} setForm={setForm} name="bandwidth_limit_mb" label="Bandwidth (MB/month)" placeholder="Unlimited" hint="Blank means unlimited." /></Section>
    <Section icon={Server} title="Websites and applications" description="How many hosted resources an account may create."><NumberField form={form} setForm={setForm} name="subdomain_limit" label="Subdomains" placeholder="Unlimited" /><NumberField form={form} setForm={setForm} name="ftp_account_limit" label="FTP accounts" placeholder="Unlimited" /><NumberField form={form} setForm={setForm} name="app_limit" label="Node.js / Python apps" placeholder="Unlimited" /></Section>
    <Section icon={Mail} title="Email" description="Mailbox allowance for domains owned by the account."><NumberField form={form} setForm={setForm} name="email_account_limit" label="Email accounts" placeholder="Unlimited" /></Section>
    <Section icon={Database} title="Databases and software" description="Database allowance and managed software defaults."><NumberField form={form} setForm={setForm} name="database_limit" label="Databases" placeholder="Unlimited" /><label className="plan-switch"><span><strong>Redis</strong><small>Provision an isolated Redis instance when this plan is applied.</small></span><Switch checked={form.redis_enabled} onCheckedChange={checked => setForm(value => ({ ...value, redis_enabled: checked }))} /></label></Section>
    <div className="plan-editor-actions"><Button asChild variant="secondary"><Link to="/plans">Cancel</Link></Button><Button type="submit" loading={save.isPending} disabled={!form.name.trim()}><Save className="h-4 w-4" /> Save plan</Button></div>
  </form>
}
