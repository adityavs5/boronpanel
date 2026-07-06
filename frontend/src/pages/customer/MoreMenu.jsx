import { Link } from 'react-router-dom'
import {
  Menu, Archive, Boxes, Server, Network, ShieldCheck, Clock, Upload, GitBranch, KeyRound, HardDrive, ArrowRight,
} from 'lucide-react'
import { PageHeader } from '@/components/ui/PageHeader'

const MENU_ITEMS = [
  { label: 'Backups', to: '/backups', icon: Archive },
  { label: 'Applications', to: '/apps', icon: Boxes },
  { label: 'Redis', to: '/redis', icon: Server },
  { label: 'DNS', to: '/dns', icon: Network },
  { label: 'SSL', to: '/ssl', icon: ShieldCheck },
  { label: 'Cron', to: '/cron', icon: Clock },
  { label: 'FTP', to: '/ftp', icon: Upload },
  { label: 'Git', to: '/git', icon: GitBranch },
  { label: 'SSH Keys', to: '/ssh', icon: KeyRound },
  { label: 'Files', to: '/files', icon: HardDrive },
]

export default function MoreMenu() {
  return (
    <div>
      <PageHeader title="More" description="All tools and settings for your hosting account." icon={Menu} />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {MENU_ITEMS.map((item) => (
          <Link
            key={item.to}
            to={item.to}
            className="group flex flex-col gap-2 rounded-btn border border-border p-4 transition-colors hover:border-accent hover:bg-accent-50 dark:hover:bg-accent-950/40"
          >
            <item.icon className="h-5 w-5 text-accent-600" />
            <span className="flex items-center justify-between text-sm font-medium text-foreground">
              {item.label}
              <ArrowRight className="h-3.5 w-3.5 opacity-0 transition-opacity group-hover:opacity-100" />
            </span>
          </Link>
        ))}
      </div>
    </div>
  )
}
