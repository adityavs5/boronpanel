import { test, expect } from '@playwright/test'

async function mockSession(page, skin) {
  const writes = []
  await page.addInitScript((skin) => {
    localStorage.setItem('boron.ui', JSON.stringify({ state: { skin, theme: 'light' }, version: 0 }))
    localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'admin', username: 'admin' }, version: 0 }))
  }, skin)
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (request.method() !== 'GET') {
      writes.push({ method: request.method(), path, body: request.postDataJSON() })
      return route.fulfill({ json: path.endsWith('/policy') ? { allocation_policy: 'specific', default_server_ip_id: 1, primary_address: '192.0.2.10' } : { status: 'ok', address: '192.0.2.10', warnings: [] } })
    }
    let data = {}
    if (path.endsWith('/whoami')) data = { role: 'admin', username: 'admin' }
    else if (path.endsWith('/branding')) data = { panel_name: 'Boron' }
    else if (path.endsWith('/version')) data = { version: '1.2.1' }
    else if (path === '/api/v1/accounts') data = [{ username: 'hostingdemo', status: 'active', primary_domain: 'example.test', server_ip: '192.0.2.10', created_at: '2026-09-14T00:00:00Z' }]
    else if (path === '/api/v1/admin/plans') data = { plans: [] }
    else if (path === '/api/v1/admin/ip-management') data = {
      policy: { allocation_policy: 'primary', default_server_ip_id: null, primary_address: '192.0.2.10' },
      detected: [{ address: '192.0.2.11', family: 'ipv4', interface: 'eth0', prefix_length: 24 }],
      ips: [{ id: 1, address: '192.0.2.10', family: 'ipv4', interface: 'eth0', prefix_length: 24, allocation_mode: 'shared', label: 'Primary', active: true, present_on_host: true, assignment_count: 1, assignments: [{ username: 'hostingdemo', status: 'active' }] }],
    }
    await route.fulfill({ json: data })
  })
  return writes
}

for (const skin of ['evolution', 'paper-lantern']) {
  test(`${skin}: multi-IP management and account allocation`, async ({ page }) => {
    const writes = await mockSession(page, skin)
    await page.goto('/app/ip-management')
    await expect(page.getByRole('heading', { name: 'IP Management' })).toBeVisible()
    await expect(page.getByRole('heading', { name: '192.0.2.10' })).toBeVisible()
    await page.getByLabel('Policy').selectOption('specific')
    await page.getByLabel('Default IP').selectOption('1')
    await page.getByRole('button', { name: 'Save policy' }).click()
    await expect.poll(() => writes.some((item) => item.path.endsWith('/policy') && item.body.default_server_ip_id === 1)).toBe(true)

    await page.getByRole('button', { name: 'Assign account' }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByLabel('Account').selectOption('hostingdemo')
    await dialog.getByLabel('Server IP').selectOption('1')
    await dialog.getByRole('button', { name: 'Assign IP' }).click()
    await expect.poll(() => writes.some((item) => item.path.endsWith('/accounts/hostingdemo'))).toBe(true)

    await page.goto('/app/accounts')
    await page.getByRole('button', { name: 'Create account' }).first().click()
    await expect(page.getByLabel('IP assignment')).toBeVisible()
    await page.getByLabel('IP assignment').selectOption('specific')
    await expect(page.getByLabel('Server IP')).toBeVisible()
    await page.setViewportSize({ width: 390, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })
}
