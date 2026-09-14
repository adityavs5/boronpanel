import { expect, test } from '@playwright/test'

const templates = ['clean', 'gradient', 'classic', 'minimal'].map((key) => ({
  key,
  name: ({ clean: 'Clean notice', gradient: 'Modern gradient', classic: 'Classic hosting', minimal: 'Minimal' })[key],
  description: `Safe ${key} suspension page`,
  html: `<html><body style="font-family:sans-serif"><h1>${key} preview</h1></body></html>`,
}))

async function mockSession(page, role, skin) {
  const writes = []
  await page.addInitScript(({ roleName, skinName }) => {
    localStorage.setItem('boron.ui', JSON.stringify({ state: { skin: skinName, theme: 'light' }, version: 0 }))
    localStorage.setItem('boron.auth', JSON.stringify({ state: { role: roleName, username: roleName === 'admin' ? 'admin' : 'sellerone' }, version: 0 }))
  }, { roleName: role, skinName: skin })
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (request.method() !== 'GET') {
      writes.push({ method: request.method(), path, body: request.postDataJSON() })
      if (path === '/api/v1/admin/resellers') return route.fulfill({ json: { id: 2, username: 'newpartner', initial_password: 'Test-Only-Partner-42!', plan_name: 'Agency', account_count: 0, max_accounts: 20, status: 'active' } })
      if (path === '/api/v1/reseller/accounts') return route.fulfill({ json: { id: 8, username: 'freshsite', initial_password: 'Test-Only-Site-42!', status: 'active' } })
      if (path.endsWith('/suspension-designs')) return route.fulfill({ json: { status: 'saved', template_key: request.postDataJSON().template_key } })
      return route.fulfill({ json: { status: 'ok' } })
    }
    let data = {}
    if (path.endsWith('/whoami')) data = { role, username: role === 'admin' ? 'admin' : 'sellerone' }
    else if (path.endsWith('/branding')) data = { panel_name: 'Boron' }
    else if (path.endsWith('/version')) data = { version: '1.2.1' }
    else if (path.endsWith('/onboarding')) data = { completed: true }
    else if (path === '/api/v1/admin/resellers/plans') data = { plans: [{ id: 1, name: 'Agency', max_accounts: 20, max_total_disk_mb: 204800, account_quota_soft_mb: 8192, account_quota_hard_mb: 10240, account_cpu_pct: 50, account_mem_mb: 2048, account_io_mb: 100, account_pids_max: 150, php_version: '8.3', reseller_count: 1 }] }
    else if (path === '/api/v1/admin/resellers') data = { resellers: [{ id: 1, username: 'sellerone', company: 'Seller One Hosting', plan_id: 1, plan_name: 'Agency', account_count: 3, max_accounts: 20, status: 'active', created_at: '2026-09-14T00:00:00Z' }] }
    else if (path === '/api/v1/reseller/dashboard') data = { profile: { username: 'sellerone', company: 'Seller One Hosting', status: 'active' }, plan: { name: 'Agency', max_accounts: 20, max_total_disk_mb: 204800 }, usage: { accounts: 3, disk_mb: 30720 }, accounts: [{ username: 'clientone', primary_domain: 'client.example', php_version: '8.3', quota_hard_mb: 10240, status: 'active' }] }
    else if (path.endsWith('/suspension-designs')) data = { current: { template_key: 'clean', accent_color: '#2563eb', heading: 'Account suspended', message: 'Please contact your hosting provider for assistance.' }, templates }
    else if (path.endsWith('/suspended-page')) data = { content: '<html><body>Suspended</body></html>' }
    else if (path.endsWith('/welcome-email')) data = { subject: null, body: null, placeholders: [] }
    await route.fulfill({ json: data })
  })
  return writes
}

for (const skin of ['evolution', 'paper-lantern']) {
  test(`${skin}: administrator manages resellers and suspension designs`, async ({ page }, info) => {
    const writes = await mockSession(page, 'admin', skin)
    await page.goto('/app/resellers')
    await expect(page.getByRole('heading', { name: 'Reseller Management' })).toBeVisible()
    await expect(page.getByRole('cell', { name: 'Seller One Hosting' })).toBeVisible()
    await page.getByRole('tab', { name: 'Plans' }).click()
    await expect(page.getByRole('cell', { name: '200 GB' })).toBeVisible()
    await page.getByRole('button', { name: 'Edit' }).click()
    await expect(page.getByRole('heading', { name: 'Edit reseller plan' })).toBeVisible()
    await expect(page.getByLabel('Maximum accounts')).toHaveValue('20')
    await page.getByRole('button', { name: 'Cancel' }).click()

    await page.getByRole('button', { name: 'New reseller' }).click()
    await page.getByLabel('Username').fill('newpartner')
    await page.getByLabel('Company').fill('New Partner Hosting')
    await page.getByLabel('Plan', { exact: true }).selectOption('1')
    await page.getByRole('dialog').getByRole('button', { name: 'Create reseller' }).click()
    await expect(page.getByRole('heading', { name: 'Save the new login' })).toBeVisible()
    await expect(page.locator('#issued-password')).toHaveValue('Test-Only-Partner-42!')
    await page.getByRole('button', { name: 'I saved it' }).click()
    expect(writes.some((item) => item.path === '/api/v1/admin/resellers')).toBe(true)

    await page.goto('/app/templates')
    await expect(page.getByRole('heading', { name: 'Templates' })).toBeVisible()
    await expect(page.locator('iframe')).toHaveCount(4)
    await page.getByRole('button', { name: /Modern gradient/ }).click()
    await page.getByLabel('Heading').fill('Website temporarily unavailable')
    await page.getByRole('button', { name: 'Apply design' }).click()
    await expect.poll(() => writes.some((item) => item.path.endsWith('/suspension-designs') && item.body.template_key === 'gradient')).toBe(true)
    await page.screenshot({ path: info.outputPath(`${skin}-resellers-templates.png`), fullPage: true })
    await page.setViewportSize({ width: 390, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })

  test(`${skin}: reseller creates and controls owned accounts`, async ({ page }, info) => {
    const writes = await mockSession(page, 'reseller', skin)
    await page.goto('/app/reseller')
    await expect(page.getByRole('heading', { name: 'Reseller Dashboard' })).toBeVisible()
    await expect(page.getByText('3 / 20')).toBeVisible()
    await expect(page.getByRole('cell', { name: 'client.example' })).toBeVisible()
    await page.getByRole('button', { name: 'Create account' }).click()
    await page.getByLabel('Username').fill('freshsite')
    await page.getByLabel('Primary domain').fill('fresh.example')
    await page.getByRole('dialog').getByRole('button', { name: 'Create account' }).click()
    await expect(page.getByRole('heading', { name: 'Save the new login' })).toBeVisible()
    await expect(page.locator('#issued-password')).toHaveValue('Test-Only-Site-42!')
    await page.getByRole('button', { name: 'I saved it' }).click()
    await page.getByRole('button', { name: 'Suspend' }).click()
    await expect.poll(() => writes.some((item) => item.path.endsWith('/clientone/suspend'))).toBe(true)
    await page.screenshot({ path: info.outputPath(`${skin}-reseller-panel.png`), fullPage: true })
    await page.setViewportSize({ width: 390, height: 844 })
    await expect(page.getByRole('navigation', { name: 'Mobile navigation' })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })
}
