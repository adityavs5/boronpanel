import { test, expect } from '@playwright/test'

const account = { username: 'example', status: 'active', primary_domain: 'a-very-long-primary-domain-name-for-a-hosting-customer.example.test', server_ip: '192.0.2.10', plan: 'A long hosting plan name', email: 'owner-with-a-long-address@example.test' }

// Wider than its space, a table must not hide a column behind the pinned actions.
for (const skin of ['modern', 'classic', 'grid']) {
  test(`${skin}: a table that does not fit keeps every column readable`, async ({ page }) => {
    await page.addInitScript(skin => {
      localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'admin', username: 'preview' }, version: 0 }))
      localStorage.setItem('boron.public-branding.v1', JSON.stringify({ panel_name: 'Boron', theme: skin }))
    }, skin)
    await page.route('**/api/**', route => {
      const path = new URL(route.request().url()).pathname
      let data = {}
      if (path.endsWith('/whoami')) data = { role: 'admin', username: 'preview' }
      else if (path.endsWith('/branding')) data = { panel_name: 'Boron' }
      else if (path === '/api/v1/accounts') data = [account]
      return route.fulfill({ json: data })
    })
    await page.setViewportSize({ width: 1900, height: 900 })
    await page.goto('/app/accounts')
    await page.getByRole('button', { name: 'Columns', exact: true }).click()
    for (const item of await page.getByRole('menuitemcheckbox').all()) {
      if (await item.getAttribute('aria-checked') !== 'true') await item.click()
    }
    await page.keyboard.press('Escape')

    const table = page.locator('.panel-data-table').first()
    // Modern caps this list at 1200px, so every optional column never fits there.
    const fitsWhenWide = skin !== 'modern'
    if (fitsWhenWide) await expect(table).not.toHaveAttribute('data-fit')

    await page.setViewportSize({ width: 680, height: 900 })
    await expect(table).toHaveAttribute('data-fit', /wrap|stack/)
    await expect.poll(() => table.evaluate(root => {
      const scroll = root.querySelector('.panel-table-scroll')
      return scroll.offsetParent ? scroll.scrollWidth - scroll.clientWidth : 0
    })).toBeLessThanOrEqual(1)
    if (await table.getAttribute('data-fit') === 'stack') await expect(table.locator('.table-card').first()).toContainText('192.0.2.10')

    await page.setViewportSize({ width: 1900, height: 900 })
    if (fitsWhenWide) {
      await expect(table).not.toHaveAttribute('data-fit')
      await expect(page.getByRole('columnheader', { name: 'Server IP', exact: true })).toBeVisible()
    }
  })
}
