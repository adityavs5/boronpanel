import { forwardRef, useState } from 'react'
import { Check, Eye, EyeOff, AlertCircle } from 'lucide-react'
import { Input } from './Input'
import { Button } from './Button'

// Passwords are generated locally with Web Crypto, never persisted or logged.
export function generateAccountPassword() {
  const groups = ['abcdefghijkmnopqrstuvwxyz', 'ABCDEFGHJKLMNPQRSTUVWXYZ', '23456789', '!@#$%&*+-=?']
  const pick = size => {
    const limit = 256 - (256 % size)
    const byte = new Uint8Array(1)
    do { crypto.getRandomValues(byte) } while (byte[0] >= limit)
    return byte[0] % size
  }
  const alphabet = groups.join('')
  const chars = groups.map(group => group[pick(group.length)])
  while (chars.length < 20) chars.push(alphabet[pick(alphabet.length)])
  for (let i = chars.length - 1; i > 0; i--) {
    const j = pick(i + 1)
    ;[chars[i], chars[j]] = [chars[j], chars[i]]
  }
  return chars.join('')
}

export const AccountPasswordInput = forwardRef(function AccountPasswordInput({ value, onChange, id, invalid, ...props }, ref) {
  const [visible, setVisible] = useState(false)
  const meetsRules = /^(?=.*[a-z])(?=.*[A-Z])(?=.*[0-9])(?=.*[^A-Za-z0-9]).{12,}$/.test(value)
  const level = !value ? 0 : !meetsRules ? 1 : value.length < 16 ? 2 : 3
  const strength = ['Generated on creation', 'Does not meet requirements', 'Meets requirements', 'Strong'][level]
  return <div className="password-field">
    <div className="password-controls">
      <div className="password-input-wrap">
        <Input {...props} ref={ref} id={id} invalid={invalid} type={visible ? 'text' : 'password'} value={value} onChange={onChange} autoComplete="new-password" />
        <button type="button" className="password-visibility" aria-label={visible ? 'Hide password' : 'Show password'} aria-pressed={visible} onClick={() => setVisible(!visible)}>{visible ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}</button>
      </div>
      <Button variant="secondary" type="button" onClick={() => onChange({ target: { value: generateAccountPassword() } })}>Generate</Button>
    </div>
    <div className="password-strength" data-strength={level}>
      <span className="password-strength-bars" aria-hidden="true">{[1, 2, 3].map(n => <i key={n} data-filled={level >= n} />)}</span>
      <span role="status" aria-live="polite">{strength}</span>
    </div>
  </div>
})

export const AccountUsernameInput = forwardRef(function AccountUsernameInput({ value, invalid, id, ...props }, ref) {
  const feedbackId = `${id}-feedback`
  return <div>
    <Input {...props} ref={ref} id={id} value={value} invalid={invalid} aria-describedby={[props['aria-describedby'], feedbackId].filter(Boolean).join(' ')} />
    <div id={feedbackId} className="field-feedback">
      <span className={value ? invalid ? 'field-invalid' : 'field-valid' : ''} role="status">{value ? <>{invalid ? <AlertCircle aria-hidden="true" /> : <Check aria-hidden="true" />}{invalid ? 'Check format' : 'Valid format'}</> : 'Start with a lowercase letter.'}</span>
      <span>{value.length}/16</span>
    </div>
  </div>
})

export function PlanLimits({ plan }) {
  if (!plan) return <p className="plan-default-note">Uses the server’s default limits.</p>
  const gb = value => value == null ? 'Not specified' : `${Number((value / 1024).toFixed(2))} GB`
  const count = value => value === undefined ? 'Not specified' : value === null ? 'Unlimited' : value.toLocaleString()
  const cpu = plan.cpu_cores ?? (plan.cpu_pct == null ? null : plan.cpu_pct / 100)
  const rows = [
    ['CPU', cpu == null ? 'Not specified' : `${cpu} cores`],
    ['Memory', gb(plan.memory_max_mb ?? plan.mem_mb)],
    ['Disk', gb(plan.quota_hard_mb)],
    ['Bandwidth', plan.bandwidth_limit_mb === null ? 'Unlimited' : gb(plan.bandwidth_limit_mb)],
    ['Databases', count(plan.database_limit)],
    ['Mailboxes', count(plan.email_account_limit)],
  ]
  return <dl className="plan-limits" aria-label={`${plan.name} limits`}>{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
}
