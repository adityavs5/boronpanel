import { test, expect } from '@playwright/test'

for (const skin of ['evolution', 'paper-lantern']) {
  test(`${skin}: migration updates, recovers from errors, and keeps one-time credentials as text`, async ({ page }) => {
    await page.addInitScript(skin => {
      localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'admin', username: 'admin' }, version: 0 }))
      localStorage.setItem('boron.ui', JSON.stringify({ state: { skin, theme: 'light' }, version: 0 }))
    }, skin)
    let reads = 0
    const job = { id: 7, username: 'migrateqa', panel: 'directadmin', source: 'directadmin_remote', status: 'running', progress_message: 'creating source backup', results: [], started_at: new Date().toISOString() }
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname
      let data = {}
      if (path.endsWith('/whoami')) data = { role: 'admin', username: 'admin' }
      else if (path.endsWith('/branding')) data = { panel_name: 'Boron' }
      else if (path.endsWith('/onboarding')) data = { completed: true }
      else if (path.endsWith('/import/accounts/7')) {
        reads++
        if (reads <= 4) return route.fulfill({ status: 502, json: { detail: 'temporary test connection failure' } })
        data = { ...job, status: 'completed', progress_message: 'completed', results: [{ item: 'files', status: 'ok', detail: 'copied' }], ...(reads === 5 ? { initial_password: 'one-time-test-secret' } : {}) }
      } else if (path.endsWith('/import/accounts')) data = { jobs: [job] }
      else if (path.endsWith('/import/boron')) data = { jobs: [] }
      await route.fulfill({ json: data })
    })
    await page.goto('/app/import/accounts')
    await page.getByRole('table').getByText('migrateqa', { exact: true }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog.getByRole('region', { name: 'Migration progress' })).toBeVisible()
    await expect(dialog.getByText('creating source backup', { exact: true })).toBeVisible()
    await expect(dialog.getByText(/Could not refresh progress/)).toBeVisible({ timeout: 20000 })
    await expect(dialog.getByText('one-time-test-secret', { exact: true })).toBeVisible({ timeout: 20000 })
    await expect(dialog.getByText('1 items processed')).toBeVisible()
    await dialog.getByRole('button', { name: 'Refresh progress' }).click()
    await expect.poll(() => reads).toBeGreaterThan(5)
    await expect(dialog.getByText('one-time-test-secret', { exact: true })).toBeVisible()
    await expect(dialog.locator('input')).toHaveCount(0)
    await page.setViewportSize({ width: 390, height: 844 })
    expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
  })
}
