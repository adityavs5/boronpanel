import { test, expect } from '@playwright/test'

test('failed migration can be selected again after reconnecting', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'admin', username: 'admin' }, version: 0 })))
  let submissions = 0
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    let data = {}
    if (path.endsWith('/whoami')) data = { role: 'admin', username: 'admin' }
    else if (path.endsWith('/branding')) data = { panel_name: 'Boron' }
    else if (path.endsWith('/onboarding')) data = { completed: true }
    else if (path.endsWith('/directadmin/inspect')) data = { accounts: [{ username: 'adzwerco', available: true }], destination_database: 'MariaDB', notes: [] }
    else if (path.endsWith('/directadmin/migrate')) data = { id: ++submissions, username: 'adzwerco', status: 'pending' }
    else if (path.includes('/import/accounts')) data = { jobs: [] }
    await route.fulfill({ json: data })
  })
  await page.goto('/app/import/accounts')
  for (let attempt = 0; attempt < 2; attempt++) {
    await page.getByRole('button', { name: 'From DirectAdmin server' }).click()
    await page.getByLabel('Source server', { exact: true }).fill('source.example.com')
    await page.getByLabel('Admin password or login key').fill('test-only')
    await page.getByRole('button', { name: 'Connect and list accounts' }).click()
    await page.getByRole('checkbox', { name: 'adzwerco' }).check()
    await page.getByRole('button', { name: 'Import 1 selected account', exact: true }).click()
    await expect(page.getByRole('dialog')).toBeVisible()
    await expect(page.locator('input[type=password]')).toHaveCount(0)
    await page.getByRole('dialog').getByRole('button', { name: 'Close', exact: true }).last().click()
  }
  expect(submissions).toBe(2)
})
