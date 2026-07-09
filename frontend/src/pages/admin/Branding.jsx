import { useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Palette, Save, Trash2, Upload } from 'lucide-react'
import { get, patch, post, del } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { toast } from '@/components/ui/Toast'
import { CenteredSpinner } from '@/components/ui/Spinner'

function AssetCard({ title, description, url, kindLabel, onUpload, onRemove, uploading, removing }) {
  const inputRef = useRef(null)
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="flex items-center gap-4">
        <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-btn border border-border bg-input-surface">
          {url ? (
            <img src={`${url}?t=${Date.now()}`} alt={kindLabel} className="max-h-12 max-w-12 object-contain" />
          ) : (
            <span className="text-xs text-muted-foreground">None</span>
          )}
        </div>
        <div className="flex flex-1 flex-wrap gap-2">
          <input
            ref={inputRef}
            type="file"
            accept="image/png,image/svg+xml,image/x-icon,.ico"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) onUpload(file)
              e.target.value = ''
            }}
          />
          <Button variant="secondary" size="sm" loading={uploading} onClick={() => inputRef.current?.click()}>
            <Upload className="h-4 w-4" /> Upload
          </Button>
          {url && (
            <Button variant="ghost" size="sm" loading={removing} onClick={onRemove}>
              <Trash2 className="h-4 w-4" /> Remove
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

export default function Branding() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['branding'],
    queryFn: () => get('/api/v1/branding'),
  })
  const [form, setForm] = useState(null)
  const active = form ?? {
    panel_name: data?.panel_name || '',
    support_email: data?.support_email || '',
    support_url: data?.support_url || '',
  }

  const invalidate = () => qc.invalidateQueries({ queryKey: ['branding'] })

  const saveMut = useMutation({
    mutationFn: (body) => patch('/api/v1/admin/branding', body),
    onSuccess: () => { toast.success('Branding updated'); invalidate(); setForm(null) },
    onError: (e) => toast.error('Could not save branding', e.message),
  })

  const uploadLogoMut = useMutation({
    mutationFn: (file) => { const fd = new FormData(); fd.append('file', file); return post('/api/v1/admin/branding/logo', fd) },
    onSuccess: () => { toast.success('Logo uploaded'); invalidate() },
    onError: (e) => toast.error('Could not upload logo', e.message),
  })
  const removeLogoMut = useMutation({
    mutationFn: () => del('/api/v1/admin/branding/logo'),
    onSuccess: () => { toast.success('Logo removed'); invalidate() },
    onError: (e) => toast.error('Could not remove logo', e.message),
  })
  const uploadFaviconMut = useMutation({
    mutationFn: (file) => { const fd = new FormData(); fd.append('file', file); return post('/api/v1/admin/branding/favicon', fd) },
    onSuccess: () => { toast.success('Favicon uploaded'); invalidate() },
    onError: (e) => toast.error('Could not upload favicon', e.message),
  })
  const removeFaviconMut = useMutation({
    mutationFn: () => del('/api/v1/admin/branding/favicon'),
    onSuccess: () => { toast.success('Favicon removed'); invalidate() },
    onError: (e) => toast.error('Could not remove favicon', e.message),
  })

  if (isLoading) return <CenteredSpinner label="Loading branding…" />

  const save = () => {
    const body = {}
    if (active.panel_name !== (data?.panel_name || '')) body.panel_name = active.panel_name
    if (active.support_email !== (data?.support_email || '')) body.support_email = active.support_email || null
    if (active.support_url !== (data?.support_url || '')) body.support_url = active.support_url || null
    if (Object.keys(body).length === 0) { toast.info('No changes to save'); return }
    saveMut.mutate(body)
  }

  return (
    <div>
      <PageHeader
        title="Branding"
        description="Customize the panel name, logo, favicon and support contact -- applied to the sidebar, login page, browser tab and email notifications."
        icon={Palette}
      />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Panel name &amp; support</CardTitle>
            <CardDescription>Shown in the sidebar, login page, browser tab title, and appended to outbound emails.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <FormField label="Panel name" required>
              <Input
                value={active.panel_name}
                onChange={(e) => setForm({ ...active, panel_name: e.target.value })}
                maxLength={64}
                placeholder="Forgehost"
              />
            </FormField>
            <FormField label="Support email" hint="Optional -- shown to customers who need help.">
              <Input
                type="email"
                value={active.support_email}
                onChange={(e) => setForm({ ...active, support_email: e.target.value })}
                placeholder="support@example.com"
              />
            </FormField>
            <FormField label="Support URL" hint="Optional -- e.g. a help center or ticket system.">
              <Input
                value={active.support_url}
                onChange={(e) => setForm({ ...active, support_url: e.target.value })}
                placeholder="https://support.example.com"
              />
            </FormField>
            <Button loading={saveMut.isPending} disabled={!active.panel_name.trim()} onClick={save}>
              <Save className="h-4 w-4" /> Save changes
            </Button>
          </CardContent>
        </Card>

        <div className="space-y-6">
          <AssetCard
            title="Logo"
            description="PNG or SVG. Shown in the sidebar and on the login page."
            url={data?.logo_url}
            kindLabel="logo"
            onUpload={(f) => uploadLogoMut.mutate(f)}
            onRemove={() => removeLogoMut.mutate()}
            uploading={uploadLogoMut.isPending}
            removing={removeLogoMut.isPending}
          />
          <AssetCard
            title="Favicon"
            description="PNG, SVG or ICO. Shown in the browser tab."
            url={data?.favicon_url}
            kindLabel="favicon"
            onUpload={(f) => uploadFaviconMut.mutate(f)}
            onRemove={() => removeFaviconMut.mutate()}
            uploading={uploadFaviconMut.isPending}
            removing={removeFaviconMut.isPending}
          />
        </div>
      </div>
    </div>
  )
}
