import { test, expect } from '@playwright/test'

for (const skin of ['evolution','paper-lantern']) for (const theme of ['light','dark']) {
  test(`${skin} ${theme}: direct mailbox and SSH key management`, async ({page}) => {
    const errors=[];page.on('pageerror',e=>errors.push(e.message))
    await page.addInitScript(({skin,theme})=>{
      localStorage.setItem('boron.ui',JSON.stringify({state:{skin,theme},version:0}))
      localStorage.setItem('boron.auth',JSON.stringify({state:{role:'customer',username:'alpha'},version:0}))
      window.open=()=>null
      HTMLFormElement.prototype.submit=function(){window.__webmailSubmit={action:this.action,method:this.method,target:this.target,fields:Object.fromEntries(new FormData(this).entries())}}
    },{skin,theme})
    let passwordRequest,removed=0,mailboxActive=true
    await page.route('**/api/**',async route=>{
      const path=new URL(route.request().url()).pathname
      let data={}
      if(path.endsWith('/whoami'))data={role:'customer',username:'alpha'}
      else if(path.endsWith('/onboarding'))data={completed:true}
      else if(path==='/api/v1/accounts/alpha/domains')data={domains:[{domain:'alpha.test'}]}
      else if(path.endsWith('/mailboxes'))data={mailboxes:[{local_part:'inbox',quota_mb:512,active:mailboxActive}]}
      else if(path.endsWith('/webmail-session'))data={token:'fixture-one-use-token',url:'https://webmail.example.test'}
      else if(path.endsWith('/email/inbox/password')){passwordRequest=route.request().postDataJSON();data={changed:true}}
      else if(path.endsWith('/mailboxes/inbox')&&route.request().method()==='PATCH'){mailboxActive=route.request().postDataJSON().active;data={local_part:'inbox',active:mailboxActive,status:mailboxActive?'active':'suspended'}}
      else if(path.endsWith('/ssh-keys'))data={keys:[{comment:'Work laptop',type:'ED25519',bits:256,fingerprint:'SHA256:test-fixture-fingerprint'}]}
      else if(route.request().method()==='DELETE'){removed++;data={deleted:true}}
      await route.fulfill({json:data})
    })
    await page.goto('/app/email')
    await expect(page.getByRole('heading',{name:'Email Accounts',exact:true})).toBeVisible()
    await expect(page.getByRole('tab')).toHaveCount(0)
    await page.getByRole('button',{name:'Log in',exact:true}).click()
    await expect.poll(()=>page.evaluate(()=>window.__webmailSubmit)).toBeTruthy()
    const launch=await page.evaluate(()=>window.__webmailSubmit)
    expect(launch.method).toBe('post')
    expect(launch.target).toBe('_blank')
    expect(launch.action).not.toContain('fixture-one-use-token')
    expect(launch.fields._boron_token).toBe('fixture-one-use-token')
    expect(launch.fields._user).toBe('inbox@alpha.test')
    await page.getByRole('button',{name:'Suspend',exact:true}).click()
    await expect(page.getByRole('button',{name:'Unsuspend',exact:true})).toBeVisible()
    await expect(page.getByRole('button',{name:'Log in',exact:true})).toBeDisabled()
    await page.getByRole('button',{name:'Unsuspend',exact:true}).click()
    await expect(page.getByRole('button',{name:'Suspend',exact:true})).toBeVisible()
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
