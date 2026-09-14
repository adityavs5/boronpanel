import { test, expect } from '@playwright/test'

for (const skin of ['evolution', 'paper-lantern']) for (const theme of ['light', 'dark']) {
  test(`${skin} ${theme}: select and queue mailbox restore`, async ({ page }, info) => {
    const errors = []; page.on('pageerror', error => errors.push(error.message))
    await page.addInitScript(({ skin, theme }) => {
      localStorage.setItem('boron.ui', JSON.stringify({ state: { skin, theme }, version: 0 }))
      localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'customer', username: 'alpha' }, version: 0 }))
    }, { skin, theme })
    let request = null
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname
      let data = {}
      if (path.endsWith('/whoami')) data = { role: 'customer', username: 'alpha' }
      else if (path.endsWith('/onboarding')) data = { completed: true }
      else if (path.endsWith('/snapshots/runs')) data = { runs: [{ id: 1, username: 'alpha', status: 'completed', snapshot_id: 'a'.repeat(64), started_at: '2026-09-14T00:00:00Z', options: { components: ['mail'] } }] }
      else if (path.endsWith('/mailboxes')) data = { mailboxes: [
        { address: 'inbox@example.test', available: true, action: 'existing' },
        { address: 'deleted@example.test', available: true, action: 'recreate' },
        { address: 'foreign@example.test', available: false, reason: 'This domain belongs to another account.' },
      ] }
      else if (path.endsWith('/runs/1/restore')) { request = route.request().postDataJSON(); data = { id: 2, status: 'pending' } }
      else if (path.endsWith('/snapshots/restores')) data = { restores: [] }
      else if (path.endsWith('/backups')) data = { jobs: [] }
      else if (path.endsWith('/restores/list')) data = { restore_jobs: [] }
      await route.fulfill({ json: data })
    })
    await page.goto('/app/backups')
    await page.getByRole('button', { name: 'View details', exact: true }).click()
    await page.getByRole('button', { name: 'Restore mailboxes', exact: true }).click()
    const form = page.getByRole('form', { name: 'Mailbox restore' })
    await expect(form.getByRole('checkbox', { name: /foreign@example/ })).toBeDisabled()
    const submit = form.getByRole('button', { name: 'Restore selected mailboxes', exact: true })
    await expect(submit).toBeDisabled()
    await form.getByLabel('Search backed-up mailboxes').fill('INBOX')
    await form.getByRole('button', { name: 'Select shown', exact: true }).click()
    await form.getByLabel('Search backed-up mailboxes').fill('')
    await form.getByRole('checkbox', { name: /deleted@example/ }).check()
    await form.getByLabel('Type alpha to confirm mailbox restore').fill('alpha')
    await expect(submit).toBeDisabled()
    await form.getByRole('checkbox', { name: /I understand/ }).check()
    await page.setViewportSize({ width: 390, height: 844 })
    await submit.scrollIntoViewIfNeeded()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: info.outputPath(`${skin}-${theme}-mail-restore.png`), fullPage: true })
    await submit.click()
    await expect(form).not.toBeVisible()
    expect(request).toEqual({ kind: 'mail', mailboxes: ['inbox@example.test', 'deleted@example.test'], confirmation: 'alpha', mail_pause_acknowledged: true })
    expect(errors).toEqual([])
  })
}
