import { test, expect } from '@playwright/test'
import { readFileSync } from 'node:fs'

const pageHtml = (heading, message) => `<!doctype html><html lang="en"><head><meta charset="utf-8"><style>*{box-sizing:border-box}html,body{margin:0;min-height:100%;font-family:system-ui}body{display:grid;place-items:center;min-height:100vh;padding:24px;background:#f1f5f9;color:#0f172a}main{width:min(680px,100%);padding:32px;background:#fff;border-top:6px solid #2563eb}h1{overflow-wrap:anywhere;font-size:clamp(28px,5vw,52px);margin:0 0 16px}p{font-size:16px;line-height:1.6;margin:0}</style></head><body><main><h1>${heading}</h1><p>${message}</p><p class="help">Contact your hosting provider.</p></main></body></html>`
const defaultWelcome = readFileSync(new URL('../../templates/site_pages/welcome.html', import.meta.url), 'utf8')
async function mockTemplates(page, skin = 'evolution', theme = 'light') {
  await page.addInitScript(({ skin, theme }) => {
    if (window !== window.top) return // Do not inject test-only storage into opaque sandbox previews.
    localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'admin', username: 'preview' }, version: 0 }))
    localStorage.setItem('boron.ui', JSON.stringify({ state: { skin, theme }, version: 0 }))
  }, { skin, theme })
  const writes = []
  let failSave = false
  let welcome = defaultWelcome
  let suspended = pageHtml('Account suspended', 'Please contact your hosting provider for assistance.')
  let design = { template_key: 'clean', accent_color: '#2563eb', heading: 'Account suspended', message: 'Please contact your hosting provider for assistance.' }
  const names = { clean: 'Clean notice', gradient: 'Modern gradient', classic: 'Classic hosting', minimal: 'Minimal' }
  await page.route('**/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname
    if (request.method() !== 'GET') {
      writes.push({ path, method: request.method(), data: request.postDataJSON() })
      if (failSave) return route.fulfill({ status: 422, json: { detail: 'The template could not be saved.', reference: 'tpl-review-1' } })
      if (path.endsWith('/welcome-page')) welcome = request.method() === 'DELETE' ? defaultWelcome : request.postDataJSON().content
      if (path.endsWith('/suspended-page')) suspended = request.postDataJSON().content
      if (path.endsWith('/suspension-designs')) { design = request.postDataJSON(); suspended = pageHtml(design.heading, design.message) }
    }
    let data = {}
    if (path.endsWith('/whoami')) data = { role: 'admin', username: 'preview' }
    if (path.endsWith('/branding')) data = { panel_name: 'SiteCountry BoronPanel' }
    if (path.endsWith('/welcome-page')) data = { content: welcome, default_content: defaultWelcome, is_custom: welcome !== defaultWelcome, placeholders: ['domain', 'panel_name'] }
    if (path.endsWith('/suspended-page')) data = { content: suspended }
    if (path.endsWith('/suspension-designs')) data = { current: design, templates: Object.entries(names).map(([key, name]) => ({ key, name, description: `The ${name.toLowerCase()} suspension layout.`, html: pageHtml(design.heading, design.message) })) }
    if (path.endsWith('/welcome-email')) data = { subject: null, body: null, placeholders: ['username', 'primary_domain', 'panel_name'] }
    await route.fulfill({ json: data })
  })
  return { writes, failSave() { failSave = true } }
}

async function noOverflow(page) { expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true) }
async function containedPreview(page, title, background = 'rgb(241, 245, 249)') {
  const frame = page.frameLocator(`iframe[title="${title}"]`)
  await expect(frame.locator('main')).toBeVisible()
  expect(await frame.locator('body').evaluate(element => getComputedStyle(element).backgroundColor)).toBe(background)
  const dimensions = await frame.locator('main').evaluate(element => { const rect = element.getBoundingClientRect(); return { top: rect.top, bottom: rect.bottom, left: rect.left, right: rect.right, width: innerWidth, height: innerHeight } })
  expect(dimensions.top).toBeGreaterThanOrEqual(0)
  expect(dimensions.bottom).toBeLessThanOrEqual(dimensions.height)
  expect(dimensions.left).toBeGreaterThanOrEqual(0)
  expect(dimensions.right).toBeLessThanOrEqual(dimensions.width)
}

for (const skin of ['evolution', 'paper-lantern']) for (const theme of ['light', 'dark']) {
  test(`templates show full responsive previews and preserve save contracts ${skin} ${theme}`, async ({ page }) => {
    test.setTimeout(90_000)
    const mocked = await mockTemplates(page, skin, theme)
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.goto('/app/templates')
    await expect(page.getByRole('heading', { name: 'Templates', exact: true })).toBeVisible()
    await expect(page.frameLocator('iframe[title="Welcome page preview"]').getByRole('heading', { name: 'Welcome to preview.example.com' })).toBeVisible()
    await expect(page.frameLocator('iframe[title="Welcome page preview"]').getByText('SiteCountry BoronPanel', { exact: true })).toBeVisible()
    for (const width of [1440, 768, 375]) {
      await page.setViewportSize({ width, height: 900 })
      await page.getByRole('tab', { name: 'Welcome page', exact: true }).click()
      await containedPreview(page, 'Welcome page preview', 'rgb(246, 248, 252)')
      await noOverflow(page)
      await page.getByRole('group', { name: 'Welcome page preview viewport' }).getByRole('button', { name: 'Mobile' }).click()
      await containedPreview(page, 'Welcome page preview', 'rgb(246, 248, 252)')
      await page.screenshot({ path: test.info().outputPath(`welcome-${width}.png`) })
      await page.getByRole('group', { name: 'Welcome page preview viewport' }).getByRole('button', { name: 'Desktop' }).click()
      await page.getByRole('tab', { name: 'Suspension page', exact: true }).click()
      await expect(page.locator('.template-design')).toHaveCount(4)
      await containedPreview(page, 'Clean notice preview')
      await containedPreview(page, 'Suspension page preview')
      await noOverflow(page)
      await page.screenshot({ path: test.info().outputPath(`suspension-${width}.png`) })
      await page.getByRole('tab', { name: 'Welcome email', exact: true }).click()
      await expect(page.getByText('Passwords are never included.', { exact: false })).toBeVisible()
      await expect(page.getByText('{{password}}', { exact: false })).toHaveCount(0)
      await noOverflow(page)
    }
    expect(mocked.writes).toEqual([])
    await page.getByRole('tab', { name: 'Suspension page', exact: true }).click()
    await page.getByLabel('Heading', { exact: true }).fill('Please contact support')
    await expect(page.frameLocator('iframe[title="Selected suspension design preview"]').getByRole('heading', { name: 'Please contact support' })).toBeVisible()
    await page.getByLabel('Message', { exact: true }).fill('Our team can help restore this account.')
    await page.getByRole('button', { name: 'Apply design', exact: true }).click()
    await expect(page.getByText('Suspension design applied', { exact: true })).toBeVisible()
    expect(mocked.writes[0]).toEqual({ path: '/api/v1/admin/templates/suspension-designs', method: 'PUT', data: { template_key: 'clean', accent_color: '#2563eb', heading: 'Please contact support', message: 'Our team can help restore this account.' } })
    await page.getByRole('tab', { name: 'Welcome page', exact: true }).click()
    await page.locator('section[aria-label="Welcome page editor"] summary').click()
    const custom = pageHtml('Draft {{domain}}', 'Custom {{panel_name}} welcome')
    await page.getByLabel('Welcome page HTML', { exact: true }).fill(custom)
    await expect(page.frameLocator('iframe[title="Welcome page preview"]').getByRole('heading', { name: 'Draft preview.example.com' })).toBeVisible()
    await page.getByRole('tab', { name: 'Welcome email', exact: true }).click()
    await page.getByRole('tab', { name: 'Welcome page', exact: true }).click()
    await expect(page.getByLabel('Welcome page HTML', { exact: true })).toHaveValue(custom)
    await page.getByRole('button', { name: 'Save welcome page', exact: true }).click()
    await expect(page.getByText('Welcome page saved', { exact: true })).toBeVisible()
    expect(mocked.writes[1]).toEqual({ path: '/api/v1/admin/templates/welcome-page', method: 'PUT', data: { content: custom } })
    await page.getByRole('button', { name: 'Revert to default', exact: true }).click()
    await expect(page.getByRole('dialog', { name: 'Reset welcome page?' })).toBeVisible()
    await page.getByRole('dialog').getByRole('button', { name: 'Revert to default', exact: true }).click()
    await expect(page.frameLocator('iframe[title="Welcome page preview"]').getByRole('heading', { name: 'Welcome to preview.example.com' })).toBeVisible()
    expect(mocked.writes[2]).toEqual({ path: '/api/v1/admin/templates/welcome-page', method: 'DELETE', data: null })
    expect(errors).toEqual([])
  })
}

test('template preview blocks active content and failed saves retain the draft', async ({ page }) => {
  const mocked = await mockTemplates(page)
  const external = []
  await page.route('https://template-preview.invalid/**', async route => { external.push(route.request().url()); await route.abort() })
  await page.goto('/app/templates')
  await page.locator('section[aria-label="Welcome page editor"] summary').click()
  const hostile = pageHtml('Safe draft', 'Scripts and external resources are not run.').replace('<body>', '<body class="custom-body" style="color:rgb(20,30,40)">') + '<script>parent.__previewExecuted = true; fetch("https://template-preview.invalid/script")</script><meta http-equiv="refresh" content="0;url=https://template-preview.invalid/redirect"><img src="https://template-preview.invalid/image"><iframe src="https://template-preview.invalid/frame"></iframe><style>body{background-image:url(https://template-preview.invalid/css)}</style><form action="https://template-preview.invalid/form"><button autofocus>Submit</button></form>'
  await page.getByLabel('Welcome page HTML', { exact: true }).fill(hostile)
  const iframe = page.locator('iframe[title="Welcome page preview"]')
  await expect(iframe).toHaveAttribute('sandbox', '')
  await expect(iframe).toHaveAttribute('referrerpolicy', 'no-referrer')
  const frame = page.frameLocator('iframe[title="Welcome page preview"]')
  await expect(frame.getByRole('heading', { name: 'Safe draft' })).toBeVisible()
  await expect(frame.locator('body')).toHaveClass('custom-body')
  expect(await frame.locator('body').evaluate(element => getComputedStyle(element).color)).toBe('rgb(20, 30, 40)')
  expect(await frame.locator('meta[http-equiv="Content-Security-Policy"]').getAttribute('content')).toContain("default-src 'none'")
  await expect(frame.locator('script,iframe,meta[http-equiv="refresh"]')).toHaveCount(0)
  await expect(frame.locator('form[action],img[src]')).toHaveCount(0)
  expect(await page.evaluate(() => window.__previewExecuted)).toBeUndefined()
  expect(external).toEqual([])
  mocked.failSave()
  await page.getByRole('button', { name: 'Save welcome page', exact: true }).click()
  await expect(page.getByText('Could not save', { exact: true })).toBeVisible()
  await expect(page.getByLabel('Welcome page HTML', { exact: true })).toHaveValue(hostile)
  await expect(page.getByRole('region', { name: 'Unsaved changes', exact: true })).toBeVisible()
  expect(mocked.writes).toHaveLength(1)
})
