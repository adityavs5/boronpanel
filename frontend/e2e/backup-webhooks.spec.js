import { test, expect } from '@playwright/test'
for (const skin of ['evolution', 'paper-lantern']) for (const theme of ['light', 'dark']) {
  test(`${skin} ${theme}: subscribe to backup failure notifications`, async ({ page }) => {
    await page.addInitScript(({ skin, theme }) => {
      localStorage.setItem('boron.ui', JSON.stringify({ state: { skin, theme }, version: 0 }))
      localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'admin', username: 'admin' }, version: 0 }))
    }, { skin, theme })
    let submitted
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname
      let data = {}
      if (path.endsWith('/whoami')) data = { role: 'admin', username: 'admin' }
      else if (path.endsWith('/onboarding')) data = { completed: true }
      else if (path.endsWith('/webhooks')) {
        if (route.request().method() === 'POST') {
          submitted = route.request().postDataJSON()
          data = { id: 1, ...submitted, secret: 'synthetic-test-only', enabled: true }
        } else data = { webhooks: [] }
      }
      await route.fulfill({ json: data })
    })
    await page.goto('/app/webhooks')
    await page.getByRole('button', { name: 'Add webhook', exact: true }).first().click()
    const dialog = page.getByRole('dialog')
    await expect(dialog.getByRole('button', { name: 'Add webhook', exact: true })).toBeDisabled()
    await expect(dialog.getByText('Select at least one event to deliver.', { exact: true })).toBeVisible()
    await dialog.getByRole('checkbox', { name: 'backup.failed', exact: true }).check()
    await dialog.locator('input[type=url]').fill('https://example.com/backups')
    await page.setViewportSize({ width: 390, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await dialog.getByRole('button', { name: 'Add webhook', exact: true }).click()
    await expect.poll(() => submitted?.events).toEqual(['backup.failed'])
  })
}
