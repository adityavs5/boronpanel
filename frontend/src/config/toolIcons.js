// Navigation entries keep their own icons for the sidebar; this map gives each
// home-screen tool a distinct glyph. The themed icon layer swaps the artwork.
import {
  Activity, AppWindow, Archive, ArrowLeftRight, Ban, BarChart3, BellRing, BookOpen, Boxes, Bug,
  CalendarClock, Clock, Cloud, CloudDownload, Code2, Construction, Cpu, Database, DatabaseBackup,
  Download, FileArchive, FileCode2, FileSearch, FileText, FileWarning, FileX2, Flame, FolderLock,
  FolderOpen, Gauge, Globe, HardDrive, HardDriveDownload, HeartPulse, History, KeyRound, KeySquare,
  Layers, LayoutGrid, LineChart, Link2, ListChecks, Lock, Mail, MailCheck, Network, Package, Palette,
  Paintbrush, RefreshCw, Rocket, RotateCcw, ScrollText, Send, Server, Settings, Settings2, Share2,
  ShieldAlert, ShieldBan, ShieldCheck, ShieldHalf, ShieldPlus, ShieldX, Signpost, SlidersHorizontal,
  SquareTerminal, Stethoscope, Upload, UserCog, UserPlus, Users, Waypoints, Webhook, Workflow, Wrench, Zap,
} from 'lucide-react'

const routeIcons = {
  '/domains': Globe, '/subdomains': AppWindow, '/ftp': Upload, '/ssl': ShieldCheck, '/databases': Database,
  '/dns': Network, '/php': FileCode2, '/email': Mail, '/email/settings': SlidersHorizontal,
  '/email/dns': MailCheck, '/email/spam': ShieldBan, '/email/migration': ArrowLeftRight,
  '/files': FolderOpen, '/redirects': Signpost, '/forwarding': Link2, '/cache': Zap,
  '/website-maintenance': Construction, '/error-pages': FileX2, '/cron': Clock, '/ssh': KeyRound,
  '/terminal': SquareTerminal, '/logs': ScrollText, '/devtools': Code2, '/change-password': KeySquare,
  '/appearance': SlidersHorizontal, '/disk-usage': HardDrive, '/website-statistics': BarChart3, '/processes': Cpu,
  '/malware': Bug, '/website-security': ShieldAlert, '/security': Lock, '/site-health': HeartPulse,
  '/accounts': Users, '/accounts/new': UserPlus, '/resellers': UserCog, '/administrators': ShieldHalf,
  '/plans': Package, '/import/accounts': CloudDownload, '/health': Activity, '/diagnostics': Stethoscope,
  '/resource-manager': Gauge, '/filesystem-isolation': FolderLock, '/security-center': ShieldCheck,
  '/stack-manager': Layers, '/services': Settings, '/openlitespeed': Rocket, '/db-monitor': Database,
  '/slow-queries': LineChart, '/dns-setup': Workflow, '/cloudflare': Cloud, '/dns-cluster': Share2,
  '/ip-management': Waypoints, '/updates': RefreshCw, '/mail-queue': Send, '/mail-tracking': FileSearch,
  '/imap-migrations': ArrowLeftRight, '/notifications': BellRing, '/templates': FileText,
  '/maintenance-mode': Wrench, '/firewall': Flame, '/fail2ban': ShieldX, '/waf': ShieldCheck,
  '/ip-bans': Ban, '/ip-whitelist': ShieldPlus, '/tokens': KeySquare, '/bandwidth': BarChart3,
  '/site-stats': LineChart, '/audit-log': ListChecks, '/account-log': FileSearch, '/error-log': FileWarning,
  '/retention': History, '/webhooks': Webhook, '/panel-settings': SlidersHorizontal, '/server-setup': Server,
  '/branding': Paintbrush, '/panel-theme': LayoutGrid, '/api/docs': BookOpen, '/license': KeySquare,
}

const exactIcons = {
  '/email?webmail=1': AppWindow,
  '/backups': Archive, '/backups?component=files': FileArchive, '/backups?component=databases': DatabaseBackup,
  '/backups?component=mail': Mail, '/backups?action=create&kind=full': HardDriveDownload,
  '/backups?view=restores': Download, '/backup-jobs': Archive, '/backup-jobs?tab=jobs': CalendarClock,
  '/backup-jobs?tab=destinations': Cloud, '/backup-jobs?tab=history&view=recovery': RotateCcw,
  '/backup-jobs?tab=history&view=activity': ListChecks, '/backup-jobs?tab=notifications': BellRing,
}

export function toolIcon(to, fallback) {
  return exactIcons[to] || routeIcons[to?.split('?')[0]] || fallback
}

// Category chips: one hue and icon per home-screen group.
const groupMeta = {
  Accounts: [Users, 'blue'],
  Domains: [Globe, 'blue'],
  Email: [Mail, 'violet'],
  Software: [Boxes, 'teal'],
  'Software & websites': [Boxes, 'teal'],
  'Site tools': [Wrench, 'amber'],
  'Advanced tools': [SquareTerminal, 'slate'],
  'Usage & security': [ShieldCheck, 'rose'],
  'Server & databases': [Server, 'indigo'],
  'Domains & network': [Network, 'sky'],
  Security: [ShieldCheck, 'rose'],
  'Logs & integrations': [BarChart3, 'orange'],
  Preferences: [Settings2, 'slate'],
  Backups: [Archive, 'emerald'],
  'More tools': [LayoutGrid, 'sky'],
}
const fallbackHues = ['blue', 'violet', 'teal', 'amber', 'rose', 'sky', 'emerald', 'slate', 'indigo', 'orange']

export function toolGroupMeta(title, role, index = 0) {
  const entry = groupMeta[title]
  const [icon, hue] = entry || [LayoutGrid, fallbackHues[index % fallbackHues.length]]
  return { icon, hue }
}
