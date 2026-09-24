import { test, expect } from '@playwright/test'

test('analysis mode clearly describes source backup and sends no-restore flag', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'admin', username: 'admin' }, version: 0 })))
  let submitted
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    let data = {}
    if (path.endsWith('/whoami')) data = { role: 'admin', username: 'admin' }
    else if (path.endsWith('/branding')) data = { panel_name: 'Boron' }
    else if (path.endsWith('/onboarding')) data = { completed: true }
    else if (path.endsWith('/directadmin/inspect')) data = { accounts: [{ username: 'alice', available: true }], destination_database: 'MariaDB', notes: [] }
    else if (path.endsWith('/directadmin/migrate')) {
      submitted = route.request().postDataJSON()
      data = { id: 10, username: 'alice', status: 'pending' }
    } else if (path.endsWith('/import/accounts/10')) data = { id: 10, username: 'alice', status: 'completed', progress_message: 'compatibility analysis completed — no destination account created', results: [{ item: 'preflight', status: 'ok', detail: 'Verified inventory' }] }
    else if (path.includes('/import/')) data = { jobs: [] }
    await route.fulfill({ json: data })
  })
  await page.goto('/app/import/accounts')
  await page.getByRole('button', { name: 'From DirectAdmin server' }).click()
  await page.getByLabel('Source server', { exact: true }).fill('source.example.com')
  await page.getByLabel('Admin password or login key').fill('test-only')
  await page.getByRole('button', { name: 'Connect and list accounts' }).click()
  await page.getByRole('checkbox', { name: 'alice', exact: true }).check()
  await page.getByRole('checkbox', { name: /Analyze compatibility only/ }).check()
  await page.getByRole('button', { name: 'Analyze 1 selected account' }).click()
  await expect(page.getByRole('dialog').getByText(/compatibility analysis completed/)).toBeVisible()
  expect(submitted.preflight_only).toBe(true)
  expect(submitted.db_compatibility).toBe('strict')
})
