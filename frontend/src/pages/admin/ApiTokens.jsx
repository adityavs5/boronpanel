import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { KeyRound, Plus, Trash2, Copy, Check, AlertTriangle } from 'lucide-react'
import { get, post, del } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { copyToClipboard } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { StatusBadge } from '@/components/ui/StatusBadge'
import { Input, FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter,
  ConfirmDialog,
} from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'

const EMPTY_FORM = { label: '', role: 'admin', account_id: '' }

export default function ApiTokens() {
  const username = useAccountUsername()
  const qc = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [form, setForm] = useState(EMPTY_FORM)
  const [newToken, setNewToken] = useState(null)
  const [copied, setCopied] = useState(false)
  const [toRevoke, setToRevoke] = useState(null)

  const invalidate = () => qc.invalidateQueries({ queryKey: ['api-tokens', username] })

  // GET /api/v1/tokens returns a BARE array: [{id,label,role,account_id,revoked}]
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['api-tokens', username],
    queryFn: () => get('/api/v1/tokens'),
  })

  const createMut = useMutation({
    mutationFn: (body) => post('/api/v1/tokens', body),
    onSuccess: (res) => {
      toast.success('Token created', 'Copy the token now — it will not be shown again.')
      invalidate()
      setCreateOpen(false)
      setForm(EMPTY_FORM)
      setCopied(false)
      setNewToken(res?.token || null)
    },
    onError: (e) => toast.error('Could not create token', e.message),
  })

  const revokeMut = useMutation({
    mutationFn: (r) => del(`/api/v1/tokens/${r.id}`),
    onSuccess: () => {
      toast.success('Token revoked', 'Requests using this token will now be rejected.')
      invalidate()
      setToRevoke(null)
    },
    onError: (e) => toast.error('Could not revoke token', e.message),
  })

  const handleCopy = async () => {
    if (!newToken) return
    const ok = await copyToClipboard(newToken)
    if (ok) {
      setCopied(true)
      toast.success('Token copied to clipboard')
      setTimeout(() => setCopied(false), 2000)
    } else {
      toast.error('Could not copy', 'Select the token and copy it manually.')
    }
  }

  const submitCreate = (e) => {
    e.preventDefault()
    const body = { label: form.label.trim(), role: form.role }
    const id = Number.parseInt(form.account_id, 10)
    if (form.role === 'customer' && Number.isInteger(id) && id > 0) body.account_id = id
    createMut.mutate(body)
  }

  const columns = [
    {
      key: 'label',
      header: 'Label',
      sortable: true,
      searchable: true,
      render: (r) => <span className="font-medium text-foreground">{r.label}</span>,
    },
    {
      key: 'role',
      header: 'Role',
      sortable: true,
      searchable: true,
      render: (r) => <Badge variant={r.role === 'admin' ? 'accent' : 'neutral'}>{r.role}</Badge>,
    },
    {
      key: 'account_id',
      header: 'Scope',
      sortable: true,
      render: (r) =>
        r.account_id != null ? (
          <span className="tabular-nums text-foreground">account #{r.account_id}</span>
        ) : (
          <span className="text-muted-foreground">Global</span>
        ),
    },
    {
      key: 'revoked',
      header: 'Status',
      sortable: true,
      sortValue: (r) => (r.revoked ? 1 : 0),
      render: (r) => <StatusBadge status={r.revoked ? 'revoked' : 'active'} />,
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      searchable: false,
      render: (r) =>
        r.revoked ? (
          <span className="text-muted-foreground">—</span>
        ) : (
          <div className="flex justify-end">
            <Button
              variant="danger"
              size="sm"
              onClick={() => setToRevoke(r)}
            >
              <Trash2 className="h-4 w-4" /> Revoke
            </Button>
          </div>
        ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="API tokens"
        description="Bearer tokens for machine-to-machine REST API access — e.g. a billing system. Send as an Authorization: Bearer <token> header."
        icon={KeyRound}
      >
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" /> Create token
        </Button>
      </PageHeader>

      <DataTable
        columns={columns}
        data={data}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        filterable
        searchPlaceholder="Search tokens…"
        pageSize={15}
        getRowKey={(r) => r.id}
        initialSort={{ key: 'revoked', dir: 'asc' }}
        emptyTitle="No API tokens yet"
        emptyDescription="Create a token to grant a machine or external system programmatic access to the API."
        emptyIcon={KeyRound}
        emptyAction={<Button onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4" /> Create token</Button>}
      />

      {/* Create token dialog */}
      <Dialog open={createOpen} onOpenChange={(v) => { setCreateOpen(v); if (!v) setForm(EMPTY_FORM) }}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>Create API token</DialogTitle>
            <DialogDescription>
              The plaintext token is shown once, immediately after creation. It cannot be retrieved later.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={submitCreate}>
            <DialogBody className="space-y-4">
              <FormField label="Label" required error={createMut.error?.fields?.label} hint="A name to help you recognise this token later.">
                <Input
                  autoFocus
                  value={form.label}
                  onChange={(e) => setForm((f) => ({ ...f, label: e.target.value }))}
                  placeholder="billing-system"
                  required
                />
              </FormField>
              <FormField label="Role" required error={createMut.error?.fields?.role} hint="Admin tokens can call every endpoint; customer tokens are scoped to one account.">
                <Select
                  value={form.role}
                  onChange={(e) => setForm((f) => ({ ...f, role: e.target.value }))}
                >
                  <option value="admin">admin</option>
                  <option value="customer">customer</option>
                </Select>
              </FormField>
              {form.role === 'customer' && (
                <FormField label="Account ID" error={createMut.error?.fields?.account_id} hint="Numeric account id this customer token is scoped to. Leave blank for none.">
                  <Input
                    type="number"
                    min="1"
                    value={form.account_id}
                    onChange={(e) => setForm((f) => ({ ...f, account_id: e.target.value }))}
                    placeholder="42"
                    className="tabular-nums"
                  />
                </FormField>
              )}
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => setCreateOpen(false)}>Cancel</Button>
              <Button type="submit" loading={createMut.isPending} disabled={!form.label.trim()}>Create token</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Reveal newly-created token (shown once) */}
      <Dialog open={!!newToken} onOpenChange={(v) => { if (!v) { setNewToken(null); setCopied(false) } }}>
        <DialogContent size="md">
          <DialogHeader>
            <DialogTitle>Token created</DialogTitle>
            <DialogDescription>
              Copy this token now and store it securely.
            </DialogDescription>
          </DialogHeader>
          <DialogBody className="space-y-4">
            <div className="flex items-start gap-2 rounded-btn border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-foreground">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
              <span>This is the only time the token is shown. It cannot be retrieved again — if you lose it, revoke it and create a new one.</span>
            </div>
            <div className="flex items-center gap-2">
              <code className="min-w-0 flex-1 break-all rounded-btn bg-muted px-3 py-2 font-mono text-sm text-foreground">
                {newToken}
              </code>
              <Button type="button" variant="outline" size="sm" onClick={handleCopy}>
                {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                {copied ? 'Copied' : 'Copy'}
              </Button>
            </div>
          </DialogBody>
          <DialogFooter>
            <Button type="button" onClick={() => { setNewToken(null); setCopied(false) }}>Done</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Revoke confirmation */}
      <ConfirmDialog
        open={!!toRevoke}
        onOpenChange={(v) => { if (!v) setToRevoke(null) }}
        title={toRevoke ? `Revoke “${toRevoke.label}”?` : 'Revoke token?'}
        description="Any client using this token will immediately lose API access. This cannot be undone — you will need to create a new token to restore access."
        confirmLabel="Revoke token"
        loading={revokeMut.isPending}
        onConfirm={() => toRevoke && revokeMut.mutate(toRevoke)}
      />
    </div>
  )
}
