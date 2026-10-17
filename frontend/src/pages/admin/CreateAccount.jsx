import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Copy, UserPlus } from 'lucide-react'
import { get, post } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { AccountPasswordInput, AccountUsernameInput, PlanLimits } from '@/components/ui/AccountFields'
import { FormActions } from '@/components/ui/FormActions'
import { Select } from '@/components/ui/Select'
import { toast } from '@/components/ui/Toast'
import { waitForControlTask } from '@/lib/control-tasks'

const INITIAL = {
  username: '', primary_domain: '', plan_id: '', email: '', password: '',
  ip_selection: 'automatic', server_ip_id: '',
}

export default function CreateAccount() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [form, setForm] = useState(INITIAL)
  const [created, setCreated] = useState(null)
  const [taskProgress, setTaskProgress] = useState(null)
  const update = (key, value) => setForm(current => ({ ...current, [key]: value }))

  const plansQuery = useQuery({ queryKey: ['plans'], queryFn: () => get('/api/v1/admin/plans') })
  const ipsQuery = useQuery({ queryKey: ['ip-management'], queryFn: () => get('/api/v1/admin/ip-management') })
  const plans = plansQuery.data?.plans || []
  const availableIps = (ipsQuery.data?.ips || []).filter(entry => entry.active && entry.present_on_host)

  const usernameError = form.username && !/^[a-z][a-z0-9]{0,15}$/.test(form.username)
    ? 'Use 1–16 lowercase letters or digits, starting with a letter.' : undefined
  const emailError = form.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email) ? 'Enter an email address such as owner@example.com.' : undefined
  const domainError = form.primary_domain && !/^(?=.{1,253}$)(?!-)[a-z0-9-]+(?:\.[a-z0-9-]+)+$/i.test(form.primary_domain)
    ? 'Enter a valid domain such as example.com.' : undefined
  const passwordError = form.password && !/^(?=.*[a-z])(?=.*[A-Z])(?=.*[0-9])(?=.*[^A-Za-z0-9]).{12,}$/.test(form.password)
    ? 'Use 12+ characters with upper, lower, number, and symbol.' : undefined

  const create = useMutation({
    mutationFn: async () => {
      setTaskProgress(null)
      const task = await post('/api/v1/task-operations/account-create', {
        username: form.username,
        primary_domain: form.primary_domain || undefined,
        plan_id: form.plan_id ? Number(form.plan_id) : undefined,
        email: form.email.trim() || undefined,
        password: form.password || undefined,
        ip_selection: form.ip_selection,
        server_ip_id: form.ip_selection === 'specific' ? Number(form.server_ip_id) : undefined,
      })
      return waitForControlTask(task, { onProgress: setTaskProgress, reveal: true })
    },
    onSuccess: account => {
      setCreated(account)
      qc.invalidateQueries({ queryKey: ['accounts'] })
      if (account.warnings?.length || account.plan_apply_error || account.ip_assignment_error) toast.warning('Account created with an item to review')
      else toast.success('Hosting account created')
    },
  })

  const copy = async (label, value) => {
    try { await navigator.clipboard.writeText(value || ''); toast.success(`${label} copied`) }
    catch { toast.error('Copy failed', 'Select the value and copy it manually.') }
  }

  if (created) return <div className="account-create-page form-design">
    <PageHeader title="Account created" description="Save the one-time credentials before leaving this page." icon={CheckCircle2} />
    <div className="mx-auto max-w-3xl space-y-5">
      {(created.warnings?.length || created.plan_apply_error || created.ip_assignment_error) && <Card className="border-warning/50"><CardContent className="space-y-2 pt-5 text-sm">
        <div className="font-semibold text-foreground">The account is active, but one setting needs attention.</div>
        {created.plan_apply_error && <p className="text-muted-foreground">Plan: {created.plan_apply_error}</p>}
        {created.ip_assignment_error && <p className="text-muted-foreground">IP assignment: {created.ip_assignment_error}</p>}
        {created.warnings?.map((warning) => <p key={warning} className="text-muted-foreground">{warning}</p>)}
      </CardContent></Card>}
      <Card>
        <CardHeader><div><CardTitle>Initial panel login</CardTitle><CardDescription>The initial password is shown only once. Store it in a password manager now.</CardDescription></div></CardHeader>
        <CardContent className="space-y-4">
          {[['Username', created.username], ['Initial password', created.initial_password]].map(([label, value]) => <div key={label}>
            <div className="mb-1.5 text-sm font-medium text-foreground">{label}</div>
            <div className="flex gap-2"><code className="min-w-0 flex-1 select-all break-all rounded-btn border border-border bg-muted px-3 py-2.5 text-sm text-foreground">{value}</code><Button type="button" variant="outline" onClick={() => copy(label, value)}><Copy className="h-4 w-4" />Copy</Button></div>
          </div>)}
        </CardContent>
      </Card>
      <div className="flex justify-end gap-3"><Button variant="secondary" asChild><Link to="/accounts">Back to accounts</Link></Button><Button onClick={() => navigate(`/accounts/${created.username}`)}>Open account</Button></div>
    </div>
  </div>

  return <div className="account-create-page form-design">
    <PageHeader title="Create account" description="Set up a customer login, website and hosting plan." icon={UserPlus} />
    <form onInvalidCapture={event => { const details = event.target.closest('details'); if (details) details.open = true }} onSubmit={event => { event.preventDefault(); create.mutate() }} className="account-create-form">
      <div className="account-create-sections">
        <section className="account-create-section">
          <div className="account-create-section-heading"><h2>Account identity</h2><p>Login and contact details for the hosting customer.</p></div>
          <div className="account-create-fields">
            <FormField label="Username" required error={usernameError}><AccountUsernameInput autoFocus required pattern="[a-z][a-z0-9]{0,15}" placeholder="acme1" value={form.username} onChange={e => update('username', e.target.value)} /></FormField>
            <FormField error={emailError} label="Contact email" optional hint="For account notifications."><Input type="email" placeholder="owner@example.com" value={form.email} onChange={e => update('email', e.target.value)} /></FormField>
            <FormField className="account-create-wide" label="Initial password" optional hint="12+ characters with upper/lowercase, a number and a symbol." error={passwordError}><AccountPasswordInput type="password" autoComplete="new-password" placeholder="Automatically generated if blank" value={form.password} onChange={e => update('password', e.target.value)} pattern={form.password ? '(?=.*[a-z])(?=.*[A-Z])(?=.*[0-9])(?=.*[^A-Za-z0-9]).{12,}' : undefined} /></FormField>
          </div>
        </section>
        <section className="account-create-section">
          <div className="account-create-section-heading"><h2>Website and package</h2><p>Create a primary domain now and apply a predefined hosting plan.</p></div>
          <div className="account-create-fields">
            <FormField label="Primary domain" optional hint="You can add a domain later." error={domainError}><Input placeholder="example.com" value={form.primary_domain} onChange={e => update('primary_domain', e.target.value)} pattern="(?=.{1,253}$)(?!-)[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+" /></FormField>
            <div><FormField label="Hosting plan" optional><Select value={form.plan_id} onChange={e => update('plan_id', e.target.value)}><option value="">Default server limits</option>{plans.map(plan => <option key={plan.id} value={plan.id}>{plan.name}</option>)}</Select></FormField><PlanLimits plan={plans.find(plan => String(plan.id) === form.plan_id)} /></div>
          </div>
        </section>
      </div>
      <details className="form-advanced">
        <summary>Advanced options<span>Server address</span></summary>
        <div className="account-create-section">
        <div className="account-create-section-heading"><h2>Server address</h2><p>Automatic assignment uses your server policy.</p></div>
        <div className="account-create-fields">
          <FormField label="IP assignment"><Select value={form.ip_selection} onChange={e => update('ip_selection', e.target.value)}><option value="automatic">Automatic (server policy)</option><option value="primary">Primary server IP</option><option value="random">Random shared IP</option><option value="specific">Choose a specific IP</option></Select></FormField>
          {form.ip_selection === 'specific' && <FormField label="Server IP" required><Select required value={form.server_ip_id} onChange={e => update('server_ip_id', e.target.value)}><option value="">Choose an IP…</option>{availableIps.map(entry => <option key={entry.id} value={entry.id}>{entry.address} — {entry.allocation_mode}{entry.label ? ` · ${entry.label}` : ''}</option>)}</Select></FormField>}
        </div>
        </div>
      </details>
      <FormActions className="account-create-actions">
        <div className="mr-auto min-w-0 text-sm">
          {create.isPending && <p role="status" className="text-muted-foreground">{taskProgress?.progress_message || `Creating ${form.username || 'hosting account'}… this can take up to 30 seconds. Do not close this page.`}</p>}
          {!create.isPending && create.error && <p role="alert" className="text-danger">{create.error.message}</p>}
        </div>
        {create.isPending ? <Button type="button" variant="secondary" disabled>Cancel</Button> : <Button type="button" variant="secondary" asChild><Link to="/accounts">Cancel</Link></Button>}
        <Button type="submit" size="lg" loading={create.isPending} disabled={!!usernameError || !!domainError || !!passwordError || !!emailError}>Create account</Button>
      </FormActions>
    </form>
  </div>
}
