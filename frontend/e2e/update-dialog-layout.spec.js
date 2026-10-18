import { test, expect } from '@playwright/test'

test.use({ ignoreHTTPSErrors: true })
for (const skin of ['evolution', 'paper-lantern']) for (const theme of ['light', 'dark']) {
  test(`update confirmation remains usable ${skin} ${theme}`, async ({ page }) => {
    await page.addInitScript(({ skin, theme }) => {
      localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'admin', username: 'preview' }, version: 0 }))
      localStorage.setItem('boron.ui', JSON.stringify({ state: { skin, theme }, version: 0 }))
    }, { skin, theme })
    const metadata = { data_plane_reloads: ['lshttpd.service', 'dovecot.service', 'postfix.service'] }
    let mutations = 0
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname
      let data = {}
      if (path.endsWith('/whoami')) data = { role: 'admin', username: 'preview' }
      else if (path.endsWith('/branding')) data = { panel_name: 'Boron' }
      else if (path.endsWith('/update/status')) data = { configured: true, channel: 'beta', current_version: '3.2.0', latest_version: '3.2.2', update_available: true, release_metadata: metadata, active_job: null }
      else if (path.endsWith('/update/history')) data = { jobs: [] }
      else if (path.endsWith('/update/preflight')) data = { release_metadata: metadata, critical_failures: [], checks: Array.from({ length: 24 }, (_, i) => ({ id: `diagnostic_service_${i}`, status: 'pass', detail: 'The configured service is active. The authenticated request reached the privileged helper and its worker pool.' })) }
      if (route.request().method() !== 'GET') mutations++
      await route.fulfill({ json: data })
    })
    await page.goto(process.env.BORON_UI_CHECK_URL || '/app/updates')
    for (const width of [1440, 768, 375]) {
      await page.setViewportSize({ width, height: 900 })
      await page.getByRole('button', { name: 'Update to v3.2.2', exact: true }).click()
      const dialog = page.getByRole('dialog')
      await expect(dialog).toBeVisible()
      const submit = dialog.getByRole('button', { name: 'Start update', exact: true })
      await expect(submit).toBeDisabled()
      const body = dialog.locator('.panel-dialog-body')
      await expect(body.getByText('diagnostic service 23', { exact: true })).toBeAttached()
      const geometry = await dialog.evaluate(el => {
        const rect = el.getBoundingClientRect()
        const body = el.querySelector('.panel-dialog-body')
        const footer = el.querySelector('.panel-dialog-footer').getBoundingClientRect()
        return { top: rect.top, bottom: rect.bottom, width: rect.width, footerBottom: footer.bottom, footerTop: footer.top, scrolls: body.scrollHeight > body.clientHeight, height: innerHeight, viewportWidth: innerWidth }
      })
      expect(geometry.top).toBeGreaterThanOrEqual(0)
      expect(geometry.bottom).toBeLessThanOrEqual(geometry.height)
      expect(geometry.footerBottom).toBeLessThanOrEqual(geometry.height)
      expect(geometry.footerTop).toBeGreaterThan(geometry.top)
      expect(geometry.width).toBeLessThanOrEqual(geometry.viewportWidth - 16)
      if (width >= 768) expect(geometry.width).toBeGreaterThan(600)
      expect(geometry.scrolls).toBeTruthy()
      await body.evaluate(el => { el.scrollTop = el.scrollHeight })
      await dialog.getByRole('checkbox').check()
      await expect(submit).toBeEnabled()
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
      await page.screenshot({ path: `test-results/update-dialog-${skin}-${theme}-${width}.png` })
      await dialog.getByRole('checkbox').uncheck()
      await dialog.getByRole('button', { name: 'Cancel', exact: true }).click()
      await expect(dialog).toHaveCount(0)
    }
    expect(mutations).toBe(0)
  })
}
