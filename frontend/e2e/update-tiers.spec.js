import { test, expect } from '@playwright/test'

for (const skin of ['evolution', 'paper-lantern']) for (const theme of ['light', 'dark']) {
  test(`update tiers and gold wordmark ${skin} ${theme}`, async ({ page }) => {
    await page.addInitScript(({ skin, theme }) => {
      localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'admin', username: 'preview' }, version: 0 }))
      localStorage.setItem('boron.ui', JSON.stringify({ state: { skin, theme }, version: 0 }))
    }, { skin, theme })
    let channel = 'stable'
    const changes = []
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname
      let data = {}
      if (path.endsWith('/whoami')) data = { role: 'admin', username: 'preview' }
      else if (path.endsWith('/branding')) data = { panel_name: 'Boron' }
      else if (path.endsWith('/update/channel')) {
        channel = route.request().postDataJSON().channel
        changes.push(channel)
        data = { channel }
      } else if (path.endsWith('/update/status')) data = { configured: true, channel, current_version: '3.2.1', latest_version: '3.2.1', active_job: null }
      else if (path.endsWith('/update/history')) data = { jobs: [] }
      await route.fulfill({ json: data })
    })
    await page.goto('/app/updates')
    const selector = page.locator('#update-tier')
    await expect(selector).toHaveValue('stable')
    await selector.selectOption('beta')
    await expect(selector).toHaveValue('beta')
    await selector.selectOption('development')
    await expect(selector).toHaveValue('development')
    expect(changes).toEqual(['beta', 'development'])
    const logo = page.locator('.boron-brand-logo img').first()
    await expect(logo).toHaveAttribute('src', '/static/dist/brand/boron-logo-gold.svg')
    await expect(logo).toHaveCSS('filter', 'none')
    expect(await logo.evaluate(img => img.complete && img.naturalWidth > 0)).toBeTruthy()
    for (const width of [1440, 768, 375]) {
      await page.setViewportSize({ width, height: 900 })
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
      await page.screenshot({ path: `test-results/update-tiers-${skin}-${theme}-${width}.png` })
    }
  })
}
