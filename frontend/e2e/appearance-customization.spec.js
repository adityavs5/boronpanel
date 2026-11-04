import { test, expect } from '@playwright/test'

for (const theme of ['classic', 'grid', 'modern']) {
  test(`display preferences persist in ${theme}`, async ({ page }) => {
    const errors = []
    page.on('pageerror', (error) => errors.push(error.message))
    await page.addInitScript((value) => {
      localStorage.setItem('boron.public-branding.v1', JSON.stringify({ panel_name: 'Boron', theme: value }))
      localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'customer', username: 'preview' }, version: 0 }))
    }, theme)
    await page.route('**/api/**', (route) => {
      const path = new URL(route.request().url()).pathname
      return route.fulfill({ json: path.endsWith('/whoami') ? { role: 'customer', username: 'preview' } : path.endsWith('/branding') ? { panel_name: 'Boron' } : {} })
    })
    await page.goto('/app/appearance')
    await page.getByLabel('Text size', { exact: true }).selectOption('large')
    await page.getByLabel('Spacing', { exact: true }).selectOption('comfortable')
    await page.getByLabel('Motion', { exact: true }).selectOption('reduced')
    await page.reload()
    await expect(page.locator('html')).toHaveAttribute('data-text-size', 'large')
    await expect(page.locator('html')).toHaveAttribute('data-density', 'comfortable')
    await expect(page.locator('html')).toHaveAttribute('data-motion', 'reduced')
    for (const width of [1440, 768, 375]) {
      await page.setViewportSize({ width, height: 1000 })
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
    }
    await page.getByRole('button', { name: 'Reset preferences' }).click()
    await expect(page.getByLabel('Text size', { exact: true })).toHaveValue('default')
    await expect(page.locator('html')).toHaveAttribute('data-density', 'default')
    expect(errors).toEqual([])
  })

  test(`${theme} keeps its accent readable`, async ({ page }) => {
    await page.addInitScript((value) => localStorage.setItem('boron.public-branding.v1', JSON.stringify({ panel_name: 'Boron', theme: value })), theme)
    await page.route('**/api/**', (route) => route.fulfill({ status: 401, json: {} }))
    await page.goto('/app/login')
    const contrast = await page.evaluate(() => {
      const css = getComputedStyle(document.documentElement), value = (name) => css.getPropertyValue(`--pref-${name}`).trim().split(' ').map(Number)
      return { button: window.BoronAppearance.contrast(value('accent'), value('ink')), link: window.BoronAppearance.contrast(value('link'), [255, 255, 255]) }
    })
    expect(contrast.button).toBeGreaterThanOrEqual(4.5)
    expect(contrast.link).toBeGreaterThanOrEqual(4.5)
  })
}

test('stored accent, font and invalid values are ignored', async ({ page }) => {
  await page.route('**/api/**', (route) => route.fulfill({ status: 401, json: {} }))
  await page.goto('/app/login')
  const prefs = await page.evaluate(() => window.BoronAppearance.normalize({ colors: { modern: 'url(https://invalid.test)' }, font: '__proto__', density: 'invalid' }))
  expect(prefs).toEqual({ textSize: 'default', density: 'default', motion: 'system' })
})
