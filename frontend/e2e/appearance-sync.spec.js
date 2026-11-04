import { test, expect } from '@playwright/test'

test('display preferences reach other tabs without echoing back', async ({ context, page }) => {
  await context.addInitScript(() => localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'admin', username: 'preview' }, version: 0 })))
  await context.route('**/api/**', (route) => {
    const path = new URL(route.request().url()).pathname
    return route.fulfill({ json: path.endsWith('/whoami') ? { role: 'admin', username: 'preview' } : path.endsWith('/branding') ? { panel_name: 'Boron' } : {} })
  })
  const other = await context.newPage()
  await page.goto('/app/appearance')
  await other.goto('/app/appearance')
  for (const tab of [page, other]) {
    await expect(tab.getByLabel('Text size', { exact: true })).toBeVisible()
    await tab.evaluate(() => {
      window.preferenceWrites = 0
      const original = Storage.prototype.setItem
      Storage.prototype.setItem = function (key, value) {
        if (key === 'boron.ui') window.preferenceWrites++
        return original.call(this, key, value)
      }
    })
  }
  await page.getByLabel('Text size', { exact: true }).selectOption('large')
  await expect(other.getByLabel('Text size', { exact: true })).toHaveValue('large')
  expect(await other.evaluate(() => window.preferenceWrites)).toBe(0)
  // A delayed old event must not undo the latest value in shared storage.
  await other.evaluate(() => window.dispatchEvent(new StorageEvent('storage', {
    key: 'boron.ui', storageArea: localStorage,
    newValue: JSON.stringify({ state: { appearance: { textSize: 'default' } } }),
  })))
  await expect(other.getByLabel('Text size', { exact: true })).toHaveValue('large')
  expect(await other.evaluate(() => window.preferenceWrites)).toBe(0)
  await other.getByLabel('Spacing', { exact: true }).selectOption('compact')
  await expect(page.getByLabel('Spacing', { exact: true })).toHaveValue('compact')
  await page.waitForTimeout(500)
  for (const tab of [page, other]) expect(await tab.evaluate(() => window.preferenceWrites)).toBe(1)
})
