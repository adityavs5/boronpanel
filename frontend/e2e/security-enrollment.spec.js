import { test, expect } from '@playwright/test'

for (const skin of ['evolution', 'paper-lantern']) for (const theme of ['light', 'dark']) {
  test(`password-gated enrollment and impersonation revocation: ${skin} ${theme}`, async ({ page }) => {
    await page.addInitScript(({ skin, theme }) => {
      localStorage.setItem('boron.ui', JSON.stringify({ state: { skin, theme }, version: 0 }))
      localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'admin', username: 'reviewadmin' }, version: 0 }))
    }, { skin, theme })
    const requests = []
    let enabled = false
    let revoked = false
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname
      let data = {}
      if (path.endsWith('/whoami')) data = { role: 'admin', username: 'reviewadmin' }
      else if (path.endsWith('/branding')) data = { panel_name: 'Boron' }
      else if (path.endsWith('/2fa/status')) data = { enabled }
      else if (path.endsWith('/2fa/setup')) {
        requests.push(route.request().postDataJSON())
        data = { secret: 'FIXTUREONLY', otpauth_uri: 'otpauth://totp/fixture', qr_data_uri: '' } // pragma: allowlist secret (synthetic fixture)
      } else if (path.endsWith('/2fa/verify')) {
        requests.push(route.request().postDataJSON())
        enabled = true
        data = { enabled, recovery_codes: ['fixture-code'] }
      } else if (path.endsWith('/security/sessions/2')) {
        revoked = true
        data = { status: 'revoked' }
      } else if (path.endsWith('/security/sessions')) data = {
        sessions: [{ id: 1, current: true, device: 'Current browser' }],
        impersonation_sessions: revoked ? [] : [{ id: 2, current: false, revocable: true, device: 'Other browser' }],
      }
      await route.fulfill({ json: data })
    })
    await page.goto('/app/security')
    const enable = page.getByRole('button', { name: 'Enable 2FA', exact: true })
    await expect(enable).toBeDisabled()
    await page.getByLabel('Current password', { exact: true }).fill('Synthetic-fixture-928!')
    await enable.click()
    await page.locator('#totp-code').fill('123456')
    await page.getByRole('button', { name: 'Verify and enable', exact: true }).click()
    await expect(page.getByText('fixture-code', { exact: true })).toBeVisible()
    expect(requests).toEqual([
      { current_password: 'Synthetic-fixture-928!' }, // pragma: allowlist secret (synthetic fixture)
      { current_password: 'Synthetic-fixture-928!', code: '123456' }, // pragma: allowlist secret (synthetic fixture)
    ])
    await page.getByRole('button', { name: 'Revoke session', exact: true }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Revoke session', exact: true }).click()
    await expect(page.getByText('Other browser', { exact: true })).toHaveCount(0)
  })
}
