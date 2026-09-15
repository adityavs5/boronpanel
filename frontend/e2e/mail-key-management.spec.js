import { test, expect } from '@playwright/test'

for (const skin of ['evolution','paper-lantern']) for (const theme of ['light','dark']) {
  test(`${skin} ${theme}: direct mailbox and SSH key management`, async ({page}) => {
    const errors=[];page.on('pageerror',e=>errors.push(e.message))
    await page.addInitScript(({skin,theme})=>{
      localStorage.setItem('boron.ui',JSON.stringify({state:{skin,theme},version:0}))
      localStorage.setItem('boron.auth',JSON.stringify({state:{role:'customer',username:'alpha'},version:0}))
    },{skin,theme})
    let passwordRequest,removed=0
    await page.route('**/api/**',async route=>{
      const path=new URL(route.request().url()).pathname
      let data={}
      if(path.endsWith('/whoami'))data={role:'customer',username:'alpha'}
      else if(path.endsWith('/onboarding'))data={completed:true}
      else if(path==='/api/v1/mail/webmail')data={enabled:true,url:'https://webmail.example.test'}
      else if(path==='/api/v1/accounts/alpha/domains')data={domains:[{domain:'alpha.test'}]}
      else if(path.endsWith('/mailboxes'))data={mailboxes:[{local_part:'inbox',quota_mb:512,active:true}]}
      else if(path.endsWith('/email/inbox/password')){passwordRequest=route.request().postDataJSON();data={changed:true}}
      else if(path.endsWith('/ssh-keys'))data={keys:[{comment:'Work laptop',type:'ED25519',bits:256,fingerprint:'SHA256:test-fixture-fingerprint'}]}
      else if(route.request().method()==='DELETE'){removed++;data={deleted:true}}
      await route.fulfill({json:data})
    })
    await page.goto('/app/email')
    await expect(page.getByRole('link',{name:'Open Webmail'})).toHaveAttribute('href','https://webmail.example.test')
    await page.getByRole('button',{name:'inbox@alpha.test',exact:true}).click()
    let dialog=page.getByRole('dialog')
    await expect(dialog.getByRole('heading',{name:'Manage mailbox'})).toBeVisible()
    await expect(dialog.getByRole('button',{name:'Update password'})).toBeDisabled()
    await dialog.getByLabel(/New mailbox password/).fill('Fixture-only-Pass123!')
    await dialog.getByRole('button',{name:'Update password'}).click()
    await expect(dialog.getByLabel(/New mailbox password/)).toHaveValue('')
    expect(passwordRequest).toEqual({domain:'alpha.test',password:'Fixture-only-Pass123!'})
    await dialog.getByRole('button',{name:'Done',exact:true}).click()
    await page.setViewportSize({width:390,height:844})
    await page.getByRole('button',{name:'Manage',exact:true}).filter({visible:true}).click()
    await expect(dialog.getByRole('heading',{name:'Manage mailbox'})).toBeVisible()
    await dialog.getByRole('button',{name:'Done',exact:true}).click()
    await page.goto('/app/ssh')
    await page.getByRole('button',{name:'Work laptop',exact:true}).filter({visible:true}).click()
    dialog=page.getByRole('dialog')
    await expect(dialog.getByRole('heading',{name:'Manage SSH key'})).toBeVisible()
    await expect(dialog.getByLabel('Key fingerprint',{exact:true})).toHaveValue('SHA256:test-fixture-fingerprint')
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
    await dialog.getByRole('button',{name:'Remove key',exact:true}).click()
    await expect(page.getByRole('dialog').getByRole('heading',{name:'Remove Work laptop?'})).toBeVisible()
    await page.getByRole('dialog').getByRole('button',{name:'Cancel',exact:true}).click()
    const removeButton=page.getByRole('button',{name:'Remove SSH key Work laptop',exact:true}).filter({visible:true})
    await removeButton.focus()
    await page.keyboard.press('Space')
    await expect(page.getByRole('dialog').getByRole('heading',{name:'Remove Work laptop?'})).toBeVisible()
    await expect(page.getByRole('heading',{name:'Manage SSH key',exact:true})).toHaveCount(0)
    await page.getByRole('dialog').getByRole('button',{name:'Cancel',exact:true}).click()
    const keyRow=page.getByRole('link').filter({has:removeButton})
    await keyRow.focus()
    await page.keyboard.press('Enter')
    await expect(page.getByRole('heading',{name:'Manage SSH key',exact:true})).toBeVisible()
    expect(removed).toBe(0)
    expect(errors).toEqual([])
  })
}
