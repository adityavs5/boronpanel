import { createBrowserRouter, Navigate } from 'react-router-dom'
import { BASE_PATH } from '@/lib/api'
import { useAuth } from '@/store/auth'
import { AppShell } from '@/components/layout/AppShell'
import { ProtectedRoute } from '@/components/auth/ProtectedRoute'
import Login from '@/pages/Login'
import ChangePassword from '@/pages/ChangePassword'
import { Maintenance, NotFound } from '@/pages/system'

// Real pages (filled in progressively).
import Dashboard from '@/pages/customer/Dashboard'
import Domains from '@/pages/customer/Domains'
import DomainDetail from '@/pages/customer/DomainDetail'
import Email from '@/pages/customer/Email'
import Databases from '@/pages/customer/Databases'
import Files from '@/pages/customer/Files'
import Backups from '@/pages/customer/Backups'
import Apps from '@/pages/customer/Apps'
import Redis from '@/pages/customer/Redis'
import Dns from '@/pages/customer/Dns'
import Ssl from '@/pages/customer/Ssl'
import Cron from '@/pages/customer/Cron'
import Ftp from '@/pages/customer/Ftp'
import Git from '@/pages/customer/Git'
import SshKeys from '@/pages/customer/SshKeys'
import Terminal from '@/pages/customer/Terminal'
import DevTools from '@/pages/customer/DevTools'
import Processes from '@/pages/customer/Processes'
import MoreMenu from '@/pages/customer/MoreMenu'
import Security from '@/pages/customer/Security'
import Logs from '@/pages/customer/Logs'
import DiskUsage from '@/pages/customer/DiskUsage'

import Accounts from '@/pages/admin/Accounts'
import AccountDetail from '@/pages/admin/AccountDetail'
import ServerHealth from '@/pages/admin/ServerHealth'
import Services from '@/pages/admin/Services'
import BandwidthRanking from '@/pages/admin/BandwidthRanking'
import MailQueue from '@/pages/admin/MailQueue'
import Firewall from '@/pages/admin/Firewall'
import Fail2ban from '@/pages/admin/Fail2ban'
import IpWhitelist from '@/pages/admin/IpWhitelist'
import AuditLog from '@/pages/admin/AuditLog'
import Waf from '@/pages/admin/Waf'
import SlowQueries from '@/pages/admin/SlowQueries'
import Webhooks from '@/pages/admin/Webhooks'
import NotificationSettings from '@/pages/admin/NotificationSettings'
import CpanelImport from '@/pages/admin/CpanelImport'
import ApiTokens from '@/pages/admin/ApiTokens'

function IndexRedirect() {
  const role = useAuth.getState().role
  return <Navigate to={role === 'admin' ? '/accounts' : '/dashboard'} replace />
}

function admin(el) {
  return <ProtectedRoute adminOnly>{el}</ProtectedRoute>
}

export const router = createBrowserRouter(
  [
    { path: '/login', element: <Login /> },
    { path: '/maintenance', element: <Maintenance /> },
    {
      path: '/',
      element: (
        <ProtectedRoute>
          <AppShell />
        </ProtectedRoute>
      ),
      children: [
        { index: true, element: <IndexRedirect /> },

        // Customer resource pages (scoped to the signed-in account).
        { path: 'dashboard', element: <Dashboard /> },
        { path: 'domains', element: <Domains /> },
        { path: 'domains/:domain', element: <DomainDetail /> },
        { path: 'email', element: <Email /> },
        { path: 'databases', element: <Databases /> },
        { path: 'files', element: <Files /> },
        { path: 'backups', element: <Backups /> },
        { path: 'apps', element: <Apps /> },
        { path: 'redis', element: <Redis /> },
        { path: 'dns', element: <Dns /> },
        { path: 'ssl', element: <Ssl /> },
        { path: 'cron', element: <Cron /> },
        { path: 'ftp', element: <Ftp /> },
        { path: 'git', element: <Git /> },
        { path: 'ssh', element: <SshKeys /> },
        { path: 'terminal', element: <Terminal /> },
        { path: 'devtools', element: <DevTools /> },
        { path: 'processes', element: <Processes /> },
        { path: 'more', element: <MoreMenu /> },
        { path: 'change-password', element: <ChangePassword /> },
        { path: 'security', element: <Security /> },
        { path: 'logs', element: <Logs /> },
        { path: 'disk-usage', element: <DiskUsage /> },

        // Admin pages.
        { path: 'accounts', element: admin(<Accounts />) },
        { path: 'accounts/:username', element: admin(<AccountDetail />) },
        { path: 'accounts/:username/domains/:domain', element: admin(<DomainDetail />) },
        { path: 'health', element: admin(<ServerHealth />) },
        { path: 'services', element: admin(<Services />) },
        { path: 'bandwidth', element: admin(<BandwidthRanking />) },
        { path: 'mail-queue', element: admin(<MailQueue />) },
        { path: 'firewall', element: admin(<Firewall />) },
        { path: 'fail2ban', element: admin(<Fail2ban />) },
        { path: 'ip-whitelist', element: admin(<IpWhitelist />) },
        { path: 'audit-log', element: admin(<AuditLog />) },
        { path: 'waf', element: admin(<Waf />) },
        { path: 'slow-queries', element: admin(<SlowQueries />) },
        { path: 'webhooks', element: admin(<Webhooks />) },
        { path: 'notifications', element: admin(<NotificationSettings />) },
        { path: 'import/cpanel', element: admin(<CpanelImport />) },
        { path: 'tokens', element: admin(<ApiTokens />) },

        { path: '*', element: <NotFound /> },
      ],
    },
    { path: '*', element: <NotFound /> },
  ],
  { basename: BASE_PATH },
)
