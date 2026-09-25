import { test, expect } from '@playwright/test'

test('custom branding never flashes the default Boron logo during reload', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'customer', username: 'hostingdemo' }, version: 0 }))
    localStorage.setItem('boron.ui', JSON.stringify({ state: { skin: 'evolution', theme: 'light' }, version: 0 }))
  })

  let brandingCalls = 0
  let releaseFirst
  let releaseReload
  const firstGate = new Promise(resolve => { releaseFirst = resolve })
  const reloadGate = new Promise(resolve => { releaseReload = resolve })

  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    let data = {}
    if (path.endsWith('/whoami')) data = { role: 'customer', username: 'hostingdemo' }
    else if (path.endsWith('/version')) data = { version: '1.6.3' }
    else if (path.endsWith('/onboarding')) data = { completed: true }
    else if (path.endsWith('/usage')) data = { current: {}, resources: {} }
    else if (path.endsWith('/alerts')) data = { active: [] }
    else if (path.endsWith('/hostingdemo')) data = { username: 'hostingdemo', primary_domain: 'example.com' }
    await route.fulfill({ json: data })
  })
  // Playwright checks routes in reverse registration order. Keep these more
  // specific branding handlers after the generic API fixture.
  await page.route('**/api/v1/branding/logo', route => route.fulfill({
    contentType: 'image/svg+xml',
    body: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 32"><rect width="120" height="32" fill="#16a3d8"/></svg>',
  }))
  await page.route('**/api/v1/branding', async route => {
    brandingCalls += 1
    await (brandingCalls === 1 ? firstGate : reloadGate)
    await route.fulfill({ json: { panel_name: 'SiteCountry', logo_url: '/api/v1/branding/logo', favicon_url: null } })
  })

  await page.goto('/app/dashboard')
  await expect(page.locator('.boron-brand-logo')).toHaveCount(0)
  await expect(page.locator('.brand-loading-placeholder')).toBeVisible()
  releaseFirst()
  await expect(page.locator('.custom-brand-logo')).toHaveAttribute('alt', 'SiteCountry')
  await expect(page.locator('.boron-brand-logo')).toHaveCount(0)

  await page.reload({ waitUntil: 'domcontentloaded' })
  await expect(page.locator('.custom-brand-logo')).toHaveAttribute('alt', 'SiteCountry')
  await expect(page.locator('.boron-brand-logo')).toHaveCount(0)
  releaseReload()
})
