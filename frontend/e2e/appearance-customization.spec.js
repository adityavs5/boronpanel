import { test, expect } from '@playwright/test'

for (const skin of ['evolution', 'paper-lantern']) for (const theme of ['light', 'dark']) {
  test(`appearance preferences ${skin} ${theme}`, async ({ page }) => {
    const errors = []
    page.on('pageerror', e => errors.push(e.message))
    await page.addInitScript(({ skin, theme }) => {
      if (!localStorage.getItem('boron.ui')) localStorage.setItem('boron.ui', JSON.stringify({ state: { skin, theme }, version: 0 }))
      localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'admin', username: 'preview' }, version: 0 }))
    }, { skin, theme })
    await page.route('**/api/**', route => {
      const path = new URL(route.request().url()).pathname
      return route.fulfill({ json: path.endsWith('/whoami') ? { role: 'admin', username: 'preview' } : path.endsWith('/branding') ? { panel_name: 'Boron' } : {} })
    })
    await page.goto('/app/appearance')
    await expect(page.getByLabel('accent colour', { exact: false })).toHaveValue(skin === 'evolution' ? '#bc942c' : '#307bb8')
    if (skin === 'evolution') {
      await page.goto('/app/accounts/new')
      const primary = page.getByRole('button', { name: 'Create account', exact: true })
      await expect(primary).toHaveCSS('background-color', theme === 'dark' ? 'rgb(188, 148, 44)' : 'rgb(177, 139, 41)')
      await expect(primary).toHaveCSS('color', 'rgb(0, 0, 0)')
      await page.screenshot({ path: `test-results/evo-gold-create-account-${theme}.png`, fullPage: true })
      await page.goto('/app/appearance')
    }
    for (const color of ['#ffffff', '#000000', '#bc942c', '#ffcc00']) {
      await page.locator('#accent-color').fill(color)
      const contrast = await page.evaluate(() => {
        const css = getComputedStyle(document.documentElement)
        const value = key => css.getPropertyValue('--pref-' + key).trim().split(' ').map(Number)
        return window.BoronAppearance.contrast(value('accent'), value('ink'))
      })
      expect(contrast).toBeGreaterThanOrEqual(4.5)
    }
    await page.getByLabel('Font', { exact: true }).selectOption('inter')
    await page.getByLabel('Text size', { exact: true }).selectOption('large')
    await page.getByLabel('Spacing', { exact: true }).selectOption('comfortable')
    await page.getByLabel('Motion', { exact: true }).selectOption('reduced')
    await page.reload()
    await expect(page.getByLabel('Font', { exact: true })).toHaveValue('inter')
    await expect(page.locator('html')).toHaveAttribute('data-density', 'comfortable')
    await expect(page.locator('html')).toHaveAttribute('data-motion', 'reduced')
    expect(await page.locator('.appearance-sample').evaluate(e => getComputedStyle(e).fontFamily)).toContain('Inter')
    for (const width of [1440, 768, 375]) {
      await page.setViewportSize({ width, height: 1000 })
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
      await page.screenshot({ path: `test-results/appearance-${skin}-${theme}-${width}.png`, fullPage: true })
    }
    await page.getByRole('button', { name: 'Reset appearance' }).click()
    await expect(page.getByLabel('Font', { exact: true })).toHaveValue('open-sans')
    await expect(page.getByLabel('accent colour', { exact: false })).toHaveValue(skin === 'evolution' ? '#bc942c' : '#307bb8')
    expect(errors).toEqual([])
  })
}

test('untrusted appearance values cannot inject CSS', async ({ page }) => {
  await page.goto('/app/login')
  const prefs = await page.evaluate(() => window.BoronAppearance.normalize({ colors: { evolution: 'url(https://invalid.test)' }, font: '__proto__', density: 'invalid' }))
  expect(prefs.colors).toEqual({})
  expect(prefs.font).toBe('open-sans')
  expect(prefs.density).toBe('default')
})
