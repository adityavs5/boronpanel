import { test, expect } from '@playwright/test'

for (const skin of ['modern', 'classic', 'grid']) for (const theme of ['light', 'dark']) {
  test(`update tiers and wordmark ${skin} ${theme}`, async ({ page }) => {
    await page.addInitScript(({ skin, theme }) => {
      localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'admin', username: 'preview' }, version: 0 }))
      localStorage.setItem('boron.public-branding.v1',JSON.stringify({panel_name:'Boron',theme:skin}));localStorage.setItem('boron.ui', JSON.stringify({ state: { skin, theme }, version: 0 }))
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
      } else if ((path.endsWith('/update/status') || path.endsWith('/update/check'))) data = { configured: true, channel, release_channel: channel === 'development' ? 'beta' : channel, current_version: '3.2.1', latest_version: channel === 'stable' ? '3.2.1' : null, release_availability: channel === 'stable' ? null : 'unpublished', error: null, active_job: null }
      else if (path.endsWith('/update/history')) data = { jobs: [] }
      await route.fulfill({ json: data })
    })
    await page.goto('/app/updates')
    const selector = page.locator('#update-tier')
    await expect(selector).toHaveValue('stable')
    await selector.selectOption('beta')
    await expect(selector).toHaveValue('beta')
    // Development is a legacy tier: it is offered only to servers already on it.
    await expect(selector.locator('option[value="development"]')).toHaveCount(0)
    expect(changes).toEqual(['beta'])
    await page.reload()
    await expect(selector).toHaveValue('beta')
    await expect(page.getByText('No Beta release published yet.', { exact: false })).toBeVisible()
    await expect(page.getByText('Last check error:', { exact: false })).toHaveCount(0)
    await page.getByRole('button', { name: 'Check now', exact: true }).click()
    await expect(page.getByRole('button', { name: 'No release published yet', exact: true })).toBeDisabled()
    channel = 'development'
    await page.reload()
    await expect(selector).toHaveValue('development')
    await expect(selector.locator('option[value="development"]')).toHaveCount(1)
    await selector.selectOption('stable')
    await expect(selector).toHaveValue('stable')
    expect(changes).toEqual(['beta', 'stable'])
    await expect(selector.locator('option[value="development"]')).toHaveCount(0)
    const logo = page.locator('.boron-brand-logo img').first()
    await expect(logo).toHaveAttribute('src', skin === 'classic' || (skin === 'modern' && theme === 'dark') ? '/static/dist/brand/boron-logo-white.svg' : '/static/dist/brand/boron-logo-navy.svg')
    await expect(logo).toHaveCSS('filter', 'none')
    expect(await logo.evaluate(img => img.complete && img.naturalWidth > 0)).toBeTruthy()
    for (const width of [1440, 768, 375]) {
      await page.setViewportSize({ width, height: 900 })
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
      await page.screenshot({ path: `test-results/update-tiers-${skin}-${theme}-${width}.png` })
    }
  })
}
