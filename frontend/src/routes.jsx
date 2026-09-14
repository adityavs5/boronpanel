import { lazy } from 'react'
import { createBrowserRouter, Navigate } from 'react-router-dom'
import { BASE_PATH } from '@/lib/api'
import { useAuth } from '@/store/auth'
import { AppShell } from '@/components/layout/AppShell'
import { ProtectedRoute } from '@/components/auth/ProtectedRoute'
import Login from '@/pages/Login'
import ChangePassword from '@/pages/ChangePassword'
import { Maintenance, NotFound, RouteError } from '@/pages/system'

// Every page is code-split: the shell paints immediately and each page loads
// on first visit (then stays cached). AppShell provides the Suspense fallback.
const Dashboard = lazy(() => import('@/components/themes/ToolDashboard'))
const PanelSettings = lazy(() => import('@/pages/admin/PanelSettings'))
const Appearance = lazy(() => import('@/pages/Appearance'))
const Domains = lazy(() => import('@/pages/customer/Domains'))
const DomainDetail = lazy(() => import('@/pages/customer/DomainDetail'))
const Email = lazy(() => import('@/pages/customer/Email'))
const Databases = lazy(() => import('@/pages/customer/Databases'))
const Files = lazy(() => import('@/pages/customer/Files'))
const BackupJobs = lazy(() => import('@/pages/admin/BackupJobs'))
const Backups = lazy(() => import('@/pages/customer/Backups'))
const Apps = lazy(() => import('@/pages/customer/Apps'))
const Redis = lazy(() => import('@/pages/customer/Redis'))
const Php = lazy(() => import('@/pages/customer/Php'))
const Dns = lazy(() => import('@/pages/customer/Dns'))
const Ssl = lazy(() => import('@/pages/customer/Ssl'))
const Cron = lazy(() => import('@/pages/customer/Cron'))
const Ftp = lazy(() => import('@/pages/customer/Ftp'))
const Git = lazy(() => import('@/pages/customer/Git'))
const SshKeys = lazy(() => import('@/pages/customer/SshKeys'))
const Terminal = lazy(() => import('@/pages/customer/Terminal'))
const DevTools = lazy(() => import('@/pages/customer/DevTools'))
const Processes = lazy(() => import('@/pages/customer/Processes'))
const MoreMenu = lazy(() => import('@/pages/customer/MoreMenu'))
const Security = lazy(() => import('@/pages/customer/Security'))
const Logs = lazy(() => import('@/pages/customer/Logs'))
const DiskUsage = lazy(() => import('@/pages/customer/DiskUsage'))
const MalwareScanner = lazy(() => import('@/pages/MalwareScanner'))

const Accounts = lazy(() => import('@/pages/admin/Accounts'))
const AccountDetail = lazy(() => import('@/pages/admin/AccountDetail'))
const Plans = lazy(() => import('@/pages/admin/Plans'))
const Branding = lazy(() => import('@/pages/admin/Branding'))
const Templates = lazy(() => import('@/pages/admin/Templates'))
const ServerHealth = lazy(() => import('@/pages/admin/ServerHealth'))
const Services = lazy(() => import('@/pages/admin/Services'))
const BandwidthRanking = lazy(() => import('@/pages/admin/BandwidthRanking'))
const MailQueue = lazy(() => import('@/pages/admin/MailQueue'))
const Firewall = lazy(() => import('@/pages/admin/Firewall'))
const OpenLiteSpeed = lazy(() => import('@/pages/admin/OpenLiteSpeed'))
const IpManagement = lazy(() => import('@/pages/admin/IpManagement'))
const IpBans = lazy(() => import('@/pages/admin/IpBans'))
const Fail2ban = lazy(() => import('@/pages/admin/Fail2ban'))
const IpWhitelist = lazy(() => import('@/pages/admin/IpWhitelist'))
const AuditLog = lazy(() => import('@/pages/admin/AuditLog'))
const AccountLog = lazy(() => import('@/pages/admin/AccountLog'))
const ErrorLog = lazy(() => import('@/pages/admin/ErrorLog'))
const Waf = lazy(() => import('@/pages/admin/Waf'))
const SlowQueries = lazy(() => import('@/pages/admin/SlowQueries'))
const Webhooks = lazy(() => import('@/pages/admin/Webhooks'))
const NotificationSettings = lazy(() => import('@/pages/admin/NotificationSettings'))
const AccountImports = lazy(() => import('@/pages/admin/CpanelImport'))
const ApiTokens = lazy(() => import('@/pages/admin/ApiTokens'))
const Cloudflare = lazy(() => import('@/pages/admin/Cloudflare'))
const WordPressManager = lazy(() => import('@/pages/WordPressManager'))
const Updates = lazy(() => import('@/pages/admin/Updates'))
const DbMonitor = lazy(() => import('@/pages/admin/DbMonitor'))
// Named MaintenanceOverview locally -- `Maintenance` (the panel's own
// system-maintenance/downtime page) is already imported above from
// @/pages/system; this is the unrelated missing-features-batch admin page
// listing every hosting domain currently in maintenance mode.
const MaintenanceOverview = lazy(() => import('@/pages/admin/Maintenance'))
const SiteStats = lazy(() => import('@/pages/admin/SiteStats'))
const ImapMigrations = lazy(() => import('@/pages/admin/ImapMigrations'))
const Resellers = lazy(() => import('@/pages/Resellers'))

function IndexRedirect() {
  const role = useAuth.getState().role
  return <Navigate to={role === 'admin' ? '/overview' : role === 'reseller' ? '/reseller' : '/dashboard'} replace />
}

function admin(el) {
  return <ProtectedRoute adminOnly>{el}</ProtectedRoute>
}

function customer(el) {
  return <ProtectedRoute customerOnly>{el}</ProtectedRoute>
}

function reseller(el) {
  return <ProtectedRoute resellerOnly>{el}</ProtectedRoute>
}

// A crash inside one page renders an inline error while the shell (sidebar,
// topbar) stays intact and usable.
function withPageErrors(children) {
  return children.map((r) => ({ ...r, errorElement: <RouteError /> }))
}

export const router = createBrowserRouter(
  [
    { path: '/login', element: <Login />, errorElement: <RouteError /> },
    { path: '/maintenance', element: <Maintenance /> },
    {
      path: '/',
      element: (
        <ProtectedRoute>
          <AppShell />
        </ProtectedRoute>
      ),
      errorElement: <RouteError />,
      children: withPageErrors([
        { index: true, element: <IndexRedirect /> },

        { path: 'overview', element: admin(<Dashboard />) },
        { path: 'reseller', element: reseller(<Resellers />) },
        { path: 'appearance', element: <Appearance /> },

        // Customer resource pages (scoped to the signed-in account).
        { path: 'dashboard', element: customer(<Dashboard />) },
        { path: 'domains', element: customer(<Domains />) },
        { path: 'subdomains', element: customer(<Domains subdomainsOnly />) },
        { path: 'domains/:domain', element: customer(<DomainDetail />) },
        { path: 'php', element: customer(<Php />) },
        { path: 'email', element: customer(<Email />) },
        { path: 'email/settings', element: customer(<Email defaultTab="forwarders" />) },
        { path: 'email/dns', element: customer(<Dns emailOnly />) },
        { path: 'databases', element: customer(<Databases />) },
        { path: 'files', element: customer(<Files />) },
        { path: 'backups', element: customer(<Backups />) },
        { path: 'backup-jobs', element: admin(<BackupJobs />) },
        { path: 'apps', element: customer(<Apps />) },
        { path: 'node-apps', element: customer(<Apps type="node" />) },
        { path: 'python-apps', element: customer(<Apps type="python" />) },
        { path: 'redis', element: customer(<Redis />) },
        { path: 'dns', element: customer(<Dns />) },
        { path: 'ssl', element: <ProtectedRoute><Ssl /></ProtectedRoute> },
        { path: 'cron', element: customer(<Cron />) },
        { path: 'ftp', element: customer(<Ftp />) },
        { path: 'git', element: customer(<Git />) },
        { path: 'ssh', element: customer(<SshKeys />) },
        { path: 'terminal', element: customer(<Terminal />) },
        { path: 'devtools', element: customer(<DevTools />) },
        { path: 'processes', element: customer(<Processes />) },
        { path: 'more', element: <MoreMenu /> },
        { path: 'change-password', element: <ChangePassword /> },
        { path: 'security', element: <Security /> },
        { path: 'logs', element: customer(<Logs />) },
        { path: 'disk-usage', element: customer(<DiskUsage />) },
        { path: 'malware', element: <MalwareScanner /> },

        // Admin pages.
        { path: 'accounts', element: admin(<Accounts />) },
        { path: 'accounts/:username', element: admin(<AccountDetail />) },
        { path: 'plans', element: admin(<Plans />) },
        { path: 'resellers', element: admin(<Resellers />) },
        { path: 'panel-settings', element: admin(<PanelSettings />) },
        { path: 'branding', element: admin(<Branding />) },
        { path: 'templates', element: admin(<Templates />) },
        { path: 'accounts/:username/domains/:domain', element: admin(<DomainDetail />) },
        { path: 'health', element: admin(<ServerHealth />) },
        { path: 'services', element: admin(<Services />) },
        { path: 'bandwidth', element: admin(<BandwidthRanking />) },
        { path: 'mail-queue', element: admin(<MailQueue />) },
        { path: 'firewall', element: admin(<Firewall />) },
        { path: 'openlitespeed', element: admin(<OpenLiteSpeed />) },
        { path: 'ip-management', element: admin(<IpManagement />) },
        { path: 'ip-bans', element: admin(<IpBans />) },
        { path: 'fail2ban', element: admin(<Fail2ban />) },
        { path: 'ip-whitelist', element: admin(<IpWhitelist />) },
        { path: 'cloudflare', element: admin(<Cloudflare />) },
        { path: 'audit-log', element: admin(<AuditLog />) },
        { path: 'account-log', element: admin(<AccountLog />) },
        { path: 'error-log', element: admin(<ErrorLog />) },
        { path: 'waf', element: admin(<Waf />) },
        { path: 'slow-queries', element: admin(<SlowQueries />) },
        { path: 'webhooks', element: admin(<Webhooks />) },
        { path: 'notifications', element: admin(<NotificationSettings />) },
        { path: 'import/accounts', element: admin(<AccountImports />) },
        { path: 'import/cpanel', element: <Navigate to="/import/accounts" replace /> },
        { path: 'tokens', element: admin(<ApiTokens />) },
        { path: 'wordpress', element: <WordPressManager /> },
        { path: 'updates', element: admin(<Updates />) },
        { path: 'db-monitor', element: admin(<DbMonitor />) },
        { path: 'maintenance-mode', element: admin(<MaintenanceOverview />) },
        { path: 'site-stats', element: admin(<SiteStats />) },
        { path: 'imap-migrations', element: admin(<ImapMigrations />) },

        { path: '*', element: <NotFound /> },
      ]),
    },
    { path: '*', element: <NotFound /> },
  ],
  { basename: BASE_PATH },
)
