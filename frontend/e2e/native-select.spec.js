import { test, expect } from '@playwright/test'

for (const skin of ['evolution', 'paper-lantern']) for (const mode of ['light', 'dark']) {
  test(`${skin} ${mode}: native arrows survive tool surfaces and padding`, async ({ page }) => {
    await page.addInitScript(({ skin, mode }) => {
      localStorage.setItem('boron.ui', JSON.stringify({ state: { skin, theme: mode }, version: 0 }))
      localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'customer', username: 'alpha' }, version: 0 }))
    }, { skin, mode })
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname
      const data = path.endsWith('/whoami') ? { role: 'customer', username: 'alpha' }
        : path.endsWith('/onboarding') ? { completed: true }
        : path.endsWith('/domains') ? { domains: [{ domain: 'example.com', kind: 'primary' }] }
        : path.endsWith('/records') ? { zone: 'example.com', managed: true, records: [] } : {}
      await route.fulfill({ json: data })
    })
    await page.goto('/app/dns')
    await page.getByRole('button', { name: 'Add record', exact: true }).click()
    const dialog = page.getByRole('dialog')
    const shared = dialog.getByLabel('Type', { exact: true })
    await expect(shared).toHaveCSS('background-repeat', 'no-repeat')
    await expect(shared).toHaveCSS('background-size', '16px 16px')
    // Keep the actual shared component and also exercise plain native selects,
    // page surface rules, padding utilities, disabled and listbox variants.
    await dialog.evaluate(element => {
      const fixture = document.createElement('div')
      fixture.id = 'native-select-controls'
      fixture.innerHTML = `<div class="wp-form"><select aria-label="Long native option" class="px-3" style="width:180px"><option value="first">An intentionally long domain name that must stop before the arrow</option><option value="second">Second option</option></select><select disabled aria-label="Disabled native option"><option>Disabled</option></select></div><div class="domain-picker"><select><option>example.com</option></select></div><div class="subdomain-parent-control"><select><option>example.com</option></select></div><select multiple><option>One</option><option>Two</option></select><select size="3"><option>One</option><option>Two</option></select><select size="1"><option>One</option></select>`
      element.append(fixture)
    })
    const checks = await dialog.locator('select').evaluateAll(elements => elements.map(element => {
      const c = getComputedStyle(element)
      return { listbox: element.multiple || (element.hasAttribute('size') && element.getAttribute('size') !== '1'),
        image: c.backgroundImage, repeat: c.backgroundRepeat, size: c.backgroundSize,
        appearance: c.appearance, padding: c.paddingRight, ellipsis: c.textOverflow }
    }))
    for (const check of checks) {
      expect(check.repeat).toBe('no-repeat')
      expect(check.size).toBe('16px 16px')
      expect(check.appearance).toBe('none')
      expect(check.image === 'none').toBe(check.listbox)
      expect(check.padding).toBe(check.listbox ? '12px' : '36px')
      expect(check.ellipsis).toBe('ellipsis')
    }
    await expect(dialog.getByLabel('Disabled native option')).toHaveCSS('opacity', '0.5')
    const long = dialog.getByLabel('Long native option')
    await long.focus()
    expect(await long.evaluate(element => {
      const c = getComputedStyle(element)
      return element.matches(':focus-visible') && (c.outlineStyle !== 'none' || c.boxShadow !== 'none')
    })).toBe(true)
    await long.selectOption('second')
    await expect(long).toHaveValue('second')
    await expect(shared).toHaveValue('A')
  })
}
