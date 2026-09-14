import { NavLink } from 'react-router-dom'
import { Grid3X3, Palette, Users, FolderOpen, Search } from 'lucide-react'
import { useAuth } from '@/store/auth'
import { useUI } from '@/store/ui'
import { Tooltip } from '@/components/ui/Tooltip'

export function PaperRail() {
  const isAdmin = useAuth((s) => s.role === 'admin')
  const isReseller = useAuth((s) => s.role === 'reseller')
  const setPaletteOpen = useUI((s) => s.setPaletteOpen)
  const items = [
    { to: isAdmin ? '/overview' : isReseller ? '/reseller' : '/dashboard', label: 'All tools', icon: Grid3X3 },
    ...(isReseller ? [] : [{ to: isAdmin ? '/accounts' : '/files', label: isAdmin ? 'Accounts' : 'File Manager', icon: isAdmin ? Users : FolderOpen }]),
    { to: '/appearance', label: 'Appearance', icon: Palette },
  ]
  return <nav className="paper-rail" aria-label="Quick navigation">
    {items.map(({ to, label, icon: Icon }) => <Tooltip key={to} content={label} side="right"><NavLink to={to} aria-label={label}><Icon size={23} /></NavLink></Tooltip>)}
    <Tooltip content="Search tools" side="right"><button type="button" onClick={() => setPaletteOpen(true)} aria-label="Search tools"><Search size={22} /></button></Tooltip>
  </nav>
}
