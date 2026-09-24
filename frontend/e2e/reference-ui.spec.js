import { test, expect } from '@playwright/test'

// Visual and behavioral acceptance for the supplied DA/cPanel references.
for (const skin of ['evolution', 'paper-lantern']) for (const mode of ['light', 'dark']) {
  test(`${skin} ${mode}: reference interiors and inline database creation`, async ({ page }, info) => {
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.addInitScript(({ skin, mode }) => {
      localStorage.setItem('boron.ui', JSON.stringify({ state: { skin, theme: mode }, version: 0 }))
      localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'customer', username: 'hostingdemo' }, version: 0 }))
    }, { skin, mode })
    let created = false
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname
      let data = {}
      if (path.endsWith('/whoami')) data = { role: 'customer', username: 'hostingdemo' }
      else if (path.endsWith('/branding')) data = { panel_name: 'Boron' }
      else if (path.endsWith('/onboarding')) data = { completed: true }
      else if (path.endsWith('/domains')) data = { domains: [{ domain: 'example.com', is_primary: true, docroot: '/home/hostingdemo/public_html', status: 'active' }] }
      else if (path.endsWith('/databases')) {
        if (route.request().method() === 'POST') {
          expect(route.request().postDataJSON()).toEqual({ name: 'shop' })
          created = true
          data = { db_name: 'hostingdemo_shop', db_user: 'hostingdemo_shop', password: 'test-only-password' }
        } else data = { databases: [{ db_name: 'hostingdemo_wordpress', db_user: 'hostingdemo_wordpress' }, ...(created ? [{ db_name: 'hostingdemo_shop', db_user: 'hostingdemo_shop' }] : [])] }
      } else if (path.endsWith('/ssl')) data = { certbot_timer_active: true, domains: [
        { domain: 'example.com', cert_status: 'active', issuer: 'Lets Encrypt', expiry_date: '2026-12-01', days_remaining: 68 },
        { domain: 'shop.example.com', cert_status: 'missing' },
      ] }
      else if (path.includes('/malware/')) data = { scans: [], findings: [] }
      await route.fulfill({ json: data })
    })
    await page.goto('/app/databases')
    await expect(page.getByRole('heading', { name: 'Create New Database', exact: true })).toBeVisible()
    await page.getByRole('textbox', { name: 'New database', exact: false }).fill('shop')
    await page.getByRole('button', { name: 'Create database', exact: true }).click()
    await expect(page.getByRole('dialog').getByText('test-only-password', { exact: true })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('table').getByRole('button', { name: 'Manage database hostingdemo_shop' })).toBeVisible()
    await page.screenshot({ path: info.outputPath('databases.png') })
    for (const path of ['domains', 'ssl', 'malware']) {
      await page.goto(`/app/${path}`)
      await expect(page.locator('main h1')).toBeVisible()
      if (path === 'ssl') {
        await expect(page.getByRole('heading', { name: 'Issue a new certificate' })).toBeVisible()
        await expect(page.getByRole('table')).toHaveCount(2)
      }
      await page.screenshot({ path: info.outputPath(`${path}.png`) })
      await page.setViewportSize({ width: 390, height: 844 })
      expect(await page.evaluate(() => document.querySelector('main').scrollWidth <= document.querySelector('main').clientWidth)).toBe(true)
      await page.setViewportSize({ width: 1440, height: 1000 })
    }
    expect(errors).toEqual([])
  })
}
