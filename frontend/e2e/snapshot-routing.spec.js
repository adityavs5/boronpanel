import { test, expect } from '@playwright/test'

for (const skin of ['evolution', 'paper-lantern']) for (const theme of ['light', 'dark']) {
  test(`${skin} ${theme}: email settings selection, restore and undo`, async ({ page }, info) => {
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.addInitScript(({ skin, theme }) => {
      localStorage.setItem('boron.ui', JSON.stringify({ state: { skin, theme }, version: 0 }))
      localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'customer', username: 'alpha' }, version: 0 }))
    }, { skin, theme })
    let request, undoRequest, reads = 0
    const restores = []
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname
      let data = {}
      if (path.endsWith('/whoami')) data = { role: 'customer', username: 'alpha' }
      else if (path.endsWith('/onboarding')) data = { completed: true }
      else if (path.endsWith('/snapshots/runs')) data = { runs: [{ id: 1, username: 'alpha', status: 'completed', snapshot_id: 'a'.repeat(64), options: { components: ['mail'] } }] }
      else if (path.endsWith('/mail-routing')) {
        reads++
        data = { domains: [{ domain: 'alpha.example', available: true, forwarders: 2, catchall: true, autoresponders: 1 }, { domain: 'gone.example', available: false, reason: 'Domain no longer belongs to this account' }] }
      } else if (path.endsWith('/runs/1/restore')) {
        request = route.request().postDataJSON()
        restores.push({ id: 10, status: 'completed', selection: request, safety_snapshot_id: 'b'.repeat(64), summary: { routing_finalized: true }, progress_message: 'Email settings restored' })
        restores.push({ id: 11, status: 'failed', selection: request, safety_snapshot_id: 'c'.repeat(64), summary: { routing_finalized: true, rolled_back: true } })
        restores.push({ id: 12, status: 'failed', selection: request, safety_snapshot_id: 'd'.repeat(64), summary: {} })
        data = { id: 10, status: 'pending' }
      } else if (path.endsWith('/restores/10/undo')) { undoRequest = route.request().postDataJSON(); data = { id: 13, status: 'pending' } }
      else if (path.endsWith('/snapshots/restores')) data = { restores }
      else if (path.endsWith('/backups')) data = { jobs: [] }
      else if (path.endsWith('/restores/list')) data = { restore_jobs: [] }
      await route.fulfill({ json: data })
    })
    await page.goto('/app/backups')
    await page.getByRole('button', { name: 'View details', exact: true }).click()
    const detail = page.getByRole('dialog').filter({ has: page.getByRole('heading', { name: 'Recovery point #1', exact: true }) })
    expect(reads).toBe(0)
    await detail.getByRole('button', { name: 'Restore email settings', exact: true }).click()
    const form = detail.getByRole('form', { name: 'Email settings restore' })
    await expect(form.getByText('2 forwarders · Catch-all configured · 1 automatic replies')).toBeVisible()
    await expect(form.getByRole('checkbox', { name: /gone.example/ })).toBeDisabled()
    await form.getByRole('textbox', { name: 'Search backed-up email domains' }).fill('ALPHA')
    await form.getByRole('button', { name: 'Select shown' }).click()
    await expect(form.getByText('1 selected', { exact: true })).toBeVisible()
    const submit = form.getByRole('button', { name: 'Restore selected email settings' })
    await form.getByLabel('Type alpha to confirm email settings restore').fill('alpha')
    await expect(submit).toBeDisabled()
    await form.getByRole('checkbox', { name: 'I understand that mail access will be interrupted briefly.' }).check()
    await page.setViewportSize({ width: 390, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: info.outputPath('routing-mobile.png'), fullPage: true })
    await submit.click()
    expect(request).toEqual({ kind: 'mail_routing', mail_domains: ['alpha.example'], confirmation: 'alpha', mail_pause_acknowledged: true })
    await expect(detail.getByText('Previous email settings were recovered automatically. No further recovery is needed.').filter({ visible: true })).toBeVisible()
    await expect(detail.getByText('Recovery data is retained. Contact the server administrator to inspect this operation before trying again.').filter({ visible: true })).toBeVisible()
    const recover = detail.getByRole('button', { name: 'Recover previous email settings', exact: true }).filter({ visible: true })
    await expect(recover).toHaveCount(1)
    await recover.click()
    const undo = page.getByRole('dialog').filter({ has: page.getByRole('heading', { name: 'Recover previous email settings?', exact: true }) })
    await undo.getByRole('textbox').fill('alpha')
    await expect(undo.getByRole('button', { name: 'Recover previous email settings', exact: true })).toBeDisabled()
    await undo.getByRole('checkbox').check()
    await undo.getByRole('button', { name: 'Recover previous email settings', exact: true }).click()
    await expect(undo).not.toBeVisible()
    expect(undoRequest).toEqual({ confirmation: 'alpha', mail_pause_acknowledged: true })
    expect(errors).toEqual([])
  })
}
