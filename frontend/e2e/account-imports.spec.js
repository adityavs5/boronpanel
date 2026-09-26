import { expect, test } from '@playwright/test'

for (const skin of ['evolution', 'paper-lantern']) {
  test(`${skin}: cPanel, DirectAdmin and Boron account migrations`, async ({ page }, info) => {
    await page.addInitScript(skinName => {
      localStorage.setItem('boron.ui', JSON.stringify({ state: { skin: skinName, theme: 'light' }, version: 0 }))
      localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'admin', username: 'admin' }, version: 0 }))
    }, skin)
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname
      let data = {}
      if (path.endsWith('/whoami')) data = { role: 'admin', username: 'admin' }
      else if (path.endsWith('/branding')) data = { panel_name: 'Boron' }
      else if (path.endsWith('/version')) data = { version: '1.2.1' }
      else if (path.endsWith('/onboarding')) data = { completed: true }
      else if (path === '/api/v1/admin/import/accounts/7') data = { id: 7, username: 'migrated1', panel: 'directadmin', source: 'upload', status: 'completed', progress_message: 'completed', initial_password: 'Test-only-Password-42!', results: [{ item: 'files', status: 'ok', detail: 'copied website files' }], started_at: '2026-09-14T10:00:00Z' }
      else if (path === '/api/v1/admin/import/accounts') data = { jobs: [{ id: 7, username: 'migrated1', panel: 'directadmin', source: 'upload', status: 'completed', progress_message: 'completed', results: [{ item: 'files', status: 'ok', detail: 'copied website files' }], started_at: '2026-09-14T10:00:00Z' }] }
      else if (path === '/api/v1/admin/import/boron') data = { jobs: [{ id: 4, username: 'portable1', status: 'completed', progress_message: 'restored', archive_version: 1, components_verified: 4, started_at: '2026-09-14T09:00:00Z' }] }
      else data = { jobs: [], active: [], disks: [] }
      await route.fulfill({ json: data })
    })

    await page.goto('/app/import/accounts')
    await expect(page.getByRole('heading', { name: 'Account migrations', exact: true })).toBeVisible()
    await expect(page.getByRole('table').getByText('DirectAdmin', { exact: true })).toBeVisible()
    await expect(page.getByRole('table').getByText('Boron archive', { exact: true })).toBeVisible()
    await page.getByRole('table').getByText('migrated1', { exact: true }).click()
    await expect(page.getByText('New account password', { exact: true })).toBeVisible()
    await expect(page.getByText('Test-only-Password-42!', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Close' }).first().click()

    await page.getByRole('button', { name: 'New migration' }).first().click()
    await expect(page.locator('#migration-panel option')).toHaveCount(3)
    await page.locator('#migration-panel').selectOption('boron')
    await expect(page.getByText('Must match the username stored in the Boron archive.')).toBeVisible()
    await expect(page.locator('#migration-source')).toHaveCount(0)
    await page.screenshot({ path: info.outputPath(`${skin}-account-migrations.png`), fullPage: true })

    await page.setViewportSize({ width: 390, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })
}
