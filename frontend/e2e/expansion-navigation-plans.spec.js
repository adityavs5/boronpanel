import { test, expect } from '@playwright/test'

async function session(page, role, skin) {
  await page.addInitScript(({ role, skin }) => {
    localStorage.setItem('boron.ui', JSON.stringify({ state: { skin, theme: 'light' }, version: 0 }))
    localStorage.setItem('boron.auth', JSON.stringify({ state: { role, username: role === 'admin' ? 'admin' : 'hostingdemo' }, version: 0 }))
  }, { role, skin })
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    let data = {}
    if (path.endsWith('/whoami')) data = { role, username: role === 'admin' ? 'admin' : 'hostingdemo' }
    else if (path.endsWith('/branding')) data = { panel_name: 'Boron' }
    else if (path.endsWith('/version')) data = { version: '1.2.1' }
    else if (path.endsWith('/onboarding')) data = { completed: true }
    else if (path === '/api/v1/accounts') data = []
    else if (path === '/api/v1/accounts/hostingdemo') data = { username: 'hostingdemo', primary_domain: 'example.test', php_version: '8.3', status: 'active', quota_hard_mb: 6144 }
    else if (path.endsWith('/accounts/hostingdemo/domains')) data = { domains: [{ id: 1, domain: 'example.test', kind: 'primary' }, { id: 2, domain: 'blog.example.test', kind: 'subdomain' }] }
    else if (path.includes('/dns/zones/')) data = { zone: 'example.test', managed: true, records: [{ name: 'example.test.', type: 'MX', ttl: 3600, values: ['10 mail.example.test.'] }, { name: '_dmarc.example.test.', type: 'TXT', ttl: 3600, values: ['"v=DMARC1; p=none"'] }, { name: 'www.example.test.', type: 'A', ttl: 3600, values: ['192.0.2.1'] }] }
    else if (path.endsWith('/alerts')) data = { active: [] }
    else if (path.endsWith('/usage')) data = { current: {}, quota_hard_mb: 6144 }
    else if (path.endsWith('/health')) data = { cpu_pct: 1, mem_pct: 2, disks: [] }
    else if (path.endsWith('/admin/plans')) data = { plans: [] }
    else if (path.endsWith('/parked-domains')) data = { parked_domains: [] }
    else data = { mailboxes: [], forwarders: [], entries: [], jobs: [] }
    await route.fulfill({ json: data })
  })
}

const expectedCustomerOrder = [
  '/app/domains', '/app/subdomains', '/app/ftp', '/app/ssl', '/app/databases', '/app/dns',
  '/app/email', '/app/email/settings', '/app/email/dns', '/app/wordpress', '/app/backups',
  '/app/node-apps', '/app/python-apps', '/app/terminal', '/app/redis',
]

for (const skin of ['evolution', 'paper-lantern']) {
  test(`${skin}: requested customer menu order and direct sections`, async ({ page }) => {
    await session(page, 'customer', skin)
    await page.goto('/app/dashboard')
    await expect(page.locator('.tool-link').first()).toBeVisible()
    const links = await page.locator('.tool-link').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('href')))
    expect(links.slice(0, expectedCustomerOrder.length)).toEqual(expectedCustomerOrder)

    await page.getByRole('textbox', { name: 'Search hosting tools' }).fill('mail deliverability')
    await expect(page.getByRole('option').filter({ hasText: 'Email DNS Records' })).toBeVisible()

    await page.goto('/app/subdomains')
    await expect(page.getByRole('heading', { name: 'Subdomains', exact: true })).toBeVisible()
    await expect(page.getByRole('table').getByText('blog.example.test', { exact: true })).toBeVisible()
    await expect(page.getByRole('table').getByText('example.test', { exact: true })).toHaveCount(0)

    await page.goto('/app/domains/example.test')
    await expect(page.getByRole('heading', { name: 'example.test', exact: true })).toBeVisible()
    await expect(page.getByRole('tab')).toHaveCount(0)
    await expect(page.getByRole('heading', { name: 'Advanced settings', exact: true })).toBeVisible()

    await page.goto('/app/redirects')
    await expect(page.getByRole('heading', { name: 'Site Redirection', exact: true })).toBeVisible()
    await expect(page.getByRole('combobox', { name: 'Choose domain' })).toHaveValue('example.test')

    await page.goto('/app/email/settings')
    await expect(page.getByRole('tab', { name: 'Forwarders' })).toHaveAttribute('data-state', 'active')

    await page.goto('/app/email/dns')
    await expect(page.getByRole('heading', { name: 'Email DNS Records', exact: true })).toBeVisible()
    await expect(page.getByRole('table').getByText('MX', { exact: true })).toBeVisible()
    await expect(page.getByRole('table').getByText('A', { exact: true })).toHaveCount(0)
  })
}

test('plan creation offers four editable templates and Custom', async ({ page }) => {
  await session(page, 'admin', 'evolution')
  await page.goto('/app/plans')
  await page.getByRole('button', { name: 'New plan' }).first().click()
  const template = page.getByLabel('Starting template')
  await expect(template.locator('option')).toHaveCount(5)
  await expect(template).toHaveValue('wordpress')
  await expect(page.getByLabel('Memory (MB)')).toHaveValue('1024')
  await expect(page.getByText('Accounts on this plan get per-account Redis enabled.')).toBeVisible()

  await template.selectOption('starter')
  await expect(page.getByLabel('Plan name')).toHaveValue('Starter')
  await expect(page.getByLabel('Disk hard quota (MB)')).toHaveValue('3072')
  await template.selectOption('custom')
  await expect(page.getByLabel('Disk hard quota (MB)')).toHaveValue('3072')

  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})
