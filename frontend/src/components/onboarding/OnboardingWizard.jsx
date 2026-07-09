import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Globe, Mail, Boxes, Copy, ArrowRight, ArrowLeft, Check } from 'lucide-react'
import { get, patch } from '@/lib/api'
import { copyToClipboard } from '@/lib/utils'
import { useAuth } from '@/store/auth'
import { useBranding } from '@/hooks/useBranding'
import { Button } from '@/components/ui/Button'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter,
} from '@/components/ui/Dialog'
import { toast } from '@/components/ui/Toast'

// Run A feature 4: 3-step onboarding wizard, shown once on a customer's
// first login (backed by GET/PATCH /accounts/{u}/onboarding -- the server
// is the once-only source of truth, not localStorage, so it doesn't
// re-trigger on another browser after being finished on this one).
// Deliberately NOT shown while an admin is impersonating: the wizard
// belongs to the real customer's first login, and an admin clicking
// through it would permanently consume it on the customer's behalf.

function CopyRow({ label, value }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-btn border border-border bg-input-surface px-3 py-2">
      <div className="min-w-0">
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className="truncate font-mono text-sm text-foreground">{value}</div>
      </div>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={`Copy ${label}`}
        onClick={async () => {
          const ok = await copyToClipboard(value)
          if (ok) toast.success(`${label} copied to clipboard`)
          else toast.error('Could not copy', 'Copy the value manually.')
        }}
      >
        <Copy className="h-4 w-4" />
      </Button>
    </div>
  )
}

function QuickAction({ icon: Icon, title, description, onClick }) {
  return (
    <button
      onClick={onClick}
      className="flex w-full items-center gap-3 rounded-btn border border-border bg-input-surface px-4 py-3 text-left transition-colors hover:border-accent"
    >
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-btn bg-accent/10 text-accent">
        <Icon className="h-4.5 w-4.5" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-foreground">{title}</div>
        <div className="text-xs text-muted-foreground">{description}</div>
      </div>
      <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground" />
    </button>
  )
}

export function OnboardingWizard({ username, account }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const impersonating = useAuth((s) => s.impersonating)
  const role = useAuth((s) => s.role)
  const { panelName } = useBranding()
  const [step, setStep] = useState(0)

  const { data } = useQuery({
    queryKey: ['onboarding', username],
    queryFn: () => get(`/api/v1/accounts/${username}/onboarding`),
    enabled: !!username && role === 'customer' && !impersonating,
    staleTime: Infinity,
    retry: false,
  })

  const finishMut = useMutation({
    mutationFn: (skipped) => patch(`/api/v1/accounts/${username}/onboarding`, { completed: true, skipped }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['onboarding', username] }),
  })

  const open = !!data && !data.completed && role === 'customer' && !impersonating
  if (!open) return null

  const skip = () => finishMut.mutate(true)
  const finish = () => finishMut.mutate(false)
  const go = (path) => { finishMut.mutate(false); navigate(path) }

  const steps = [
    {
      title: `Welcome to ${panelName}`,
      description: 'Here are your account details to get you oriented.',
      body: (
        <div className="space-y-2">
          <CopyRow label="Account username" value={username} />
          {account?.primary_domain && <CopyRow label="Primary domain" value={account.primary_domain} />}
          <div className="grid grid-cols-2 gap-2">
            <div className="rounded-btn border border-border bg-input-surface px-3 py-2">
              <div className="text-xs text-muted-foreground">PHP version</div>
              <div className="text-sm font-medium text-foreground">PHP {account?.php_version || '—'}</div>
            </div>
            <div className="rounded-btn border border-border bg-input-surface px-3 py-2">
              <div className="text-xs text-muted-foreground">Disk quota</div>
              <div className="text-sm font-medium text-foreground">
                {account?.quota_hard_mb ? `${(account.quota_hard_mb / 1024).toFixed(1)} GB` : '—'}
              </div>
            </div>
          </div>
          <p className="pt-1 text-xs text-muted-foreground">
            Tip: you can change your panel password anytime under Security.
          </p>
        </div>
      ),
    },
    {
      title: 'Point your domain here',
      description: 'To serve your site from this server, update DNS at your domain registrar.',
      body: (
        <div className="space-y-2">
          {data.server_ip && <CopyRow label="Server IP (A record)" value={data.server_ip} />}
          {data.nameservers?.map((ns) => <CopyRow key={ns} label="Nameserver" value={ns} />)}
          <p className="pt-1 text-xs text-muted-foreground">
            {data.nameservers?.length
              ? 'Either point your domain\'s nameservers at the pair above (full DNS management here), or just add an A record with the server IP at your current DNS provider.'
              : 'Add an A record with the server IP at your DNS provider. Once you add a domain to this account, vanity nameservers become available too.'}
          </p>
        </div>
      ),
    },
    {
      title: 'Quick start',
      description: 'Jump straight into the most common first steps.',
      body: (
        <div className="space-y-2">
          <QuickAction icon={Globe} title="Add a domain" description="Attach a domain or subdomain to this account." onClick={() => go('/domains')} />
          <QuickAction icon={Mail} title="Create an email address" description="Set up mailboxes on your domain." onClick={() => go('/email')} />
          <QuickAction icon={Boxes} title="Install WordPress" description="One-click WordPress on any of your domains." onClick={() => go('/domains')} />
        </div>
      ),
    },
  ]

  const current = steps[step]
  const isLast = step === steps.length - 1

  return (
    <Dialog open onOpenChange={(v) => { if (!v) skip() }}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>{current.title}</DialogTitle>
          <DialogDescription>{current.description}</DialogDescription>
        </DialogHeader>
        <DialogBody>{current.body}</DialogBody>
        <DialogFooter className="justify-between">
          <div className="flex items-center gap-2">
            {steps.map((_, i) => (
              <span key={i} className={`h-1.5 w-1.5 rounded-full ${i === step ? 'bg-accent' : 'bg-muted'}`} />
            ))}
            <span className="ml-2 text-xs text-muted-foreground">Step {step + 1} of {steps.length}</span>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={skip} loading={finishMut.isPending}>Skip</Button>
            {step > 0 && (
              <Button variant="secondary" size="sm" onClick={() => setStep((s) => s - 1)}>
                <ArrowLeft className="h-4 w-4" /> Back
              </Button>
            )}
            {isLast ? (
              <Button size="sm" onClick={finish} loading={finishMut.isPending}>
                <Check className="h-4 w-4" /> Finish
              </Button>
            ) : (
              <Button size="sm" onClick={() => setStep((s) => s + 1)}>
                Next <ArrowRight className="h-4 w-4" />
              </Button>
            )}
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
