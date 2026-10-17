import { test, expect } from '@playwright/test'

async function setup(page, theme = 'light') {
  await page.addInitScript(theme => {
    localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'admin', username: 'preview' }, version: 0 }))
    localStorage.setItem('boron.ui', JSON.stringify({ state: { skin: 'evolution', theme }, version: 0 }))
  }, theme)
  const requests = []
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    let data = {}
    if (path.endsWith('/whoami')) data = { role: 'admin', username: 'preview' }
    else if (path.endsWith('/branding')) data = { panel_name: 'SiteCountry BoronPanel' }
    else if (path.endsWith('/plans')) data = { plans: [{ id: 1, name: 'Standard', cpu_cores: 2, mem_mb: 2048, quota_hard_mb: 20480, bandwidth_limit_mb: null, database_limit: 5, email_account_limit: 10 }] }
    else if (path.endsWith('/ip-management')) data = { ips: [{ id: 1, address: '192.0.2.1', active: true, present_on_host: true, allocation_mode: 'shared' }] }
    else if (path === '/api/v1/task-operations/account-create') { requests.push(route.request().postDataJSON()); data = { id: 7, status: 'completed' } }
    else if (path.endsWith('/7/reveal')) data = { username: 'previewuser', initial_password: 'Fixture-only-43!NoRealAccount' } // pragma: allowlist secret — mocked response only, never a live credential
    await route.fulfill({ json: data })
  })
  await page.goto('/app/accounts/new')
  await expect(page.getByRole('heading', { name: 'Create account', exact: true })).toBeVisible()
  return requests
}

for (const theme of ['light', 'dark']) for (const width of [1440, 768, 375]) {
  test(`create account: ${theme} ${width}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 })
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await setup(page, theme)
    await page.getByLabel('Username', { exact: false }).fill('previewuser')
    await expect(page.getByText('11/16', { exact: true })).toBeVisible()
    await expect(page.getByText('Valid format', { exact: true })).toBeVisible()
    await page.getByLabel('Hosting plan').selectOption('1')
    await expect(page.getByText('20 GB', { exact: true })).toBeVisible()
    await expect(page.getByText('2 cores', { exact: true })).toBeVisible()
    await expect(page.locator('details')).not.toHaveAttribute('open', '')
    await expect(page.getByRole('button', { name: 'Create account', exact: true })).toBeInViewport()
    expect(await page.locator('.account-create-form').evaluate(e => e.getBoundingClientRect().width)).toBeLessThanOrEqual(900)
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false)
    expect(await page.locator('main').evaluate(e => e.scrollWidth > e.clientWidth)).toBe(false)
    await page.screenshot({ path: info.outputPath('after.png'), fullPage: true })
    await page.locator('main').evaluate(e => e.scrollTo(0, e.scrollHeight))
    await expect(page.getByRole('button', { name: 'Create account', exact: true })).toBeInViewport()
    expect(errors).toEqual([])
  })
}

test('password tools, username validation, advanced fields and unchanged create payload', async ({ page }) => {
  const requests = await setup(page)
  const username = page.getByLabel('Username', { exact: false })
  await username.fill('INVALID!')
  await expect(page.getByText('Check format', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Create account', exact: true })).toBeDisabled()
  await username.fill('previewuser')
  const password = page.getByLabel('Initial password')
  await page.getByRole('button', { name: 'Generate', exact: true }).click()
  const generated = await password.inputValue()
  expect(generated).toMatch(/^(?=.*[a-z])(?=.*[A-Z])(?=.*[0-9])(?=.*[^A-Za-z0-9]).{20}$/)
  await expect(password).toHaveAttribute('type', 'password')
  await page.getByRole('button', { name: 'Show password' }).click()
  await expect(password).toHaveAttribute('type', 'text')
  await page.getByRole('button', { name: 'Hide password' }).click()
  await expect(password).toHaveAttribute('type', 'password')
  await expect(page.getByText('Strong', { exact: true })).toBeVisible()
  await password.fill('')
  await page.getByText('Advanced options', { exact: false }).click()
  await page.getByLabel('IP assignment').selectOption('specific')
  await page.getByLabel('Server IP').selectOption('1')
  await page.getByRole('button', { name: 'Create account', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Account created' })).toBeVisible()
  expect(requests).toEqual([{ username: 'previewuser', ip_selection: 'specific', server_ip_id: 1 }])
})
