import { test, expect } from '@playwright/test'

for (const skin of ['evolution', 'paper-lantern']) for (const theme of ['light', 'dark']) {
  test(`${skin} ${theme}: resource, isolation, security and stack administration`, async ({ page }, info) => {
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.addInitScript(({ skin, theme }) => {
      localStorage.setItem('boron.ui', JSON.stringify({ state: { skin, theme }, version: 0 }))
      localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'admin', username: 'admin' }, version: 0 }))
    }, { skin, theme })
    await page.route('**/api/**', async route => {
      const request = route.request()
      const path = new URL(request.url()).pathname
      let data = {}
      if (path.endsWith('/whoami')) data = { role: 'admin', username: 'admin' }
      else if (path.endsWith('/branding')) data = { panel_name: 'Boron' }
      else if (path.endsWith('/version')) data = { version: '3.0.0' }
      else if (path === '/api/v1/health') data = { cpu_pct: 11, mem_pct: 31, disks: [], uptime_seconds: 90000 }
      else if (path === '/api/v1/accounts') data = [{ username: 'hostingdemo', status: 'active' }]
      else if (path === '/api/v1/admin/resources') data = {
        users: [{ account_id: 1, username: 'hostingdemo', values: { cpu_cores: 2, cpu_weight: 100, memory_high_mb: 768, memory_max_mb: 1024, io_read_bps: 52428800, io_write_bps: 26214400, io_read_iops: 500, io_write_iops: 250, nproc: 100, entry_processes: 20 }, usage: { cpu_pct: 14.2, memory_bytes: 268435456, pids: 12 }, policy: { scope_type: 'plan', scope_id: 2 }, recent_faults: [] }],
        policies: [], history: [], faults: [],
      }
      else if (path === '/api/v1/admin/isolation') data = {
        warning: 'Web and PHP use the OpenLiteSpeed namespace. Interactive SSH is reported separately.',
        capabilities: { mount_namespace: true, private_tmp: true, pid_namespace: true },
        accounts: [{ username: 'hostingdemo', web: 'isolated', terminal: 'account_permissions', services: ['node', 'redis'], ssh_sftp: 'account_permissions' }],
      }
      else if (path === '/api/v1/firewall/status') data = { active: true, rule_count: 8 }
      else if (path === '/api/v1/waf') data = { mode: 'detect', exceptions: [] }
      else if (path === '/api/v1/admin/openlitespeed') data = { active: true, settings: { throttle_preset: 'balanced' } }
      else if (path === '/api/v1/admin/stack') data = {
        components: {
          openlitespeed: { installed: true, version: '1.8.3', targets: ['latest-supported'] },
          php: { installed: [{ target: '8.3', installed: true, version: '8.3.25' }], targets: [{ target: '8.3', installed: true, version: '8.3.25' }, { target: '8.4', installed: false, version: null }] },
          mariadb: { installed: true, version: '10.11.13', targets: ['10.11'] },
        }, jobs: [], policy: { mariadb: 'Boron supports the Ubuntu 24.04 MariaDB 10.11 series.' },
      }
      else if (path === '/api/v1/admin/stack/preview') data = { component: 'php', action: 'install', target: '8.4', packages: ['lsphp84'], free_bytes: 9999999999, blockers: [], impact: 'The PHP runtime will be added.', rollback: 'Configuration is backed up first.' }
      await route.fulfill({ json: data })
    })

    await page.goto('/app/overview')
    const serverGroup = page.getByRole('region', { name: skin === 'evolution' ? 'Server Manager' : 'Server & Databases' })
    for (const label of ['Resource Manager', 'Filesystem Isolation', 'Security Center', 'Stack Manager']) {
      await expect(serverGroup.getByRole('link', { name: new RegExp(label) })).toBeVisible()
    }

    await page.goto('/app/resource-manager')
    await expect(page.getByRole('heading', { name: 'Resource Manager' })).toBeVisible()
    await page.getByRole('button', { name: 'Manage' }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog.getByLabel('CPU cores')).toHaveValue('2')
    await expect(dialog.getByLabel('Read throughput (MB/s)')).toHaveValue('50')
    await expect(dialog.getByLabel('Write throughput (MB/s)')).toHaveValue('25')
    await dialog.getByRole('button', { name: 'Cancel' }).click()

    await page.goto('/app/filesystem-isolation')
    await expect(page.getByRole('heading', { name: 'Filesystem Isolation' })).toBeVisible()
    await expect(page.getByRole('cell', { name: 'hostingdemo', exact: true })).toBeVisible()
    await expect(page.getByText('account permissions').filter({ visible: true }).first()).toBeVisible()

    await page.goto('/app/security-center')
    await expect(page.getByRole('heading', { name: 'Security Center' })).toBeVisible()
    await expect(page.getByText(/cannot absorb a volumetric attack/)).toBeVisible()
    await page.screenshot({ path: info.outputPath(`${skin}-${theme}-security-center.png`), fullPage: true })

    await page.goto('/app/stack-manager')
    await expect(page.getByRole('heading', { name: 'Stack Manager' })).toBeVisible()
    await page.getByRole('button', { name: 'Install 8.4' }).click()
    await expect(page.getByRole('dialog').getByRole('heading', { name: 'install php 8.4' })).toBeVisible()
    await page.getByRole('dialog').getByRole('button', { name: 'Cancel' }).click()
    await page.setViewportSize({ width: 390, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    expect(errors).toEqual([])
  })
}
