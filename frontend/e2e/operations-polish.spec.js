import { test, expect } from '@playwright/test'

async function mockOperations(page, skin, theme, deferred = new Set()) {
  await page.addInitScript(({ skin, theme }) => {
    localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'admin', username: 'preview' }, version: 0 }))
    localStorage.setItem('boron.ui', JSON.stringify({ state: { skin, theme }, version: 0 }))
  }, { skin, theme })
  const pending = new Map()
  const writes = []
  const backups = Array.from({ length: 12 }, (_, index) => ({
    id: `pre-update-3.2.${index}-20261018-120000`, created_at: '2026-10-18T12:00:00Z', integrity_ok: true, schema_compatible: true,
  }))
  const resources = { host: { cpu_cores: 12, memory_gb: 8 }, users: [{
    account_id: 1, username: 'alpha', values: { memory_high_mb: 460, memory_max_mb: 512 },
    recent_faults: [], capacity_adjusted: true, configured_cpu_cores: 16,
  }], policies: [], history: [], faults: [] }
  const dataFor = path => {
    if (path.endsWith('/whoami')) return { role: 'admin', username: 'preview' }
    if (path.endsWith('/branding')) return { panel_name: 'Boron', terminal_banner: 'Welcome' }
    if (path.endsWith('/update/status')) return { configured: true, channel: 'beta', current_version: '3.2.2', latest_version: '3.2.2', update_available: false, active_job: null }
    if (path.endsWith('/update/history')) return { jobs: [] }
    if (path.endsWith('/update/database-backups')) return { backups }
    if (path === '/api/v1/accounts') return [{ username: 'alpha', status: 'active' }]
    if (path.endsWith('/tasks')) return { kinds: ['update', 'support-bundle'], tasks: [
      { key: 'update:1', id: 1, kind: 'update', label: 'Panel update', status: 'completed', progress: 100 },
      { key: 'support-bundle:2', id: 2, kind: 'support-bundle', label: 'Support bundle', status: 'expired', progress: null },
    ] }
    if (path.endsWith('/admin/stack')) return { components: {
      openlitespeed: { installed: true, version: '1.8.0' }, mariadb: { installed: true, version: '10.11' },
      php: { installed: ['8.1', '8.2', '8.3'].map(target => ({ target, version: target })), targets: ['8.1', '8.2', '8.3'].map(target => ({ target, installed: true, eol_date: '2028-12-31' })) },
    }, policy: { mariadb: 'Supported database version.' }, php_session_migration: { affected_count: 0 }, jobs: [] }
    if (path.endsWith('/admin/resources')) return resources
    if (path.endsWith('/admin/server-setup')) return { system: {}, dns: {}, current_step: 5, completed_steps: 1, step_results: { 5: { state: 'completed' } }, steps: Array.from({ length: 13 }, (_, i) => ({ number: i + 1, title: i === 4 ? 'DNS verification' : `Setup section ${i + 1}`, dependencies: i ? [1] : [] })) }
    if (path.endsWith('/snapshots/destinations')) return { destinations: [{ id: 1, name: 'Local', kind: 'local', enabled: true, status: 'ready' }] }
    if (path.endsWith('/snapshots/policies')) return { policies: [] }
    if (path.endsWith('/snapshots/catalog/accounts')) return { counts: { active: 4, former: 0 }, accounts: [] }
    if (path.endsWith('/snapshots/destination-operations')) return { operations: [] }
    if (path.endsWith('/snapshots/runs')) return { runs: [{ id: 1, username: 'alpha', status: 'completed', snapshot_id: 'saved', started_at: '2026-10-18T12:00:00Z' }, { id: 2, username: 'alpha', status: 'failed' }, { id: 3, username: 'alpha', status: 'running' }] }
    if (path.endsWith('/firewall/rules')) return { active: true, ipv6: true, rules: [], bypass: [], temporary_bans: [], recent_changes: [] }
    return {}
  }
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (route.request().method() !== 'GET') writes.push(path)
    if (deferred.has(path)) await new Promise(resolve => pending.set(path, resolve))
    await route.fulfill({ json: dataFor(path) })
  })
  return { writes, release(path) { deferred.delete(path); pending.get(path)?.() } }
}

async function noOverflow(page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
}

for (const skin of ['evolution', 'paper-lantern']) for (const theme of ['light', 'dark']) {
  test(`operations controls preserve state and fit ${skin} ${theme}`, async ({ page }) => {
    test.setTimeout(90_000)
    const mocked = await mockOperations(page, skin, theme)
    await page.goto('/app/tasks')
    await expect(page.getByRole('table').getByText('Progress unavailable', { exact: true })).toBeVisible()
    await expect(page.getByRole('table').getByText('100%', { exact: true })).toBeVisible()
    const controls = await Promise.all(['Search tasks', 'Status', 'Task type'].map(label => page.getByLabel(label, { exact: true }).boundingBox()))
    expect(Math.max(...controls.map(box => box.y)) - Math.min(...controls.map(box => box.y))).toBeLessThan(2)
    await page.getByText('Date and account filters', { exact: true }).click()
    await expect(page.getByLabel('From', { exact: true })).toBeVisible()
    await expect(page.getByLabel('To', { exact: true })).toBeVisible()
    await page.setViewportSize({ width: 375, height: 900 })
    await noOverflow(page)
    await page.screenshot({ path: test.info().outputPath('tasks-mobile.png') })

    await page.goto('/app/server-setup')
    await expect(page.getByRole('heading', { name: 'Verify DNS and delegation', exact: true })).toBeVisible()
    await expect(page.getByText(/Complete earlier steps first/)).toHaveCount(0)
    await expect(page.getByRole('navigation', { name: 'Setup steps' })).toBeHidden()
    const verify = await page.getByRole('button', { name: 'Verify DNS now', exact: true }).boundingBox()
    expect(verify.y + verify.height).toBeLessThan(900)
    await page.screenshot({ path: test.info().outputPath('setup-mobile.png') })
    await page.getByRole('button', { name: 'All steps', exact: true }).click()
    await expect(page.getByRole('navigation', { name: 'Setup steps' })).toBeVisible()
    await page.getByRole('button', { name: 'Step 2 Setup section 2', exact: true }).click()
    await expect(page.getByText(/Complete earlier steps first/)).toHaveCount(1)
    await expect(page.getByRole('navigation', { name: 'Setup steps' })).toBeHidden()
    await noOverflow(page)

    await page.goto('/app/resource-manager')
    await page.getByRole('button', { name: 'Manage', exact: true }).click()
    const memory = page.getByLabel('Soft memory limit (GB)', { exact: true })
    await expect(memory).toHaveValue('0.44921875')
    expect(await memory.evaluate(el => el.checkValidity())).toBe(true)
    await page.screenshot({ path: test.info().outputPath('resource-limits-mobile.png') })
    await page.getByRole('button', { name: 'Cancel', exact: true }).click()

    await page.goto('/app/branding')
    await expect(page.getByRole('textbox', { name: /^Panel name/ })).toBeVisible()
    await expect(page.getByLabel('Terminal banner', { exact: true })).toBeHidden()
    await page.getByText('Advanced: admin terminal welcome', { exact: true }).click()
    await expect(page.getByLabel('Terminal banner', { exact: true })).toBeVisible()

    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/app/stack-manager')
    await expect(page.getByRole('button', { name: 'Remove 8.3', exact: true })).toHaveCount(0)
    await page.getByRole('button', { name: 'More actions for PHP 8.3', exact: true }).click()
    await expect(page.getByRole('menuitem', { name: 'Remove 8.3', exact: true })).toBeVisible()
    await page.keyboard.press('Escape')
    const ols = await page.locator('.panel-card').filter({ has: page.getByRole('heading', { name: 'OpenLiteSpeed', exact: true }) }).boundingBox()
    const php = await page.locator('.panel-card').filter({ has: page.getByRole('heading', { name: 'PHP runtimes', exact: true }) }).boundingBox()
    expect(ols.height).toBeLessThan(php.height)
    await page.screenshot({ path: test.info().outputPath('stack-desktop.png') })

    await page.setViewportSize({ width: 768, height: 900 })
    await page.goto('/app/updates')
    await expect(page.getByRole('heading', { name: 'Update history', exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Restore database', exact: true })).toHaveCount(0)
    const disclosure = page.locator('details').filter({ has: page.locator('summary', { hasText: 'Panel database recovery' }) })
    await disclosure.locator(':scope > summary').click()
    await expect(disclosure.getByText('1–5 of 12', { exact: true })).toBeVisible()
    await expect(disclosure.getByRole('button', { name: 'Restore database', exact: true })).toHaveCount(5)
    await expect(disclosure.getByRole('table').getByText('Before update to v3.2.0', { exact: true })).toBeVisible()
    await disclosure.getByRole('button', { name: 'Next page', exact: true }).click()
    await expect(disclosure.getByText('6–10 of 12', { exact: true })).toBeVisible()
    await noOverflow(page)
    await page.screenshot({ path: test.info().outputPath('recovery-tablet.png') })
    expect(mocked.writes).toEqual([])
  })
}

test('backup and firewall reads show loading until real state arrives', async ({ page }) => {
  test.setTimeout(90_000)
  const destinationPath = '/api/v1/backups/snapshots/destinations'
  const catalogPath = '/api/v1/backups/snapshots/catalog/accounts'
  const firewallPath = '/api/v1/firewall/rules'
  const mocked = await mockOperations(page, 'evolution', 'light', new Set([destinationPath, catalogPath, firewallPath]))
  await page.goto('/app/backup-jobs')
  await expect(page.getByText(/Loading accounts and destinations/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Create job', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Active accounts (Loading…)', exact: true })).toBeVisible()
  mocked.release(destinationPath)
  mocked.release(catalogPath)
  await expect(page.getByRole('button', { name: 'Active accounts (4)', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Create job', exact: true }).click()
  await expect(page.getByLabel('Delivery frequency', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Cancel', exact: true }).click()
  await page.goto('/app/backup-jobs?tab=history&view=recovery')
  await expect(page.getByRole('button', { name: 'Backup #1', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Backup #2', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'View queue and logs', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Backup #2', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Backup #3', exact: true })).toBeVisible()
  await page.goto('/app/firewall')
  await expect(page.getByRole('heading', { name: 'Firewall', exact: true })).toBeVisible()
  await expect(page.getByText('Inactive', { exact: true })).toHaveCount(0)
  await expect(page.getByText('No temporary blocks are active.', { exact: true })).toHaveCount(0)
  mocked.release(firewallPath)
  await expect(page.getByText('Active', { exact: true })).toBeVisible()
  await expect(page.getByText('No temporary blocks are active.', { exact: true })).toBeVisible()
  expect(mocked.writes).toEqual([])
})

test('health alerts switch has an accessible name', async ({ page }) => {
  const mocked = await mockOperations(page, 'evolution', 'light')
  await page.goto('/app/health')
  const alerts = page.getByRole('switch', { name: 'Alerts', exact: true })
  await expect(alerts).toBeVisible()
  await expect(alerts).toBeChecked()
  await alerts.focus()
  await expect(alerts).toBeFocused()
  expect(mocked.writes).toEqual([])
})
