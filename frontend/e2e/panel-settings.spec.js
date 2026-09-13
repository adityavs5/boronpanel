import { test, expect } from '@playwright/test'
for (const skin of ['evolution', 'paper-lantern']) for (const mode of ['light', 'dark']) {
  test(`${skin} ${mode}: panel port previews, queue and failure recovery`, async ({ page }, info) => {
    await page.addInitScript(({ skin, mode }) => {
      localStorage.setItem('boron.ui', JSON.stringify({ state: { skin, theme: mode }, version: 0 }))
      localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'admin', username: 'admin' }, version: 0 }))
    }, { skin, mode })
    let job = null, submitted
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname
      let data = {}
      if (path.endsWith('/whoami')) data = { role: 'admin', username: 'admin' }
      else if (path.endsWith('/onboarding')) data = { completed: true }
      else if (path === '/api/v1/admin/panel-config/ports') {
        submitted = route.request().postDataJSON()
        job = { id: 1, ...submitted, status: 'pending', initiated_by: 'admin', created_at: new Date().toISOString() }
        data = job
      } else if (path === '/api/v1/admin/panel-config') data = { admin_port: 9443, customer_port: 9443, jobs: job ? [job] : [] }
      await route.fulfill({ json: data })
    })
    await page.goto('/app/panel-settings')
    const button = page.getByRole('button', { name: 'Apply panel ports' })
    await expect(button).toBeDisabled()
    await page.getByLabel('Administrator port', { exact: true }).fill('2222')
    await page.getByLabel('Customer port', { exact: true }).fill('3333')
    await expect(button).toBeDisabled()
    await page.getByRole('checkbox').check()
    await button.click()
    expect(submitted).toEqual({ admin_port: 2222, customer_port: 3333, confirm: true })
    await expect(page.getByRole('heading', { name: 'Applying panel ports' })).toBeVisible()
    await expect(page.getByRole('link', { name: 'Open new administrator address' })).toHaveAttribute('href', 'https://127.0.0.1:2222/app/panel-settings')
    job = { ...job, status: 'failed', error: 'Previous listeners restored.' }
    await expect(page.getByRole('heading', { name: 'Port change failed' })).toBeVisible({ timeout: 10000 })
    await expect(page.getByRole('link', { name: 'Open new administrator address' })).toHaveCount(0)
    await page.setViewportSize({ width: 390, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: info.outputPath(`${skin}-${mode}-panel-settings.png`), fullPage: true })
  })
}
