import { useState } from 'react'
import { KeyRound } from 'lucide-react'
import { changePassword } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardContent } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import { toast } from '@/components/ui/Toast'

export default function ChangePassword() {
  const [form, setForm] = useState({ current: '', next: '', confirm: '' })
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setError(null)
    if (form.next !== form.confirm) {
      setError('New password and confirmation do not match.')
      return
    }
    setLoading(true)
    try {
      await changePassword(form.current, form.next, form.confirm)
      toast.success('Password changed')
      setForm({ current: '', next: '', confirm: '' })
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="password-page">
      <PageHeader title="Change password" description="Use a long, unique password. You stay signed in on this device after the change." icon={KeyRound} />
      <Card>
        <CardContent>
          <form onSubmit={submit}>
            <FormField label="Current password" required>
              <Input type="password" autoComplete="current-password" value={form.current} onChange={(e) => setForm((f) => ({ ...f, current: e.target.value }))} required />
            </FormField>
            <FormField label="New password" required>
              <Input type="password" autoComplete="new-password" value={form.next} onChange={(e) => setForm((f) => ({ ...f, next: e.target.value }))} required />
            </FormField>
            <FormField label="Confirm new password" required error={error}>
              <Input type="password" autoComplete="new-password" value={form.confirm} onChange={(e) => setForm((f) => ({ ...f, confirm: e.target.value }))} required />
            </FormField>
            <Button type="submit" loading={loading}>Update password</Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
