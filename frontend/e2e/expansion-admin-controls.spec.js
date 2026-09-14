import { test, expect } from '@playwright/test'

const settings = {
  max_connections: 10000, max_ssl_connections: 10000, connection_timeout: 300,
  keep_alive_timeout: 5, max_keep_alive_requests: 10000, gzip_level: 6,
  brotli_level: 6, gzip_enabled: true, brotli_enabled: true, quic_enabled: true,
  log_level: 'WARN', log_keep_days: 30,
}

async function session(page, skin) {
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
      if (path.endsWith('/openlitespeed/settings')) return route.fulfill({ json: { settings: { ...settings, ...request.postDataJSON() } } })
      if (path.endsWith('/credentials/reset')) return route.fulfill({ json: { username: 'admin', password: 'Generated-Test-Password', reset_at: '2026-09-14T12:00:00Z' } })
      return route.fulfill({ json: { status: 'ok' } })
    }
    let data = {}
    if (path.endsWith('/whoami')) data = { role: 'admin', username: 'admin' }
    else if (path.endsWith('/branding')) data = { panel_name: 'Boron' }
    else if (path.endsWith('/version')) data = { version: '1.2.1' }
    else if (path === '/api/v1/admin/openlitespeed') data = { active: true, config_valid: true, settings, credential: { username: 'admin', password_available: false }, accounts: 3, domains: 7, version: 'OpenLiteSpeed 1.8', webadmin_port: 7080 }
    else if (path === '/api/v1/firewall/rules') data = { active: true, rules: [{ rule_id: 'one', action: 'allow', port: 2222, protocol: 'tcp', from: 'any', comment: 'boron-panel', protected: true }], bypass: [{ bypass_id: 'trusted', address: '198.51.100.42', label: 'Office VPN' }] }
    else if (path === '/api/v1/admin/ssl') data = { certbot_timer_active: true, domains: [{ username: 'hostingdemo', account_status: 'active', domain: 'example.test', cert_status: 'missing', ssl_status: 'none', is_wildcard: false, system: false }] }
    await route.fulfill({ json: data })
  })
  return writes
}

for (const skin of ['evolution', 'paper-lantern']) {
  test(`${skin}: firewall, OLS and global SSL controls`, async ({ page }) => {
    const writes = await session(page, skin)
    await page.goto('/app/firewall')
    await expect(page.getByRole('heading', { name: 'Firewall' })).toBeVisible()
    await expect(page.getByText('198.51.100.42')).toBeVisible()
    await page.getByRole('button', { name: 'Add trusted IP' }).click()
    await expect(page.getByRole('heading', { name: 'Add full-access IP' })).toBeVisible()
    const bypassDialog = page.getByRole('dialog')
    await bypassDialog.getByLabel('IP address or CIDR').fill('203.0.113.10')
    await bypassDialog.getByLabel('Label').fill('Recovery VPN')
    await bypassDialog.getByRole('button', { name: 'Add trusted IP' }).click()
    await expect.poll(() => writes.some((item) => item.path.endsWith('/firewall/bypass'))).toBe(true)

    await page.goto('/app/openlitespeed')
    await expect(page.getByRole('heading', { name: 'OpenLiteSpeed' })).toBeVisible()
    await expect(page.getByText('Reset once to make a password available.')).toBeVisible()
    await page.getByLabel('Performance profile').selectOption('high')
    await expect(page.getByLabel('Maximum connections')).toHaveValue('30000')
    await page.getByRole('button', { name: 'Validate and apply' }).click()
    await expect.poll(() => writes.some((item) => item.path.endsWith('/openlitespeed/settings'))).toBe(true)
    await page.getByRole('button', { name: 'Reset password' }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Reset password' }).click()
    await expect(page.getByRole('heading', { name: 'WebAdmin login' })).toBeVisible()
    await page.getByRole('dialog').getByRole('button', { name: 'Done' }).click()

    await page.goto('/app/ssl')
    await expect(page.getByRole('heading', { name: 'SSL Certificates' })).toBeVisible()
    await expect(page.getByRole('cell', { name: /hostingdemo/ }).first()).toBeVisible()
    await page.getByRole('button', { name: 'Manage SSL for example.test' }).click()
    await page.getByRole('button', { name: 'Issue certificate' }).click()
    await expect(page.getByText("An SSL certificate for example.test will be requested via Let's Encrypt.")).toBeVisible()
    await page.getByRole('dialog').getByRole('button', { name: 'Issue certificate' }).click()
    await expect.poll(() => writes.some((item) => item.path.endsWith('/ssl/domains/example.test/issue'))).toBe(true)
    await page.setViewportSize({ width: 390, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })
}
