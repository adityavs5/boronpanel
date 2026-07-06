import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Save, Pencil, Globe, Mail, KeyRound, AlertTriangle } from 'lucide-react'
import { patch } from '@/lib/api'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { ConfirmDialog } from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'

// Phase 8 feature 2: admin-only editor for an account's identity and any of
// its passwords, without entering the customer panel.
export default function AccountIdentity({ username, account }) {
  return (
    <div className="space-y-6">
      <RenameCard username={username} account={account} />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <PrimaryDomainCard username={username} account={account} />
        <ContactEmailCard username={username} />
      </div>
      <PasswordsCard username={username} />
    </div>
  )
}

function RenameCard({ username, account }) {
  const navigate = useNavigate()
  const [value, setValue] = useState('')
  const [confirm, setConfirm] = useState(false)
  const mut = useMutation({
    mutationFn: () => patch(`/api/v1/admin/accounts/${username}/identity`, { new_username: value.trim() }),
    onSuccess: (res) => {
      toast.success('Account renamed', `${res.old_username} → ${res.new_username}`)
      setConfirm(false)
      navigate(`/accounts/${res.new_username}`, { replace: true })
    },
    onError: (e) => { toast.error('Rename failed (rolled back)', e.message); setConfirm(false) },
  })
  const canRename = ['active', 'suspended'].includes(account.status)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Pencil className="h-4 w-4" /> Rename username</CardTitle>
        <CardDescription>
          Atomically renames the Linux user, home directory, OLS/PHP config and cgroup slice. Rolls back on any failure.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-end gap-3">
          <FormField label="New username" className="flex-1" hint="1–16 lowercase letters/digits, starting with a letter.">
            <Input value={value} onChange={(e) => setValue(e.target.value)} placeholder="newname" disabled={!canRename} />
          </FormField>
          <Button variant="warning" disabled={!canRename || !value.trim()} onClick={() => setConfirm(true)}>Rename</Button>
        </div>
        <p className="flex items-start gap-2 text-xs text-muted-foreground">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" />
          Refused if the account has NodeJS/Python apps or FTP sub-accounts (their absolute home paths aren't rewritten).
          Hosted MariaDB databases keep their original name prefix.
        </p>
      </CardContent>
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title={`Rename ${username} → ${value.trim()}?`}
        description="This changes the account username everywhere. Existing customer sessions and the old username stop working."
        confirmLabel="Rename account"
        variant="warning"
        loading={mut.isPending}
        onConfirm={() => mut.mutate()}
      />
    </Card>
  )
}

function PrimaryDomainCard({ username, account }) {
  const qc = useQueryClient()
  const [value, setValue] = useState('')
  const mut = useMutation({
    mutationFn: () => patch(`/api/v1/admin/accounts/${username}/identity`, { primary_domain: value.trim() }),
    onSuccess: (res) => {
      toast.success('Primary domain updated', res.primary_domain)
      qc.invalidateQueries({ queryKey: ['account', username] })
      setValue('')
    },
    onError: (e) => toast.error('Failed', e.message),
  })
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Globe className="h-4 w-4" /> Primary domain</CardTitle>
        <CardDescription>Current: {account.primary_domain || <span className="italic">none</span>}</CardDescription>
      </CardHeader>
      <CardContent className="flex items-end gap-3">
        <FormField label="New primary domain" className="flex-1">
          <Input value={value} onChange={(e) => setValue(e.target.value)} placeholder="example.com" />
        </FormField>
        <Button loading={mut.isPending} disabled={!value.trim()} onClick={() => mut.mutate()}>Set</Button>
      </CardContent>
    </Card>
  )
}

function ContactEmailCard({ username }) {
  const [value, setValue] = useState('')
  const mut = useMutation({
    mutationFn: () => patch(`/api/v1/admin/accounts/${username}/identity`, { contact_email: value.trim() }),
    onSuccess: () => toast.success('Contact email updated'),
    onError: (e) => toast.error('Failed', e.message),
  })
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Mail className="h-4 w-4" /> Contact email</CardTitle>
        <CardDescription>Where account notifications are sent. Blank clears it.</CardDescription>
      </CardHeader>
      <CardContent className="flex items-end gap-3">
        <FormField label="Contact email" className="flex-1">
          <Input type="email" value={value} onChange={(e) => setValue(e.target.value)} placeholder="owner@example.com" />
        </FormField>
        <Button loading={mut.isPending} onClick={() => mut.mutate()}><Save className="h-4 w-4" /> Save</Button>
      </CardContent>
    </Card>
  )
}

const PW_KINDS = [
  { value: 'account', label: 'Account (FTP/SSH)', fields: [] },
  { value: 'panel', label: 'Panel login', fields: [] },
  { value: 'mailbox', label: 'Mailbox', fields: ['domain', 'local_part'] },
  { value: 'database', label: 'Database user', fields: ['name'] },
  { value: 'ftp', label: 'FTP sub-account', fields: ['label'] },
]

function PasswordsCard({ username }) {
  const [kind, setKind] = useState('account')
  const [password, setPassword] = useState('')
  const [extra, setExtra] = useState({})
  const spec = PW_KINDS.find((k) => k.value === kind)

  const mut = useMutation({
    mutationFn: () => {
      const body = { kind, password }
      for (const f of spec.fields) body[f] = extra[f] || ''
      return patch(`/api/v1/admin/accounts/${username}/passwords`, body)
    },
    onSuccess: () => { toast.success('Password reset'); setPassword('') },
    onError: (e) => toast.error('Could not reset password', e.message),
  })

  const fieldLabel = { domain: 'Mail domain', local_part: 'Mailbox (local part)', name: 'Database suffix', label: 'FTP sub-account label' }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><KeyRound className="h-4 w-4" /> Reset a password</CardTitle>
        <CardDescription>Reset any credential on this account without entering the customer panel.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <FormField label="Credential">
            <Select value={kind} onChange={(e) => { setKind(e.target.value); setExtra({}) }}>
              {PW_KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
            </Select>
          </FormField>
          {spec.fields.map((f) => (
            <FormField key={f} label={fieldLabel[f]}>
              <Input value={extra[f] || ''} onChange={(e) => setExtra((s) => ({ ...s, [f]: e.target.value }))} />
            </FormField>
          ))}
          <FormField label="New password" hint="12+ chars, mixed case, number and symbol.">
            <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </FormField>
        </div>
        <Button loading={mut.isPending} disabled={!password} onClick={() => mut.mutate()}>
          <KeyRound className="h-4 w-4" /> Reset password
        </Button>
      </CardContent>
    </Card>
  )
}
