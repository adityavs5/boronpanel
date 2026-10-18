import { test, expect } from '@playwright/test'
import { build } from 'esbuild'
import path from 'node:path'

async function fixture(page, skin = 'evolution', theme = 'light', role = 'admin') {
  const state = { writes: [], failSave: false, errors: [] }
  const account = { id: 1, username: 'alpha', status: 'active', primary_domain: 'example.com', php_version: '8.3', cpu_cores: 2, cpu_pct: 200, mem_mb: 460, io_mb: 50, pids_max: 100, host_cpu_cores: 8, host_memory_gb: 16, quota_hard_mb: 12288 }
  const plan = { id: 1, name: 'Starter', cpu_cores: 1, cpu_pct: 100, cpu_weight: 100, mem_mb: 1024, memory_high_mb: 896, memory_max_mb: 1024, io_mb: 50, pids_max: 100, nproc: 100, quota_soft_mb: 10240, quota_hard_mb: 12288, inode_soft_limit: 450000, inode_hard_limit: 500000 }
  const usageLimits = { bandwidth_limit_mb: 1024, database_limit: 1, email_account_limit: 1, subdomain_limit: 1, auto_suspend_at_100: false, updated_at: '2026-10-18T08:00:00Z' }
  let notifications = { sender_address: 'panel@example.com', events: {} }
  page.on('pageerror', error => state.errors.push(error.message))
  await page.addInitScript(({ skin, theme, role }) => {
    localStorage.setItem('boron.ui', JSON.stringify({ state: { skin, theme }, version: 0 }))
    localStorage.setItem('boron.auth', JSON.stringify({ state: { role, username: role === 'admin' ? 'admin' : 'alpha' }, version: 0 }))
  }, { skin, theme, role })
  await page.route('**/api/**', async route => {
    const req = route.request(), path = new URL(req.url()).pathname
    const writing = !['GET', 'HEAD'].includes(req.method())
    if (writing) state.writes.push({ path, body: req.postDataJSON() })
    if (writing && state.failSave) return route.fulfill({ status: 500, json: { detail: 'Synthetic save failure' } })
    let data = {}
    if (path.endsWith('/whoami')) data = { role, username: role === 'admin' ? 'admin' : 'alpha' }
    else if (path.endsWith('/branding')) data = { panel_name: 'Boron' }
    else if (path.endsWith('/onboarding')) data = { completed: true }
    else if (path === '/api/v1/accounts') data = [account]
    else if (path === '/api/v1/accounts/alpha') data = account
    else if (path === '/api/v1/accounts/alpha/php-version' && writing) Object.assign(account, req.postDataJSON())
    else if (path === '/api/v1/accounts/alpha/limits' && writing) { Object.assign(account, req.postDataJSON()); account.cpu_cores = account.cpu_pct / 100 }
    else if (path === '/api/v1/accounts/alpha/usage-limits') {
      if (writing) Object.assign(usageLimits, req.postDataJSON(), { updated_at: '2026-10-18T09:00:00Z' })
      else if (state.usageRefetch) await state.usageRefetch
      data = usageLimits
    }
    else if (path === '/api/v1/admin/accounts/alpha/apply-plan/1' && writing) Object.assign(account, { plan_id: 1, cpu_pct: 400, cpu_cores: 4 })
    else if (path.endsWith('/domains')) data = { domains: [{ domain: 'example.com', kind: 'primary', docroot: '/home/alpha/public_html', ssl_status: 'issued' }, { domain: 'blog.example.com', kind: 'subdomain', docroot: '/home/alpha/blog' }] }
    else if (path.endsWith('/parked-domains')) data = { parked_domains: [] }
    else if (path.includes('/dns/zones/')) data = { zone: 'example.com', records: [{ name: 'www.example.com.', type: 'A', ttl: 3600, values: ['192.0.2.1'] }] }
    else if (path === '/api/v1/admin/plans') data = { plans: [plan] }
    else if (path === '/api/v1/admin/plans/1') { if (writing) Object.assign(plan, req.postDataJSON()); data = plan }
    else if (path === '/api/v1/admin/resources') data = { host: { cpu_cores: 8, memory_gb: 16 }, users: [{ account_id: 1, username: 'alpha', values: { cpu_cores: 2, memory_high_mb: 460, memory_max_mb: 1024, nproc: 100 }, recent_faults: [] }], policies: [], faults: [], history: [] }
    else if (path === '/api/v1/admin/notifications/settings') { if (writing) notifications = req.postDataJSON(); data = notifications }
    else if (path.endsWith('/namespace')) data = { enabled: true, eligible: true }
    else if (path.endsWith('/usage')) data = { current: {}, resources: {} }
    else if (path.endsWith('/resellers')) data = { resellers: [] }
    else if (path.endsWith('/reseller-plans')) data = { plans: [] }
    else if (path.endsWith('/dns-cluster')) data = { peers: [], jobs: [] }
    else if (path.endsWith('/webhooks')) data = { webhooks: [] }
    else if (path.endsWith('/snapshots/destinations')) data = { destinations: [{ id: 1, name: 'Local test destination', enabled: true, status: 'ready', kind: 'local' }] }
    else if (path.endsWith('/snapshots/policies')) data = { policies: [] }
    else if (path.endsWith('/catalog/accounts')) data = { accounts: [] }
    await route.fulfill({ json: data })
  })
  return state
}

async function reachableDialog(page, name) {
  const dialog = page.getByRole('dialog').last()
  await expect(dialog).toBeVisible()
  await expect(dialog.locator('.panel-dialog-footer')).toBeInViewport()
  const before = await dialog.evaluate(el => {
    const body = el.querySelector('.panel-dialog-body'), footer = el.querySelector('.panel-dialog-footer')
    return { bottom: el.getBoundingClientRect().bottom, footerBottom: footer.getBoundingClientRect().bottom, range: body.scrollHeight - body.clientHeight }
  })
  expect(before.footerBottom, `${name}: footer inside dialog`).toBeLessThanOrEqual(before.bottom + 1)
  expect(before.range, `${name}: body has actual scroll range`).toBeGreaterThan(0)
  const body = dialog.locator('.panel-dialog-body').first()
  await body.hover()
  await page.mouse.wheel(0, 2400)
  await expect.poll(() => body.evaluate(el => el.scrollTop)).toBeGreaterThan(0)
  await body.evaluate(el => { el.scrollTop = el.scrollHeight })
  await expect(dialog.locator('.panel-dialog-footer')).toBeInViewport()
  expect(await body.evaluate(el => Math.abs(el.scrollTop - (el.scrollHeight - el.clientHeight)))).toBeLessThanOrEqual(1)
  const lastControl = body.locator(':is(input, select, textarea, [role=checkbox], [role=switch], [role=combobox], [role=radio]):not([aria-hidden=true]):not([type=hidden])').filter({ visible: true }).last()
  await lastControl.scrollIntoViewIfNeeded()
  await expect(lastControl).toBeInViewport()
  await expect(dialog.locator('.panel-dialog-footer')).toBeInViewport()
  await page.keyboard.press('Escape')
}

for (const skin of ['evolution', 'paper-lantern']) for (const theme of ['light', 'dark']) {
  test(`${skin} ${theme}: six long forms keep actions reachable`, async ({ browser }) => {
    test.setTimeout(120000)
    for (const role of ['admin', 'customer']) {
      const context = await browser.newContext({ viewport: { width: 375, height: 700 } }), page = await context.newPage()
      const state = await fixture(page, skin, theme, role)
      const cases = role === 'admin' ? [['resellers', 'New plan'], ['dns-cluster', 'Add peer'], ['resource-manager', 'Manage'], ['webhooks', 'Add webhook']] : [['subdomains', 'Add subdomain'], ['dns', 'Add record']]
      for (const [path, button] of cases) {
        await page.goto(`/app/${path}`)
        if (button === 'New plan') {
          await page.getByRole('button', { name: 'More', exact: true }).click()
          await page.getByRole('menuitem', { name: 'New plan', exact: true }).click()
        } else await page.getByRole('button', { name: button, exact: true }).filter({ visible: true }).first().click()
        if (path === 'dns') await page.getByRole('textbox', { name: 'DNS record name', exact: true }).fill('external.example.org')
        await reachableDialog(page, path)
        if (path === 'webhooks') {
          await page.setViewportSize({ width: 1440, height: 900 })
          await page.getByRole('button', { name: 'Add webhook', exact: true }).first().click()
          await reachableDialog(page, 'desktop webhook')
        }
      }
      expect(state.writes).toEqual([])
      expect(state.errors).toEqual([])
      await context.close()
    }
  })

  test(`${skin} ${theme}: table headers follow their real scroller and checkboxes stay square`, async ({ page }) => {
    const state = await fixture(page, skin, theme)
    await page.goto('/app/notifications')
    const scroll = page.locator('.panel-table-scroll')
    await scroll.scrollIntoViewIfNeeded()
    await page.locator('#panel-main').evaluate(el => { el.scrollTop = el.scrollHeight })
    await scroll.hover()
    await page.mouse.wheel(0, 400)
    await expect.poll(() => scroll.evaluate(el => el.scrollTop)).toBeGreaterThan(0)
    const geometry = await scroll.evaluate(el => ({ top: el.getBoundingClientRect().top, header: el.querySelector('th').getBoundingClientRect().top }))
    expect(Math.abs(geometry.header - geometry.top)).toBeLessThanOrEqual(2)
    await expect(scroll.locator('th').first()).toBeInViewport()
    await page.goto('/app/accounts/alpha?tab=domains')
    const selectAll = page.getByRole('checkbox', { name: 'Select all domains', exact: true })
    await expect(selectAll).toBeVisible()
    const rect = await selectAll.boundingBox()
    expect(Math.abs(rect.width - rect.height)).toBeLessThanOrEqual(1)
    await selectAll.check()
    await expect(selectAll).toBeChecked()
    const domainScroll = page.locator('.panel-table-scroll')
    await domainScroll.evaluate(el => { el.scrollLeft = el.scrollWidth })
    await expect(domainScroll.locator('thead .table-actions-cell')).toBeInViewport()
    expect(state.errors).toEqual([])
  })
}

test('notification drafts survive errors, guard navigation, and clear after saving', async ({ page }) => {
  const state = await fixture(page)
  await page.goto('/app/notifications')
  const sender = page.getByRole('textbox', { name: 'From address', exact: true })
  const bar = page.getByRole('region', { name: 'Unsaved changes', exact: true })
  await sender.fill('invalid-email')
  await bar.getByRole('button', { name: 'Save changes', exact: true }).click()
  expect(state.writes).toEqual([])
  await sender.fill('draft@example.com')
  expect(await page.evaluate(() => { const event = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(event); return event.defaultPrevented })).toBe(true)
  state.failSave = true
  await bar.getByRole('button', { name: 'Save changes', exact: true }).click()
  await expect.poll(() => state.writes.length).toBe(1)
  await expect(bar.getByRole('button', { name: 'Save changes', exact: true })).toBeEnabled()
  await page.getByRole('textbox', { name: 'Search navigation', exact: true }).fill('Accounts')
  await page.locator('a[href="/app/accounts"]').first().click()
  await expect(page.getByRole('dialog', { name: 'Discard unsaved changes?' })).toBeVisible()
  await page.getByRole('button', { name: 'Keep editing', exact: true }).click()
  await expect(sender).toHaveValue('draft@example.com')
  state.failSave = false
  await bar.getByRole('button', { name: 'Save changes', exact: true }).click()
  await expect(bar).toHaveCount(0)
  await page.locator('a[href="/app/accounts"]').first().click()
  await expect(page).toHaveURL(/\/accounts$/)
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(state.errors).toEqual([])
})

test('plan sticky save, discard and successful navigation preserve the plan contract', async ({ page }) => {
  const state = await fixture(page)
  await page.setViewportSize({ width: 375, height: 700 })
  await page.goto('/app/plans/1/edit')
  await page.getByRole('textbox', { name: 'Plan name', exact: true }).fill('Edited starter')
  const bar = page.getByRole('region', { name: 'Unsaved changes', exact: true })
  await page.getByRole('button', { name: 'More', exact: true }).click()
  await page.getByRole('menuitem', { name: 'Plans', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Discard unsaved changes?' })).toBeVisible()
  await page.getByRole('button', { name: 'Keep editing', exact: true }).click()
  await page.locator('#panel-main').evaluate(el => { el.scrollTop = el.scrollHeight })
  await expect(bar.getByRole('button', { name: 'Save plan', exact: true })).toBeInViewport()
  await bar.getByRole('button', { name: 'Discard', exact: true }).click()
  await expect(page.getByRole('textbox', { name: 'Plan name', exact: true })).toHaveValue('Starter')
  await page.getByRole('textbox', { name: 'Plan name', exact: true }).fill('Saved starter')
  await bar.getByRole('button', { name: 'Save plan', exact: true }).click()
  await expect(page).toHaveURL(/\/plans$/)
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(state.writes.at(-1).body.name).toBe('Saved starter')
  expect(state.errors).toEqual([])
})

test('account PHP and resource drafts guard tab changes and discard restores both', async ({ page }) => {
  const state = await fixture(page)
  await page.goto('/app/accounts/alpha?tab=settings')
  await page.getByRole('combobox', { name: 'Version', exact: true }).click()
  await page.getByRole('option', { name: 'PHP 8.2', exact: true }).click()
  await page.getByRole('spinbutton', { name: 'CPU cores', exact: true }).fill('3')
  const bar = page.getByRole('region', { name: 'Unsaved changes', exact: true })
  await expect(bar).toContainText('2 sections')
  await page.getByRole('tab', { name: 'Overview', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Discard unsaved changes?' })).toBeVisible()
  await page.getByRole('button', { name: 'Keep editing', exact: true }).click()
  await expect(page.getByRole('spinbutton', { name: 'CPU cores', exact: true })).toHaveValue('3')
  await bar.getByRole('button', { name: 'Discard', exact: true }).click()
  await expect(page.getByRole('combobox', { name: 'Version', exact: true })).toHaveText('PHP 8.3')
  await expect(page.getByRole('spinbutton', { name: 'CPU cores', exact: true })).toHaveValue('2')
  await page.getByRole('tab', { name: 'Overview', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(state.writes).toEqual([])
  expect(state.errors).toEqual([])
})

test('an already-constrained backup dialog keeps grouped field names and hints', async ({ page }) => {
  const state = await fixture(page)
  await page.setViewportSize({ width: 375, height: 700 })
  await page.goto('/app/backup-jobs')
  await page.getByRole('button', { name: 'Create job', exact: true }).click()
  await expect(page.getByRole('combobox', { name: 'Delivery frequency', exact: true })).toBeAttached()
  await reachableDialog(page, 'backup job')
  expect(state.writes).toEqual([])
  expect(state.errors).toEqual([])
})

test('saved account limits follow a newly applied plan without creating a false draft', async ({ page }) => {
  const state = await fixture(page)
  await page.goto('/app/accounts/alpha?tab=settings')
  await page.getByRole('spinbutton', { name: 'CPU cores', exact: true }).fill('3')
  const bar = page.getByRole('region', { name: 'Unsaved changes', exact: true })
  await bar.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(bar).toHaveCount(0)
  await page.getByRole('combobox', { name: 'Plan', exact: true }).click()
  await page.getByRole('option', { name: 'Starter', exact: true }).click()
  await page.getByRole('button', { name: 'Apply', exact: true }).click()
  await expect(page.getByRole('spinbutton', { name: 'CPU cores', exact: true })).toHaveValue('4')
  await expect(bar).toHaveCount(0)
  expect(state.writes).toHaveLength(2)
  expect(state.errors).toEqual([])
})

test('equivalent account usage values stay clean and changed values save once', async ({ page }) => {
  const state = await fixture(page)
  await page.goto('/app/accounts/alpha?tab=usage-limits')
  await page.getByText('Additional usage limits', { exact: true }).click()
  const bandwidth = page.getByRole('spinbutton', { name: 'Bandwidth limit (GB/month)', exact: true })
  const databases = page.getByRole('spinbutton', { name: 'Database limit', exact: true })
  const bar = page.getByRole('region', { name: 'Unsaved changes', exact: true })
  await bandwidth.fill('1.0')
  await databases.fill('01')
  await expect(bar).toHaveCount(0)
  await bandwidth.fill('1.0001') // The existing API rounds bandwidth to whole MB.
  await expect(bar).toHaveCount(0)
  expect(state.writes).toEqual([])
  await databases.fill('2')
  await bar.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(bar).toHaveCount(0)
  await expect(databases).toHaveValue('2')
  expect(state.writes).toEqual([{ path: '/api/v1/accounts/alpha/usage-limits', body: { database_limit: 2 } }])
  expect(state.errors).toEqual([])
})

test('account usage refetch preserves an edit made after the preceding save', async ({ page }) => {
  const state = await fixture(page)
  await page.goto('/app/accounts/alpha?tab=usage-limits')
  await page.getByText('Additional usage limits', { exact: true }).click()
  const databases = page.getByRole('spinbutton', { name: 'Database limit', exact: true })
  const bar = page.getByRole('region', { name: 'Unsaved changes', exact: true })
  await databases.fill('2')
  let releaseRefetch
  state.usageRefetch = new Promise(resolve => { releaseRefetch = resolve })
  await bar.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(bar).toHaveCount(0)
  await databases.fill('3')
  await expect(bar).toBeVisible()
  const refetched = page.waitForResponse(response => response.url().endsWith('/usage-limits') && response.request().method() === 'GET')
  releaseRefetch()
  await refetched
  await expect(databases).toHaveValue('3')
  await expect(bar).toBeVisible()
  expect(state.writes).toEqual([{ path: '/api/v1/accounts/alpha/usage-limits', body: { database_limit: 2 } }])
  expect(state.errors).toEqual([])
})

test('FormField binds nested controls, combines hints, and keeps grouped names distinct', async ({ page }) => {
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  // Isolate the shared composition contract from pages with explicit field IDs.
  const fixture = await build({
    stdin: { resolveDir: process.cwd(), loader: 'jsx', contents: `
      import React from 'react'; import { createRoot } from 'react-dom/client';
      import { FormField, Input } from './src/components/ui/Input.jsx';
      import { Select } from './src/components/ui/Select.jsx';
      createRoot(document.getElementById('root')).render(<>
        <p id="existing-help">Keep this description.</p>
        <FormField label="Delivery frequency" hint="Choose when to deliver."><div><span aria-hidden="true">Schedule</span><Select aria-describedby="existing-help"><option>Daily</option></Select><button type="button">Preview</button></div></FormField>
        <FormField label="Mailbox quota (GB)" hint="Storage allowance."><div><Input type="number" defaultValue="1"/><span>GB</span></div></FormField>
        <FormField label="Start time" hint="Local server time."><div><Select aria-label="Hour"><option>02</option></Select><Select aria-label="Minute"><option>30</option></Select></div></FormField>
        <FormField label="Invalid quota"><Input invalid defaultValue="invalid"/></FormField>
        <FormField label="Folders to migrate"><div><label><input type="checkbox"/>Inbox</label></div></FormField>
      </>);
    ` },
    bundle: true, write: false, format: 'iife', jsx: 'automatic', define: { 'process.env.NODE_ENV': '"production"' }, alias: { '@': path.resolve('src') },
  })
  await page.setContent('<div id="root"></div>')
  await page.addScriptTag({ content: fixture.outputFiles[0].text })
  await expect(page.getByRole('combobox', { name: 'Delivery frequency', exact: true })).toHaveAccessibleDescription('Keep this description. Choose when to deliver.')
  const quota = page.getByRole('spinbutton', { name: 'Mailbox quota (GB)', exact: true })
  await expect(quota).toHaveAccessibleDescription('Storage allowance.')
  await page.getByText('Mailbox quota (GB)', { exact: true }).click()
  await expect(quota).toBeFocused()
  const group = page.getByRole('group', { name: 'Start time', exact: true })
  await expect(group.getByRole('combobox', { name: 'Hour', exact: true })).toHaveAccessibleDescription('Local server time.')
  await expect(group.getByRole('combobox', { name: 'Minute', exact: true })).toHaveAccessibleDescription('Local server time.')
  await expect(page.getByRole('textbox', { name: 'Invalid quota', exact: true })).toHaveAttribute('aria-invalid', 'true')
  await expect(page.getByRole('group', { name: 'Folders to migrate', exact: true }).getByRole('checkbox', { name: 'Inbox', exact: true })).toBeVisible()
  expect(errors).toEqual([])
})
