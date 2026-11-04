import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Check, LayoutGrid } from 'lucide-react'
import { get, put } from '@/lib/api'
import { Button } from '@/components/ui/Button'
import { PageHeader } from '@/components/ui/PageHeader'
import { CenteredSpinner } from '@/components/ui/Spinner'
import { ErrorState } from '@/components/ui/States'
import { toast } from '@/components/ui/Toast'
import { THEMES } from '@/themes/registry'

// Small drawings of each theme's frame: where its menu sits and how tools look.
function Preview({ id }) {
  const tools = Array.from({ length: id === 'grid' ? 8 : 9 })
  return <div className={`theme-sketch theme-sketch-${id}`} aria-hidden="true">
    {id === 'grid' ? <div className="sketch-menu" /> : <div className="sketch-side">{[0, 1, 2, 3, 4, 5].map((n) => <i key={n} />)}</div>}
    <div className="sketch-body">
      {id !== 'grid' && <div className="sketch-head" />}
      <div className="sketch-tools">{tools.map((_, n) => <i key={n} />)}</div>
    </div>
  </div>
}

export default function PanelTheme() {
  const [chosen, setChosen] = useState(null)
  const query = useQuery({ queryKey: ['admin-branding'], queryFn: () => get('/api/v1/admin/branding'), retry: false })
  const apply = useMutation({
    mutationFn: (theme) => put('/api/v1/admin/branding/theme', { theme }),
    onSuccess: () => {
      // The theme is part of the page itself, so load it fresh.
      try { localStorage.removeItem('boron.public-branding.v1') } catch { /* storage can be unavailable */ }
      window.location.reload()
    },
    onError: (error) => toast.error('Could not change the panel theme', error.message),
  })
  if (query.isLoading) return <CenteredSpinner />
  if (query.error) return <ErrorState error={query.error} onRetry={query.refetch} />
  const current = query.data.theme
  const selected = chosen ?? current
  return <div className="panel-theme-page">
    <PageHeader title="Panel theme" description="Choose how the panel looks for everyone on this server: administrators, resellers and customers. Each theme has its own layout, menus, icons and page styles." icon={LayoutGrid} />
    <div className="theme-options" role="radiogroup" aria-label="Panel theme">
      {THEMES.map((theme) => <button key={theme.id} type="button" role="radio" aria-checked={selected === theme.id} className="theme-option" onClick={() => setChosen(theme.id)}>
        <Preview id={theme.id} />
        <span className="theme-option-title"><strong>{theme.name}</strong>{current === theme.id && <span className="theme-option-current"><Check aria-hidden="true" /> In use</span>}</span>
        <span className="theme-option-layout">{theme.layout}</span>
        <span className="theme-option-text">{theme.description}</span>
      </button>)}
    </div>
    <div className="theme-apply">
      <Button loading={apply.isPending} disabled={selected === current} onClick={() => apply.mutate(selected)}>Apply theme for everyone</Button>
      <p>People using the panel get the new theme the next time a page loads. Their data and settings do not change, and you can switch again at any time.</p>
    </div>
  </div>
}
