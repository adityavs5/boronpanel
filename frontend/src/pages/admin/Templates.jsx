import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { FileText, Save, RotateCcw, Ban, Palette, Check } from 'lucide-react'
import { get, put, del } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardContent, CardHeader, CardTitle, CardDescription, CardFooter } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input, Textarea, FormField } from '@/components/ui/Input'
import { toast } from '@/components/ui/Toast'
import { CenteredSpinner } from '@/components/ui/Spinner'
import { ErrorState } from '@/components/ui/States'

// QA round 2, item 10: admin-editable suspension page + welcome email
// template. Both are server-wide (not per-account/per-domain) -- distinct
// from the per-domain custom error pages (Firewall-adjacent Security tab).

function SuspensionDesignCard() {
  const qc = useQueryClient()
  const [form, setForm] = useState({ template_key: 'clean', accent_color: '#2563eb', heading: 'Account suspended', message: 'Please contact your hosting provider for assistance.' })
  const [dirty, setDirty] = useState(false)
  const designs = useQuery({ queryKey: ['suspension-designs'], queryFn: () => get('/api/v1/admin/templates/suspension-designs') })
  useEffect(() => {
    if (designs.data?.current && !dirty) setForm(designs.data.current)
  }, [designs.data]) // eslint-disable-line react-hooks/exhaustive-deps
  const save = useMutation({
    mutationFn: () => put('/api/v1/admin/templates/suspension-designs', form),
    onSuccess: () => { toast.success('Suspension design applied'); setDirty(false); qc.invalidateQueries({ queryKey: ['suspension-designs'] }); qc.invalidateQueries({ queryKey: ['suspended-page-template'] }) },
    onError: error => toast.error('Could not apply design', error.message),
  })
  if (designs.isLoading) return <CenteredSpinner />
  if (designs.error) return <ErrorState error={designs.error} onRetry={designs.refetch} />
  const set = (key, value) => { setForm(previous => ({ ...previous, [key]: value })); setDirty(true) }
  return <Card>
    <CardHeader><CardTitle className="flex items-center gap-2"><Palette className="h-4 w-4" />Ready-made suspension designs</CardTitle><CardDescription>Choose a responsive page, customize its message and brand color, then apply it to every suspended website.</CardDescription></CardHeader>
    <CardContent className="space-y-5">
      <div className="grid gap-4 sm:grid-cols-2">{(designs.data?.templates || []).map(template => <button type="button" key={template.key} onClick={() => set('template_key', template.key)} className={`overflow-hidden rounded-panel border text-left transition ${form.template_key === template.key ? 'border-accent ring-2 ring-ring/30' : 'border-border hover:border-accent/50'}`}>
        <iframe title={`${template.name} preview`} srcDoc={template.html} sandbox="" tabIndex="-1" className="pointer-events-none h-40 w-full border-0 bg-white" />
        <span className="flex items-start justify-between gap-3 p-3"><span><strong className="block text-sm">{template.name}</strong><span className="mt-1 block text-xs text-muted-foreground">{template.description}</span></span>{form.template_key === template.key && <Check className="h-4 w-4 shrink-0 text-accent" />}</span>
      </button>)}</div>
      <div className="grid gap-4 sm:grid-cols-2"><FormField label="Heading" htmlFor="suspension-heading"><Input id="suspension-heading" maxLength={160} value={form.heading} onChange={event => set('heading', event.target.value)} /></FormField><FormField label="Brand color" htmlFor="suspension-color"><div className="flex gap-2"><Input id="suspension-color" type="color" value={form.accent_color} onChange={event => set('accent_color', event.target.value)} className="w-16 p-1" /><Input aria-label="Brand color hex value" pattern="#[0-9a-fA-F]{6}" value={form.accent_color} onChange={event => set('accent_color', event.target.value)} /></div></FormField></div>
      <FormField label="Message" htmlFor="suspension-message"><Textarea id="suspension-message" maxLength={500} rows={3} value={form.message} onChange={event => set('message', event.target.value)} /></FormField>
    </CardContent><CardFooter><Button onClick={() => save.mutate()} loading={save.isPending} disabled={!dirty || !form.heading.trim() || !form.message.trim()}><Save className="h-4 w-4" />Apply design</Button></CardFooter>
  </Card>
}

function SuspendedPageCard() {
  const qc = useQueryClient()
  const [content, setContent] = useState('')
  const [dirty, setDirty] = useState(false)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['suspended-page-template'],
    queryFn: () => get('/api/v1/admin/templates/suspended-page'),
  })

  useEffect(() => {
    if (data && !dirty) setContent(data.content)
  }, [data]) // eslint-disable-line react-hooks/exhaustive-deps

  const saveMut = useMutation({
    mutationFn: () => put('/api/v1/admin/templates/suspended-page', { content }),
    onSuccess: () => {
      toast.success('Suspension page saved', 'Every suspended account now shows this page immediately.')
      setDirty(false)
      qc.invalidateQueries({ queryKey: ['suspended-page-template'] })
    },
    onError: (e) => toast.error('Could not save', e.message),
  })

  if (isLoading) return <CenteredSpinner />
  if (error) return <ErrorState error={error} onRetry={refetch} />

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Ban className="h-4 w-4" /> Suspension page</CardTitle>
        <CardDescription>
          Shown for every request to a suspended account's site, across all domains. Full HTML — write a complete page.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Textarea
          value={content}
          onChange={(e) => { setContent(e.target.value); setDirty(true) }}
          rows={16}
          className="font-mono text-xs"
          spellCheck={false}
        />
      </CardContent>
      <CardFooter>
        <Button onClick={() => saveMut.mutate()} loading={saveMut.isPending} disabled={!content.trim()}>
          <Save className="h-4 w-4" /> Save suspension page
        </Button>
      </CardFooter>
    </Card>
  )
}

const WELCOME_PLACEHOLDERS = ['username', 'password', 'primary_domain', 'panel_name']

function WelcomeEmailCard() {
  const qc = useQueryClient()
  const [form, setForm] = useState({ subject: '', body: '' })
  const [dirty, setDirty] = useState(false)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['welcome-email-template'],
    queryFn: () => get('/api/v1/admin/templates/welcome-email'),
  })

  useEffect(() => {
    if (data && !dirty) setForm({ subject: data.subject || '', body: data.body || '' })
  }, [data]) // eslint-disable-line react-hooks/exhaustive-deps

  const invalidate = () => qc.invalidateQueries({ queryKey: ['welcome-email-template'] })

  const saveMut = useMutation({
    mutationFn: () => put('/api/v1/admin/templates/welcome-email', { subject: form.subject || null, body: form.body || null }),
    onSuccess: () => { toast.success('Welcome email template saved'); setDirty(false); invalidate() },
    onError: (e) => toast.error('Could not save', e.message),
  })

  const resetMut = useMutation({
    mutationFn: () => del('/api/v1/admin/templates/welcome-email'),
    onSuccess: () => { toast.success('Reverted to the built-in default'); setDirty(false); invalidate() },
    onError: (e) => toast.error('Could not reset', e.message),
  })

  if (isLoading) return <CenteredSpinner />
  if (error) return <ErrorState error={error} onRetry={refetch} />

  const usingCustom = !!(data?.subject || data?.body)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><FileText className="h-4 w-4" /> Welcome email</CardTitle>
        <CardDescription>
          Sent when a new hosting account is created (if a contact email is set). Leave blank to use the built-in default.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <FormField label="Subject" hint="Blank uses the default: “Your <panel> hosting account has been created”.">
          <Input
            value={form.subject}
            onChange={(e) => { setForm((f) => ({ ...f, subject: e.target.value })); setDirty(true) }}
            placeholder="Welcome to {{panel_name}}!"
          />
        </FormField>
        <FormField
          label="Body"
          hint={`Placeholders: ${WELCOME_PLACEHOLDERS.map((p) => `{{${p}}}`).join(', ')}. Blank uses the built-in default text.`}
        >
          <Textarea
            value={form.body}
            onChange={(e) => { setForm((f) => ({ ...f, body: e.target.value })); setDirty(true) }}
            rows={8}
            placeholder={'Hi {{username}},\n\nYour hosting account has been created. Your temporary password is {{password}}.\n\n— {{panel_name}}'}
          />
        </FormField>
      </CardContent>
      <CardFooter className="flex gap-2">
        <Button onClick={() => saveMut.mutate()} loading={saveMut.isPending}>
          <Save className="h-4 w-4" /> Save template
        </Button>
        {usingCustom && (
          <Button variant="secondary" loading={resetMut.isPending} onClick={() => resetMut.mutate()}>
            <RotateCcw className="h-4 w-4" /> Revert to default
          </Button>
        )}
      </CardFooter>
    </Card>
  )
}

export default function Templates() {
  return (
    <div>
      <PageHeader title="Templates" description="Suspension page and welcome email, applied on suspend and account creation." icon={FileText} />
      <div className="max-w-3xl space-y-6">
        <SuspensionDesignCard />
        <SuspendedPageCard />
        <WelcomeEmailCard />
      </div>
    </div>
  )
}
