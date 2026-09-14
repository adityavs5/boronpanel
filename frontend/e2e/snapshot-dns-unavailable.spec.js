import { test, expect } from '@playwright/test'

for (const skin of ['evolution', 'paper-lantern']) {
  test(`${skin}: unavailable DNS records explain why and retain cron recovery`, async ({ page }) => {
    await page.addInitScript(skin => {
      localStorage.setItem('boron.ui', JSON.stringify({state: {skin, theme: 'light'}, version: 0}))
      localStorage.setItem('boron.auth', JSON.stringify({state: {role: 'customer', username: 'alpha'}, version: 0}))
    }, skin)
    let mutations = 0
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname
      if (route.request().method() !== 'GET') mutations++
      let data = {}
      if (path.endsWith('/whoami')) data = {role: 'customer', username: 'alpha'}
      else if (path.endsWith('/onboarding')) data = {completed: true}
      else if (path.endsWith('/snapshots/runs')) data = {runs: [{id: 1, status: 'completed', snapshot_id: 'a'.repeat(64), options: {components: ['config']}, summary: {}}]}
      else if (path.endsWith('/configuration')) data = {cron_available: true, dns_available: false, dns_reason: 'A saved DNS zone is no longer owned by this account'}
      else if (path.endsWith('/snapshots/restores')) data = {restores: []}
      else if (path.endsWith('/backups')) data = {jobs: []}
      else if (path.endsWith('/restores/list')) data = {restore_jobs: []}
      await route.fulfill({json: data})
    })
    await page.goto('/app/backups')
    await page.getByRole('button', {name: 'View details', exact: true}).click()
    await page.getByRole('button', {name: 'Restore DNS records', exact: true}).click()
    const dns = page.getByRole('form', {name: 'DNS records restore'})
    await expect(dns.getByText('A saved DNS zone is no longer owned by this account')).toBeVisible()
    await expect(dns.getByRole('button', {name: 'Restore DNS records now'})).toHaveCount(0)
    await page.getByRole('button', {name: 'Restore scheduled tasks', exact: true}).click()
    const cron = page.getByRole('form', {name: 'Scheduled-task restore'})
    await expect(cron.getByRole('textbox', {name: 'Confirm account username', exact: true})).toBeVisible()
    await expect(cron.getByRole('button', {name: 'Restore scheduled tasks now'})).toBeDisabled()
    expect(mutations).toBe(0)
  })
}
