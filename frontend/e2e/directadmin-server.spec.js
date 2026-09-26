import { test, expect } from '@playwright/test'
for (const mode of ['admin', 'root']) test(`DirectAdmin ${mode}: connect, select and queue a server import`, async ({ page }) => {
  let migration
  await page.addInitScript(() => localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'admin', username: 'admin' }, version: 0 })))
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    let data = {}
    if (path.endsWith('/whoami')) data = { role: 'admin', username: 'admin' }
    else if (path.endsWith('/directadmin/inspect')) {
      expect(route.request().postDataJSON().mode).toBe(mode)
      data = { accounts: [{ username: 'customer', available: true }], destination_database: '10.11-MariaDB', notes: ['Source accounts remain active.'] }
    } else if (path.endsWith('/directadmin/migrate')) {
      migration = route.request().postDataJSON()
      data = { id: 22, username: 'customer', status: 'pending' }
    } else if (path.includes('/import/')) data = { jobs: [] }
    else if (path === '/api/v1/accounts') data = []
    await route.fulfill({ json: data })
  })
  await page.goto('/app/import/accounts')
  await page.getByRole('button', { name: 'From DirectAdmin server' }).click()
  await page.getByLabel('Connection method').selectOption(mode)
  await page.getByLabel('Source server', { exact: true }).fill('source.example.com')
  await page.getByLabel(mode === 'root' ? 'Root SSH password' : 'Admin password or login key', { exact: true }).fill('test-only-source-password')
  if (mode === 'root') await page.getByLabel('SSH host-key fingerprint', { exact: true }).fill('SHA256:' + 'a'.repeat(43))
  await page.getByRole('button', { name: 'Connect and list accounts' }).click()
  await page.getByRole('checkbox', { name: 'customer', exact: true }).check()
  await page.getByRole('button', { name: 'Import 1 selected account', exact: true }).click()
  const progress = page.getByRole('dialog', { name: 'Migration #22 · customer' })
  await expect(progress).toBeVisible()
  await expect(progress.getByRole('status')).toContainText('Waiting for the worker')
  expect(migration.remote_user).toBe('customer')
  expect(migration.username).toBe('customer')
  expect(migration.db_compatibility).toBe('strict')
  expect(migration.remote.port).toBe(mode === 'root' ? 22 : 2222)
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain('test-only-source-password')
  expect(await page.locator('input').evaluateAll(inputs => inputs.map(input => input.value))).not.toContain('test-only-source-password')
})
