import { test, expect } from '@playwright/test'

for (const skin of ['evolution', 'paper-lantern']) for (const theme of ['light', 'dark']) {
  test(`appearance does not echo across tabs: ${skin} ${theme}`, async ({ context, page }) => {
    await context.addInitScript(({ skin, theme }) => {
      if (!localStorage.getItem('boron.ui')) localStorage.setItem('boron.ui', JSON.stringify({ state: { skin, theme }, version: 0 }))
      localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'admin', username: 'preview' }, version: 0 }))
    }, { skin, theme })
    await context.route('**/api/**', route => {
      const path = new URL(route.request().url()).pathname
      return route.fulfill({ json: path.endsWith('/whoami') ? { role: 'admin', username: 'preview' } : path.endsWith('/branding') ? { panel_name: 'Boron' } : {} })
    })
    const other = await context.newPage()
    await page.goto('/app/appearance')
    await other.goto('/app/appearance')
    for (const tab of [page, other]) {
      await expect(tab.locator('#accent-color')).toBeVisible()
      await tab.evaluate(() => {
        window.preferenceWrites = 0
        const original = Storage.prototype.setItem
        Storage.prototype.setItem = function (key, value) {
          if (key === 'boron.ui') window.preferenceWrites++
          return original.call(this, key, value)
        }
      })
    }
    await page.getByRole('button', { name: 'Blue', exact: true }).click()
    await expect(other.locator('#accent-color')).toHaveValue('#076fd5')
    expect(await other.evaluate(() => window.preferenceWrites)).toBe(0)
    // A delayed old event must not undo the latest value in shared storage.
    await other.evaluate(() => window.dispatchEvent(new StorageEvent('storage', {
      key: 'boron.ui', storageArea: localStorage,
      newValue: JSON.stringify({ state: { appearance: { colors: { evolution: '#bc942c', 'paper-lantern': '#bc942c' } } } }),
    })))
    await expect(other.locator('#accent-color')).toHaveValue('#076fd5')
    expect(await other.evaluate(() => window.preferenceWrites)).toBe(0)
    await other.getByRole('button', { name: 'Gold', exact: true }).click()
    await expect(page.locator('#accent-color')).toHaveValue('#bc942c')
    await page.waitForTimeout(500)
    for (const tab of [page, other]) expect(await tab.evaluate(() => window.preferenceWrites)).toBe(1)
    await page.reload()
    await expect(page.locator('#accent-color')).toHaveValue('#bc942c')
  })
}
