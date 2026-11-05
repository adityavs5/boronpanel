import { test, expect } from '@playwright/test'

// Modern must stay easy to navigate. These walk the real menu and search the
// way a person would, using everyday words rather than the panel's own labels.
async function open(page, role, username) {
  await page.addInitScript(({ role, username }) => {
    localStorage.setItem('boron.auth', JSON.stringify({ state: { role, username }, version: 0 }))
    localStorage.setItem('boron.public-branding.v1', JSON.stringify({ panel_name: 'Boron', theme: 'modern' }))
  }, { role, username })
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/whoami')) return route.fulfill({ json: { role, username } })
    if (path.endsWith('/branding')) return route.fulfill({ json: { panel_name: 'Boron', theme: 'modern' } })
    if (path.endsWith('/onboarding')) return route.fulfill({ json: { completed: true } })
    return route.fulfill({ json: {} })
  })
}

const tasks = {
  customer: [
    ['mailbox', /\/app\/email/],
    ['webmail', /\/app\/email|webmail/],
    ['certificate', /\/app\/ssl/],
    ['mysql', /\/app\/databases/],
    ['upload files', /\/app\/files/],
    ['php version', /\/app\/php/],
    ['restore backup', /\/app\/backups/],
    ['wordpress', /\/app\/wordpress/],
    ['cron', /\/app\/cron/],
    ['nameserver', /\/app\/dns/],
    ['redirect', /\/app\/redirects/],
    ['ftp', /\/app\/ftp/],
    ['change password', /\/app\/change-password/],
    ['disk space', /\/app\/disk-usage/],
  ],
  admin: [
    ['create account', /\/app\/accounts\/new/],
    ['hosting plan', /\/app\/plans/],
    ['firewall', /\/app\/firewall/],
    ['block ip', /\/app\/ip-bans/],
    ['restart service', /\/app\/services/],
    ['update panel', /\/app\/updates/],
    ['panel theme', /\/app\/panel-theme/],
    ['backup schedule', /\/app\/backup-jobs/],
    ['reseller', /\/app\/resellers/],
    ['license', /\/app\/license/],
  ],
}

for (const [role, username, home] of [['customer', 'alpha', '/app/dashboard'], ['admin', 'preview', '/app/overview']]) {
  test(`modern ${role}: every tool is two clicks from the menu`, async ({ page }) => {
    await open(page, role, username)
    await page.goto(home)
    await expect(page.locator('main h1').first()).toBeVisible()
    const sections = page.locator('aside .sidebar-section')
    await expect(sections.first()).toBeVisible()
    const count = await sections.count()
    expect(count).toBeGreaterThanOrEqual(6)
    for (let i = 0; i < count; i++) {
      const section = sections.nth(i)
      if (await section.getAttribute('aria-expanded') !== 'true') await section.click()
      await expect(section).toHaveAttribute('aria-expanded', 'true')
    }
    const links = page.locator('aside a.sidebar-tool:not(.sidebar-home)')
    const tools = await links.evaluateAll(items => items.map(item => ({
      label: item.getAttribute('aria-label') || item.innerText.trim(),
      path: new URL(item.href).pathname,
      external: item.target === '_blank',
      section: item.closest('.sidebar-group, section, div')?.parentElement?.querySelector('.sidebar-section')?.innerText.trim(),
    })))
    expect(tools.length).toBeGreaterThanOrEqual(role === 'admin' ? 40 : 30)

    // Really walk each tool from a fresh home page, counting the clicks.
    test.setTimeout(240000)
    for (const tool of tools) {
      await page.goto(home)
      const link = page.locator('aside').getByRole('link', { name: tool.label, exact: true }).first()
      await expect(page.locator('aside .sidebar-section').first()).toBeVisible()
      let clicks = 0
      if (!await link.isVisible()) {
        await page.locator('aside .sidebar-section').filter({ hasText: tool.section }).first().click()
        clicks++
      }
      await expect(link, `${tool.label} is in its menu section`).toBeVisible()
      if (tool.external) continue
      await link.click()
      clicks++
      await expect.poll(() => new URL(page.url()).pathname, { message: `${tool.label} opens its page` }).toBe(tool.path)
      expect(clicks, tool.label).toBeLessThanOrEqual(2)
    }
  })

  for (const [words, destination] of tasks[role]) {
    test(`modern ${role}: searching "${words}" finds the right tool`, async ({ page }) => {
      await open(page, role, username)
      await page.goto(home)
      await expect(page.locator('main h1').first()).toBeVisible()
      await page.keyboard.press('Control+k')
      await page.getByRole('combobox', { name: 'Search tools and settings' }).fill(words)
      await page.keyboard.press('Enter')
      await expect(page).toHaveURL(destination)
    })
  }
}
