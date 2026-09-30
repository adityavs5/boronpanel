import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, CheckCircle2, Copy, Globe2, Server, UserPlus } from 'lucide-react'
import { get, post } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { toast } from '@/components/ui/Toast'

const INITIAL = {
  username: '', primary_domain: '', plan_id: '', email: '', password: '',
  ip_selection: 'automatic', server_ip_id: '',
}

export default function CreateAccount() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [form, setForm] = useState(INITIAL)
  const [created, setCreated] = useState(null)
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
    mutationFn: () => post('/api/v1/accounts', {
      username: form.username,
      primary_domain: form.primary_domain || undefined,
      plan_id: form.plan_id ? Number(form.plan_id) : undefined,
      email: form.email.trim() || undefined,
      password: form.password || undefined,
      ip_selection: form.ip_selection,
      server_ip_id: form.ip_selection === 'specific' ? Number(form.server_ip_id) : undefined,
    }),
    onSuccess: account => {
      setCreated(account)
      qc.invalidateQueries({ queryKey: ['accounts'] })
      if (account.plan_apply_error || account.ip_assignment_error) toast.warning('Account created with an item to review')
      else toast.success('Hosting account created')
    },
    onError: error => toast.error('Could not create account', error.message),
  })

  const copy = async (label, value) => {
    try { await navigator.clipboard.writeText(value || ''); toast.success(`${label} copied`) }
    catch { toast.error('Copy failed', 'Select the value and copy it manually.') }
  }

  if (created) return <div>
    <PageHeader title="Account created" description="Save the one-time credentials before leaving this page." icon={CheckCircle2} />
    <div className="mx-auto max-w-3xl space-y-5">
      {(created.plan_apply_error || created.ip_assignment_error) && <Card className="border-warning/50"><CardContent className="space-y-2 pt-5 text-sm">
        <div className="font-semibold text-foreground">The account is active, but one setting needs attention.</div>
        {created.plan_apply_error && <p className="text-muted-foreground">Plan: {created.plan_apply_error}</p>}
        {created.ip_assignment_error && <p className="text-muted-foreground">IP assignment: {created.ip_assignment_error}</p>}
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

  return <div>
    <PageHeader title="Create hosting account" description="Provision the customer identity, primary website, package limits, and server address." icon={UserPlus}>
      <Button variant="secondary" asChild><Link to="/accounts"><ArrowLeft className="h-4 w-4" />Accounts</Link></Button>
    </PageHeader>
    <form onSubmit={event => { event.preventDefault(); create.mutate() }} className="space-y-6">
      <div className="grid gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader><div><CardTitle className="flex items-center gap-2"><UserPlus className="h-5 w-5 text-accent" />Account identity</CardTitle><CardDescription>Login and contact details for the hosting customer.</CardDescription></div></CardHeader>
          <CardContent className="grid gap-5 md:grid-cols-2">
            <FormField label="Username" required hint="Lowercase letters and digits; maximum 16 characters." error={usernameError}><Input autoFocus required pattern="[a-z][a-z0-9]{0,15}" placeholder="acme1" value={form.username} onChange={e => update('username', e.target.value)} /></FormField>
            <FormField error={emailError} label="Contact email" hint="Used for welcome and account notifications."><Input type="email" placeholder="owner@example.com" value={form.email} onChange={e => update('email', e.target.value)} /></FormField>
            <FormField className="md:col-span-2" label="Initial password" hint="Leave blank to generate a strong password automatically." error={passwordError}><Input type="password" autoComplete="new-password" placeholder="Automatically generated if blank" value={form.password} onChange={e => update('password', e.target.value)} pattern={form.password ? '(?=.*[a-z])(?=.*[A-Z])(?=.*[0-9])(?=.*[^A-Za-z0-9]).{12,}' : undefined} /></FormField>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><div><CardTitle className="flex items-center gap-2"><Globe2 className="h-5 w-5 text-accent" />Website and package</CardTitle><CardDescription>Create a primary domain now and apply a predefined hosting plan.</CardDescription></div></CardHeader>
          <CardContent className="grid gap-5 md:grid-cols-2">
            <FormField label="Primary domain" hint="Optional; a domain can be added later." error={domainError}><Input placeholder="example.com" value={form.primary_domain} onChange={e => update('primary_domain', e.target.value)} pattern="(?=.{1,253}$)(?!-)[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+" /></FormField>
            <FormField label="Hosting plan" hint="Applies limits immediately after creation."><Select value={form.plan_id} onChange={e => update('plan_id', e.target.value)}><option value="">Default server limits</option>{plans.map(plan => <option key={plan.id} value={plan.id}>{plan.name}</option>)}</Select></FormField>
          </CardContent>
        </Card>
      </div>
      <Card>
        <CardHeader><div><CardTitle className="flex items-center gap-2"><Server className="h-5 w-5 text-accent" />Server address</CardTitle><CardDescription>Automatic assignment follows the policy configured in IP Management.</CardDescription></div></CardHeader>
        <CardContent className="grid gap-5 md:grid-cols-2">
          <FormField label="IP assignment"><Select value={form.ip_selection} onChange={e => update('ip_selection', e.target.value)}><option value="automatic">Automatic (server policy)</option><option value="primary">Primary server IP</option><option value="random">Random shared IP</option><option value="specific">Choose a specific IP</option></Select></FormField>
          {form.ip_selection === 'specific' && <FormField label="Server IP" required><Select required value={form.server_ip_id} onChange={e => update('server_ip_id', e.target.value)}><option value="">Choose an IP…</option>{availableIps.map(entry => <option key={entry.id} value={entry.id}>{entry.address} — {entry.allocation_mode}{entry.label ? ` · ${entry.label}` : ''}</option>)}</Select></FormField>}
        </CardContent>
      </Card>
      <div className="flex items-center justify-end gap-3 border-t border-border pt-5"><Button type="button" variant="secondary" asChild><Link to="/accounts">Cancel</Link></Button><Button type="submit" loading={create.isPending} disabled={!!usernameError || !!domainError || !!passwordError || !!emailError}>Create hosting account</Button></div>
    </form>
  </div>
}
