import { test, expect } from '@playwright/test'

const adminUsername = process.env.BORON_CI_ADMIN_USERNAME || 'ciadmin'
const adminPassword = process.env.BORON_CI_ADMIN_PASSWORD
const accountUsername = process.env.BORON_CI_ACCOUNT_USERNAME || 'ciaccount'

test('real login, account navigation, impersonation and File Manager entry', async ({ page }) => {
  expect(adminPassword, 'BORON_CI_ADMIN_PASSWORD must be supplied by the ephemeral CI job').toBeTruthy()

  await page.goto('/app/login')
  await page.getByLabel('Username', { exact: true }).fill(adminUsername)
  await page.locator('input#password').fill(adminPassword)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(page).toHaveURL(/\/app\/overview$/)
  await expect(page.getByRole('heading', { name: 'Admin Dashboard', exact: true })).toBeVisible()

  await page.goto('/app/accounts')
  await expect(page.getByRole('heading', { name: 'Accounts', exact: true })).toBeVisible()
  await expect(page.getByRole('row').filter({ hasText: accountUsername })).toBeVisible()

  await page.goto(`/app/accounts/${accountUsername}`)
  await expect(page.getByRole('heading', { name: accountUsername, exact: true })).toBeVisible()
  for (const name of ['Overview', 'Domains', 'Email', 'Databases', 'Backups', 'Security', 'Settings']) {
    await expect(page.getByRole('tab', { name, exact: true })).toBeVisible()
  }
  await page.getByRole('tab', { name: 'Domains', exact: true }).click()
  await expect(page.getByRole('tab', { name: 'Domains', exact: true })).toHaveAttribute('data-state', 'active')
  await page.getByRole('tab', { name: 'Overview', exact: true }).click()

  await page.getByRole('button', { name: 'Login as user', exact: true }).click()
  await expect(page).toHaveURL(/\/app\/dashboard$/, { timeout: 30_000 })
  await expect(page.getByRole('heading', { name: 'Hosting Dashboard', exact: true })).toBeVisible()

  const fileManager = page.getByText('File Manager', { exact: true }).first()
  await expect(fileManager).toBeVisible()
  await fileManager.click()
  await expect(page).toHaveURL(/\/app\/files$/)
  await expect(page.getByRole('heading', { name: 'File Manager', exact: true })).toBeVisible()
})
