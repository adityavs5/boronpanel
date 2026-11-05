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
    const tools = page.locator('aside a.sidebar-tool:not(.sidebar-home)')
    expect(await tools.count()).toBeGreaterThanOrEqual(role === 'admin' ? 40 : 30)
    for (const tool of await tools.all()) {
      await expect(tool).toBeVisible()
      await expect(tool).toHaveAccessibleName(/\S/)
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
