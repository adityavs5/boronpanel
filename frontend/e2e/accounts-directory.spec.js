import { test, expect } from '@playwright/test'
for (const skin of ['modern', 'classic', 'grid']) {
  test(`${skin}: account directory, ownership and search focus`, async ({ page }) => {
    await page.addInitScript(skin => {
      localStorage.setItem('boron.auth', JSON.stringify({state:{role:'admin',username:'preview'},version:0}))
      localStorage.setItem('boron.public-branding.v1',JSON.stringify({panel_name:'Boron',theme:skin}));localStorage.setItem('boron.ui', JSON.stringify({state:{skin,theme:'light'},version:0}))
    }, skin)
    const assignments=[]
    await page.route('**/api/**', async route => {
      const path=new URL(route.request().url()).pathname
      let data={}
      if(path.endsWith('/whoami')) data={role:'admin',username:'preview'}
      else if(path.endsWith('/branding')) data={panel_name:'Boron'}
      else if(path==='/api/v1/accounts') data=[{username:'example',status:'active',primary_domain:'example.test',server_ip:'192.0.2.1',created_at:'2026-10-17T00:00:00Z'}]
      else if(path==='/api/v1/admin/resellers') data={resellers:[{id:4,username:'resellerdemo',status:'active'}]}
      else if(path==='/api/v1/admin/resellers/accounts/example'){assignments.push(route.request().postDataJSON());data={}}
      await route.fulfill({json:data})
    })
    await page.goto('/app/accounts')
    await expect(page.getByRole('heading',{name:'Accounts',exact:true})).toBeVisible()
    await expect(page.getByRole('table').getByText('example.test').filter({visible:true}).first()).toBeVisible()
    // Classic lists every column by default, as its reference does; the others open them from Columns.
    if(skin!=='classic'){
      await expect(page.getByRole('columnheader',{name:'Server IP',exact:true})).toHaveCount(0)
      await page.getByRole('button',{name:'Columns',exact:true}).click()
      await page.getByRole('menuitemcheckbox',{name:'Server IP',exact:true}).click()
      await page.keyboard.press('Escape')
    }
    await expect(page.getByRole('columnheader',{name:'Server IP',exact:true})).toBeVisible()
    await expect(page.getByRole('button',{name:'Login as example',exact:true})).toHaveCount(skin==='classic'?1:0)
    await page.getByRole('button',{name:'Actions for example',exact:true}).click()
    await page.getByRole('menuitem',{name:'Change reseller',exact:true}).click()
    const dialog=page.getByRole('dialog')
    await dialog.getByRole('combobox').click()
    await page.getByRole('option',{name:'resellerdemo',exact:true}).click()
    await dialog.getByRole('button',{name:'Save',exact:true}).click()
    await expect(dialog).toHaveCount(0)
    expect(assignments).toEqual([{reseller_id:4}])
    await page.getByRole('button',{name:'Suspended (0)',exact:true}).click()
    await expect(page).toHaveURL(/status=suspended/)
    await expect(page.getByRole('cell',{name:'example',exact:true})).toHaveCount(0)
    await page.getByRole('button',{name:'All',exact:true}).click()
    await page.getByRole('checkbox',{name:'Select example',exact:true}).check()
    await expect(page.locator('.accounts-bulk-bar')).toContainText('1 selected')
    await page.getByRole('button',{name:'Search the panel',exact:true}).click()
    const search=page.getByRole('combobox',{name:'Search tools and settings'})
    await expect(search).toBeFocused()
    expect(await search.evaluate(el=>getComputedStyle(el).outlineStyle)).toBe('none')
    expect(await page.locator('.command-search-row').evaluate(el=>getComputedStyle(el).boxShadow)).not.toBe('none')
  })
}
