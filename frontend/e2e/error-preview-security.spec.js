import { test, expect } from '@playwright/test'

// Exercise normal CSP and prove the sandbox independently of that extra layer.
for (const bypassCSP of [false, true]) test.describe(`preview with bypassCSP=${bypassCSP}`, () => {
test.use({ bypassCSP })
test('customer HTML preview is visible but cannot execute or access the panel', async ({ page, context }) => {
  const html = `<h1>Custom error preview</h1><script>parent.previewEscaped=true;fetch('/api/security-canary')</script>
    <form action="/api/security-canary" method="post"><button>Send</button></form>`
  let canaryRequests = 0
  await page.addInitScript(() => {
    localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'customer', username: 'previewdemo' }, version: 0 }))
  })
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    let data = {}
    if (path.endsWith('/whoami')) data = { role: 'customer', username: 'previewdemo' }
    else if (path.endsWith('/onboarding')) data = { completed: true }
    else if (path.endsWith('/domains')) data = { domains: [{ domain: 'example.com', kind: 'primary' }] }
    else if (path.endsWith('/error-pages')) data = { pages: [{ code: 404, has_custom: true }] }
    else if (path.endsWith('/error-pages/404')) data = { content: html, code: 404 }
    else if (path === '/api/security-canary') canaryRequests++
    await route.fulfill({ json: data })
  })
  await page.goto('/app/error-pages')
  await expect(page.locator('textarea')).toHaveValue(html)
  await page.getByRole('button', { name: 'Preview', exact: true }).click()
  const iframe = page.locator('iframe[title="Error page preview"]')
  await expect(iframe).toHaveAttribute('sandbox', '')
  const frame = page.frameLocator('iframe[title="Error page preview"]')
  await expect(frame.getByRole('heading', { name: 'Custom error preview' })).toBeVisible()
  const child = await (await iframe.elementHandle()).contentFrame()
  expect(await child.evaluate(() => {
    try { return !!parent.document.body } catch { return false }
  })).toBe(false)
  await frame.getByRole('button', { name: 'Send' }).click()
  expect(await page.evaluate(() => window.previewEscaped)).toBeUndefined()
  expect(canaryRequests).toBe(0)
  expect(context.pages()).toHaveLength(1)
  await page.getByRole('button', { name: 'Close', exact: true }).last().click()
  await expect(iframe).toHaveCount(0)
})
})
