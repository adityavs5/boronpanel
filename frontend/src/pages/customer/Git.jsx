import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { GitBranch, Plus, Trash2, MoreHorizontal, Target, ScrollText, Copy } from 'lucide-react'
import { get, post, patch, del } from '@/lib/api'
import { useAccountUsername } from '@/hooks/useAccount'
import { formatDate, copyToClipboard } from '@/lib/utils'
import { PageHeader } from '@/components/ui/PageHeader'
import { DataTable } from '@/components/ui/Table'
import { Button } from '@/components/ui/Button'
import { Input, FormField } from '@/components/ui/Input'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter,
  ConfirmDialog,
} from '@/components/ui/Dialog'
import {
  DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuSeparator,
} from '@/components/ui/DropdownMenu'
import { CenteredSpinner } from '@/components/ui/Spinner'
import { toast } from '@/components/ui/Toast'

function cloneUrl(username, name) {
  if (!username || !name) return ''
  const host = (typeof window !== 'undefined' && window.location?.hostname) || '<this-server>'
  return `ssh://${username}@${host}/home/${username}/repos/${name}.git`
}

export default function Git() {
  const username = useAccountUsername()
  const qc = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [name, setName] = useState('')
  const [deployTarget, setDeployTarget] = useState(null) // row
  const [deployValue, setDeployValue] = useState('')
  const [logTarget, setLogTarget] = useState(null) // row
  const [toDelete, setToDelete] = useState(null)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['git', username],
    queryFn: () => get(`/api/v1/accounts/${username}/git`),
    enabled: !!username,
  })

  const createMut = useMutation({
    mutationFn: (body) => post(`/api/v1/accounts/${username}/git`, body),
    onSuccess: (res) => {
      toast.success('Repository created', `${res?.name || name} is ready to push to.`)
      qc.invalidateQueries({ queryKey: ['git', username] })
      setCreateOpen(false)
      setName('')
    },
    onError: (e) => toast.error('Could not create repository', e.message),
  })

  const deployMut = useMutation({
    mutationFn: ({ repo, deploy_target }) => patch(`/api/v1/accounts/${username}/git/${repo}/deploy-target`, { deploy_target }),
    onSuccess: (_res, { repo }) => {
      toast.success('Deploy target updated', `Pushes to ${repo} now check out to the configured directory.`)
      qc.invalidateQueries({ queryKey: ['git', username] })
      setDeployTarget(null)
      setDeployValue('')
    },
    onError: (e) => toast.error('Could not update deploy target', e.message),
  })

  const deleteMut = useMutation({
    mutationFn: (r) => del(`/api/v1/accounts/${username}/git/${r.name}`),
    onSuccess: (_res, r) => {
      toast.success('Repository deleted', `${r.name} was permanently removed.`)
      qc.invalidateQueries({ queryKey: ['git', username] })
      setToDelete(null)
    },
    onError: (e) => toast.error('Could not delete repository', e.message),
  })

  async function copyValue(text, label) {
    const ok = await copyToClipboard(text)
    if (ok) toast.success(`${label} copied to clipboard`)
    else toast.error('Could not copy', 'Copy the value manually.')
  }

  const columns = [
    {
      key: 'name',
      header: 'Repository',
      sortable: true,
      searchable: true,
      render: (r) => <span className="font-mono font-medium text-foreground">{r.name}</span>,
    },
    {
      key: 'clone_url',
      header: 'Clone / push URL',
      searchable: true,
      sortValue: (r) => r.name,
      render: (r) => {
        const url = cloneUrl(username, r.name)
        return (
          <div className="flex items-center gap-2">
            <code className="truncate font-mono text-xs text-muted-foreground" title={url}>{url}</code>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={`Copy clone URL for ${r.name}`}
              onClick={() => copyValue(url, 'Clone URL')}
            >
              <Copy className="h-3.5 w-3.5" />
            </Button>
          </div>
        )
      },
    },
    {
      key: 'deploy_target',
      header: 'Deploy target',
      sortable: true,
      searchable: true,
      render: (r) =>
        r.deploy_target
          ? <span className="font-mono text-sm text-muted-foreground">{r.deploy_target}</span>
          : <span className="text-muted-foreground">not configured</span>,
    },
    {
      key: 'created_at',
      header: 'Created',
      sortable: true,
      render: (r) => (r.created_at ? formatDate(r.created_at) : '—'),
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      searchable: false,
      render: (r) => (
        <div className="flex justify-end">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${r.name}`}>
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              <DropdownMenuItem onSelect={() => { setDeployValue(r.deploy_target || ''); setDeployTarget(r) }}>
                <Target className="h-4 w-4" /> Set deploy target
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => setLogTarget(r)}>
                <ScrollText className="h-4 w-4" /> Push log
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem destructive onSelect={() => setToDelete(r)}>
                <Trash2 className="h-4 w-4" /> Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="Git version control"
        description="Each repository is a bare git repo under ~/repos. Push over SSH and, if a deploy target is set, pushes to main/master check out into that directory."
        icon={GitBranch}
      >
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" /> Create repository
        </Button>
      </PageHeader>

      <DataTable
        columns={columns}
        data={data?.repos}
        loading={isLoading}
        error={error}
        onRetry={refetch}
        filterable
        searchPlaceholder="Search repositories…"
        pageSize={15}
        initialSort={{ key: 'name', dir: 'asc' }}
        getRowKey={(r) => r.name}
        emptyTitle="No repositories yet"
        emptyDescription="Create a bare git repository to push your site to over SSH."
        emptyIcon={GitBranch}
        emptyAction={<Button onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4" /> Create repository</Button>}
      />

      {/* Create repository */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>Create repository</DialogTitle>
            <DialogDescription>
              A bare repo is created at <code className="font-mono text-foreground">~/repos/{name.trim() || '<name>'}.git</code>.
            </DialogDescription>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              createMut.mutate({ name: name.trim() })
            }}
          >
            <DialogBody className="space-y-4">
              <FormField label="Repository name" required hint="A short name (e.g. my-site). The .git suffix is added automatically.">
                <Input
                  autoFocus
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="my-site"
                  required
                />
              </FormField>
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => setCreateOpen(false)}>Cancel</Button>
              <Button type="submit" loading={createMut.isPending} disabled={!name.trim()}>Create repository</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Set deploy target */}
      <Dialog open={!!deployTarget} onOpenChange={(v) => { if (!v) { setDeployTarget(null); setDeployValue('') } }}>
        <DialogContent size="sm">
          <DialogHeader>
            <DialogTitle>Set deploy target</DialogTitle>
            <DialogDescription>
              When you push <code className="font-mono text-foreground">main</code> or <code className="font-mono text-foreground">master</code> to{' '}
              <code className="font-mono text-foreground">{deployTarget?.name}</code>, the commit is checked out into this directory.
            </DialogDescription>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              if (deployTarget) deployMut.mutate({ repo: deployTarget.name, deploy_target: deployValue.trim() })
            }}
          >
            <DialogBody className="space-y-4">
              <FormField label="Deploy target" hint="Path relative to your account home (e.g. public_html). Leave blank to disable auto-deploy.">
                <Input
                  autoFocus
                  value={deployValue}
                  onChange={(e) => setDeployValue(e.target.value)}
                  placeholder="public_html"
                />
              </FormField>
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="secondary" onClick={() => { setDeployTarget(null); setDeployValue('') }}>Cancel</Button>
              <Button type="submit" loading={deployMut.isPending}>Save deploy target</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Push log */}
      <PushLogDialog username={username} repo={logTarget} onOpenChange={(v) => { if (!v) setLogTarget(null) }} />

      {/* Delete */}
      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(v) => { if (!v) setToDelete(null) }}
        title={toDelete ? `Delete ${toDelete.name}?` : 'Delete repository?'}
        description="The repository and all of its history are permanently removed. This cannot be undone."
        confirmLabel="Delete repository"
        loading={deleteMut.isPending}
        onConfirm={() => toDelete && deleteMut.mutate(toDelete)}
      />
    </div>
  )
}

function PushLogDialog({ username, repo, onOpenChange }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['git-push-log', username, repo?.name],
    queryFn: () => get(`/api/v1/accounts/${username}/git/${repo.name}/push-log`),
    enabled: !!username && !!repo,
  })
  const lines = data?.lines || []

  return (
    <Dialog open={!!repo} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>Push log — {repo?.name}</DialogTitle>
          <DialogDescription>Recent pushes and deploy checkouts for this repository.</DialogDescription>
        </DialogHeader>
        <DialogBody>
          {isLoading ? (
            <CenteredSpinner />
          ) : error ? (
            <div className="text-sm text-danger">{error.message}</div>
          ) : lines.length === 0 ? (
            <div className="rounded-btn border border-border bg-muted/40 px-4 py-8 text-center text-sm text-muted-foreground">
              No pushes yet.
            </div>
          ) : (
            <pre className="max-h-[55vh] overflow-auto rounded-btn border border-border bg-muted/40 p-4 font-mono text-xs leading-relaxed text-foreground whitespace-pre-wrap">
              {lines.join('\n')}
            </pre>
          )}
        </DialogBody>
      </DialogContent>
    </Dialog>
  )
}
