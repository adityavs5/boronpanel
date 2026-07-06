import {
  LayoutDashboard, Users, Globe, Mail, Database, FolderOpen, Archive, Boxes,
  Server, Network, ShieldCheck, Clock, Upload, GitBranch, KeyRound, Activity,
  Cog, Inbox, Flame, ShieldAlert, ScrollText, ShieldHalf, Gauge, ListChecks,
  Webhook, BellRing, LockKeyhole, DownloadCloud, Cpu, HardDrive, TerminalSquare, Wrench,
} from 'lucide-react'

// Customer nav — resource pages scoped to the signed-in account. Paths are
// client-router paths (mounted under the /app basename).
export const customerNav = [
  { section: 'Overview' },
  { label: 'Dashboard', to: '/dashboard', icon: LayoutDashboard },
  { section: 'Hosting' },
  { label: 'Domains', to: '/domains', icon: Globe },
  { label: 'Email', to: '/email', icon: Mail },
  { label: 'Databases', to: '/databases', icon: Database },
  { label: 'Files', to: '/files', icon: FolderOpen },
  { label: 'Backups', to: '/backups', icon: Archive },
  { section: 'Apps & Services' },
  { label: 'Applications', to: '/apps', icon: Boxes },
  { label: 'Redis', to: '/redis', icon: Server },
  { label: 'DNS', to: '/dns', icon: Network },
  { label: 'SSL', to: '/ssl', icon: ShieldCheck },
  { label: 'Disk Usage', to: '/disk-usage', icon: HardDrive },
  { section: 'Advanced' },
  { label: 'Cron Jobs', to: '/cron', icon: Clock },
  { label: 'FTP', to: '/ftp', icon: Upload },
  { label: 'Git', to: '/git', icon: GitBranch },
  { label: 'SSH Keys', to: '/ssh', icon: KeyRound },
  { label: 'Terminal', to: '/terminal', icon: TerminalSquare },
  { label: 'Dev Tools', to: '/devtools', icon: Wrench },
  { label: 'Processes', to: '/processes', icon: Cpu },
  { label: 'Logs', to: '/logs', icon: ScrollText },
  { label: 'Security', to: '/security', icon: ShieldCheck },
]

// Admin nav — server-wide administration + the accounts hub.
export const adminNav = [
  { section: 'Administration' },
  { label: 'Accounts', to: '/accounts', icon: Users },
  { label: 'Server Health', to: '/health', icon: Activity },
  { label: 'Services', to: '/services', icon: Cog },
  { label: 'Bandwidth', to: '/bandwidth', icon: Gauge },
  { section: 'Mail & Network' },
  { label: 'Mail Queue', to: '/mail-queue', icon: Inbox },
  { label: 'Firewall', to: '/firewall', icon: Flame },
  { label: 'Fail2ban', to: '/fail2ban', icon: ShieldAlert },
  { label: 'IP Whitelist', to: '/ip-whitelist', icon: LockKeyhole },
  { section: 'Security & Logs' },
  { label: 'Audit Log', to: '/audit-log', icon: ScrollText },
  { label: 'WAF', to: '/waf', icon: ShieldHalf },
  { label: 'Slow Queries', to: '/slow-queries', icon: ListChecks },
  { label: 'API Tokens', to: '/tokens', icon: KeyRound },
  { section: 'Integrations' },
  { label: 'Webhooks', to: '/webhooks', icon: Webhook },
  { label: 'Notifications', to: '/notifications', icon: BellRing },
  { label: 'cPanel Import', to: '/import/cpanel', icon: DownloadCloud },
]

export const navIcons = { Cpu, Server, DownloadCloud }
