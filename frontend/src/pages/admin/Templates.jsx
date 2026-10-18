import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { FileText, Save, RotateCcw, Check } from 'lucide-react'
import { get, put, del } from '@/lib/api'
import { useBranding } from '@/hooks/useBranding'
import { PageHeader } from '@/components/ui/PageHeader'
import { Button } from '@/components/ui/Button'
import { Input, Textarea, FormField } from '@/components/ui/Input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/Tabs'
import { ConfirmDialog } from '@/components/ui/Dialog'
import { DraftChanges, useDraftSection } from '@/components/ui/DraftChanges'
import { TemplatePreview } from '@/components/ui/TemplatePreview'
import { toast } from '@/components/ui/Toast'
import { CenteredSpinner } from '@/components/ui/Spinner'
import { ErrorState } from '@/components/ui/States'
import '@/templates.css'

function PageTemplateEditor({ welcome = false }) {
  const { panelName } = useBranding()
  const qc = useQueryClient()
  const key = welcome ? 'welcome-page-template' : 'suspended-page-template'
  const endpoint = `/api/v1/admin/templates/${welcome ? 'welcome-page' : 'suspended-page'}`
  const label = welcome ? 'Welcome page' : 'Suspension page'
  const [content, setContent] = useState('')
  const [dirty, setDirty] = useState(false)
  const [resetOpen, setResetOpen] = useState(false)
  const { data, isLoading, error, refetch } = useQuery({ queryKey: [key], queryFn: () => get(endpoint) })
  useEffect(() => { if (data && !dirty) setContent(data.content || '') }, [data]) // Refresh saved content without resetting an in-progress draft.
  const invalidate = () => { qc.invalidateQueries({ queryKey: [key] }); if (!welcome) qc.invalidateQueries({ queryKey: ['suspension-designs'] }) }
  const save = useMutation({
    mutationFn: () => put(endpoint, { content }),
    onSuccess: () => { toast.success(welcome ? 'Welcome page saved' : 'Suspension page saved', welcome ? 'New empty domains will use this welcome page.' : 'Every suspended account now shows this page immediately.'); setDirty(false); invalidate() },
    onError: error => toast.error('Could not save', error.message),
  })
  const reset = useMutation({
    mutationFn: () => del(endpoint),
    onSuccess: data => { if (data?.content) setContent(data.content); toast.success('Reverted to the built-in default'); setDirty(false); setResetOpen(false); invalidate() },
    onError: error => toast.error('Could not reset', error.message),
  })
  useDraftSection(key, { dirty, busy: save.isPending || reset.isPending, label, disabled: !content.trim(), onSave: () => save.mutateAsync(), onDiscard: () => { setContent(data?.content || ''); setDirty(false) } })
  if (isLoading) return <CenteredSpinner />
  if (error) return <ErrorState error={error} onRetry={refetch} />
  return <section className="template-section" aria-label={`${label} editor`}>
    <div className="template-section__heading"><h2>{label}</h2><p>{welcome ? 'Shown as welcome.html for a newly created, empty website. Installing a site or adding index.html or index.php removes the managed welcome file.' : 'Shown instead of the website while its hosting account is suspended. Existing website files are kept.'}</p></div>
    <TemplatePreview title={`${label} preview`} content={content} options={welcome ? { replacements: { domain: 'preview.example.com', panel_name: panelName } } : undefined} />
    <details className="template-source"><summary>Edit HTML</summary><div><FormField label={`${label} HTML`} hint={welcome ? 'Use {{domain}} and {{panel_name}}. The preview uses preview.example.com.' : 'Enter a complete HTML page. Saving replaces the active suspension design.'}><Textarea value={content} onChange={event => { setContent(event.target.value); setDirty(true) }} rows={14} spellCheck={false} disabled={save.isPending || reset.isPending} /></FormField></div></details>
    <div className="template-actions"><Button onClick={() => save.mutate()} loading={save.isPending} disabled={!dirty || !content.trim() || reset.isPending}><Save className="h-4 w-4" />{welcome ? 'Save welcome page' : 'Save suspension page'}</Button>{welcome && <Button variant="secondary" onClick={() => setResetOpen(true)} disabled={save.isPending || reset.isPending}><RotateCcw className="h-4 w-4" />Revert to default</Button>}</div>
    {welcome && <ConfirmDialog open={resetOpen} onOpenChange={setResetOpen} title="Reset welcome page?" description="Replace the welcome-page template and any unsaved edits with the built-in default. Existing website files will not change." confirmLabel="Revert to default" variant="primary" loading={reset.isPending} onConfirm={() => reset.mutate()} />}
  </section>
}

function SuspensionDesigns() {
  const qc = useQueryClient()
  const [form, setForm] = useState({ template_key: 'clean', accent_color: '#2563eb', heading: 'Account suspended', message: 'Please contact your hosting provider for assistance.' })
  const [dirty, setDirty] = useState(false)
  const designs = useQuery({ queryKey: ['suspension-designs'], queryFn: () => get('/api/v1/admin/templates/suspension-designs') })
  useEffect(() => { if (designs.data?.current && !dirty) setForm(designs.data.current) }, [designs.data]) // Refresh saved content without resetting an in-progress draft.
  const save = useMutation({
    mutationFn: () => put('/api/v1/admin/templates/suspension-designs', form),
    onSuccess: () => { toast.success('Suspension design applied'); setDirty(false); qc.invalidateQueries({ queryKey: ['suspension-designs'] }); qc.invalidateQueries({ queryKey: ['suspended-page-template'] }) },
    onError: error => toast.error('Could not apply design', error.message),
  })
  const valid = !!form.heading.trim() && !!form.message.trim() && /^#[0-9a-fA-F]{6}$/.test(form.accent_color)
  useDraftSection('suspension-design', { dirty, busy: save.isPending, label: 'Suspension design', disabled: !valid, onSave: () => save.mutateAsync(), onDiscard: () => { if (designs.data?.current) setForm(designs.data.current); setDirty(false) } })
  if (designs.isLoading) return <CenteredSpinner />
  if (designs.error) return <ErrorState error={designs.error} onRetry={designs.refetch} />
  const set = (key, value) => { setForm(previous => ({ ...previous, [key]: value })); setDirty(true) }
  const selected = designs.data?.templates?.find(template => template.key === form.template_key)
  return <section className="template-section" aria-label="Ready-made suspension designs">
    <div className="template-section__heading"><h2>Ready-made suspension designs</h2><p>Choose a layout, then customize its message and color. Preview your changes before applying them.</p></div>
    <div className="template-design-grid">{(designs.data?.templates || []).map(template => <button type="button" key={template.key} aria-pressed={form.template_key === template.key} disabled={save.isPending} onClick={() => set('template_key', template.key)} className="template-design">
      <TemplatePreview thumbnail title={`${template.name} preview`} content={template.html} />
      <span className="template-design__label"><span><strong>{template.name}</strong><small>{template.description}</small></span>{form.template_key === template.key && <Check className="h-4 w-4 shrink-0" aria-hidden="true" />}</span>
    </button>)}</div>
    {selected && <>
      <div className="template-form-grid"><FormField label="Heading"><Input maxLength={160} value={form.heading} onChange={event => set('heading', event.target.value)} disabled={save.isPending} /></FormField><FormField label="Brand color" htmlFor="suspension-color"><div className="template-color-control"><Input id="suspension-color" type="color" value={/^#[0-9a-fA-F]{6}$/.test(form.accent_color) ? form.accent_color : '#2563eb'} onChange={event => set('accent_color', event.target.value)} disabled={save.isPending} /><Input aria-label="Brand color hex value" pattern="#[0-9a-fA-F]{6}" value={form.accent_color} onChange={event => set('accent_color', event.target.value)} disabled={save.isPending} /></div></FormField></div>
      <FormField label="Message"><Textarea maxLength={500} rows={3} value={form.message} onChange={event => set('message', event.target.value)} disabled={save.isPending} /></FormField>
      {dirty && <TemplatePreview title="Selected suspension design preview" content={selected.html} options={{ heading: form.heading, message: form.message, fromColor: designs.data.current.accent_color, toColor: form.accent_color }} />}
    </>}
    {!selected && <p className="template-muted">A custom HTML page is active. Choose a ready-made design to replace it, or edit the current page below.</p>}
    <div className="template-actions"><Button onClick={() => save.mutate()} loading={save.isPending} disabled={!dirty || !valid || !selected}><Save className="h-4 w-4" />Apply design</Button></div>
  </section>
}

const WELCOME_PLACEHOLDERS = ['username', 'primary_domain', 'panel_name']
function WelcomeEmail() {
  const { panelName } = useBranding()
  const qc = useQueryClient()
  const [form, setForm] = useState({ subject: '', body: '' })
  const [dirty, setDirty] = useState(false)
  const [resetOpen, setResetOpen] = useState(false)
  const { data, isLoading, error, refetch } = useQuery({ queryKey: ['welcome-email-template'], queryFn: () => get('/api/v1/admin/templates/welcome-email') })
  useEffect(() => { if (data && !dirty) setForm({ subject: data.subject || '', body: data.body || '' }) }, [data]) // Refresh saved content without resetting an in-progress draft.
  const invalidate = () => qc.invalidateQueries({ queryKey: ['welcome-email-template'] })
  const save = useMutation({ mutationFn: () => put('/api/v1/admin/templates/welcome-email', { subject: form.subject || null, body: form.body || null }), onSuccess: () => { toast.success('Welcome email template saved'); setDirty(false); invalidate() }, onError: error => toast.error('Could not save', error.message) })
  const reset = useMutation({ mutationFn: () => del('/api/v1/admin/templates/welcome-email'), onSuccess: () => { toast.success('Reverted to the built-in default'); setDirty(false); setResetOpen(false); invalidate() }, onError: error => toast.error('Could not reset', error.message) })
  useDraftSection('welcome-email', { dirty, busy: save.isPending || reset.isPending, label: 'Welcome email', onSave: () => save.mutateAsync(), onDiscard: () => { setForm({ subject: data?.subject || '', body: data?.body || '' }); setDirty(false) } })
  if (isLoading) return <CenteredSpinner />
  if (error) return <ErrorState error={error} onRetry={refetch} />
  const sample = value => value.replace(/\{\{\s*(username|primary_domain|panel_name)\s*\}\}/g, (_, key) => ({ username: 'customer', primary_domain: 'example.com', panel_name: panelName })[key])
  return <section className="template-section" aria-label="Welcome email editor">
    <div className="template-section__heading"><h2>Welcome email</h2><p>Sent when an account with a contact email is created, if account notifications are enabled. Leave fields blank to use the default. Passwords are never included.</p></div>
    <FormField label="Subject" optional hint="Blank uses the default account-created subject."><Input value={form.subject} maxLength={200} onChange={event => { setForm(previous => ({ ...previous, subject: event.target.value })); setDirty(true) }} placeholder={`Welcome to ${panelName}!`} disabled={save.isPending || reset.isPending} /></FormField>
    <FormField label="Body" optional hint={`Placeholders: ${WELCOME_PLACEHOLDERS.map(token => `{{${token}}}`).join(', ')}. Blank uses the built-in default text.`}><Textarea value={form.body} maxLength={8000} onChange={event => { setForm(previous => ({ ...previous, body: event.target.value })); setDirty(true) }} rows={8} placeholder={'Hi {{username}},\n\nYour hosting account has been created. Contact your administrator through a secure channel for login credentials.\n\n— {{panel_name}}'} disabled={save.isPending || reset.isPending} /></FormField>
    <div className="template-email-preview" role="region" aria-label="Welcome email preview"><strong>{form.subject || `Your ${panelName} hosting account has been created`}</strong><p>{sample(form.body || "Your hosting account '{{username}}' has been created.\nContact your administrator through a secure channel for your login credentials.")}{`\n\n— ${panelName}`}</p><small>Sample account: customer · example.com</small></div>
    <div className="template-actions"><Button onClick={() => save.mutate()} loading={save.isPending} disabled={!dirty || reset.isPending}><Save className="h-4 w-4" />Save template</Button>{!!(data?.subject || data?.body) && <Button variant="secondary" onClick={() => setResetOpen(true)} disabled={save.isPending || reset.isPending}><RotateCcw className="h-4 w-4" />Revert to default</Button>}</div>
    <ConfirmDialog open={resetOpen} onOpenChange={setResetOpen} title="Reset welcome email?" description="Replace the welcome email subject and body with the built-in default. No email will be sent." confirmLabel="Revert to default" variant="primary" loading={reset.isPending} onConfirm={() => reset.mutate()} />
  </section>
}

export default function Templates() {
  const [tab, setTab] = useState('welcome')
  return <DraftChanges><div className="templates-workspace"><PageHeader title="Templates" description="Preview and customize your domain welcome page, account suspension page and welcome email." icon={FileText} /><Tabs value={tab} onValueChange={setTab}><TabsList aria-label="Template type"><TabsTrigger value="welcome">Welcome page</TabsTrigger><TabsTrigger value="suspension">Suspension page</TabsTrigger><TabsTrigger value="email">Welcome email</TabsTrigger></TabsList><TabsContent forceMount hidden={tab !== 'welcome'} value="welcome"><PageTemplateEditor welcome /></TabsContent><TabsContent forceMount hidden={tab !== 'suspension'} value="suspension"><SuspensionDesigns /><PageTemplateEditor /></TabsContent><TabsContent forceMount hidden={tab !== 'email'} value="email"><WelcomeEmail /></TabsContent></Tabs></div></DraftChanges>
}
