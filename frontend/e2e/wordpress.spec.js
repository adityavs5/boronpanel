import { test, expect } from '@playwright/test'

for (const skin of ['evolution','paper-lantern']) {
  test(`${skin}: WordPress install, management, backups, clone and related search`, async ({page},info) => {
    const errors=[];page.on('pageerror', e=>errors.push(e.message))
    await page.addInitScript(skin=>{
      localStorage.setItem('boron.ui',JSON.stringify({state:{skin,theme:'light'},version:0}))
      localStorage.setItem('boron.auth',JSON.stringify({state:{role:'customer',username:'hostingdemo'},version:0}))
    },skin)
    const requests=[];let lastAction='';let installed=false
    await page.route('**/api/**',async route=>{
      const p=new URL(route.request().url()).pathname;let data={}
      if(p.endsWith('/whoami')) data={role:'customer',username:'hostingdemo'}
      else if(p.endsWith('/onboarding')) data={completed:true}
      else if(p==='/api/v1/wordpress') data={installs:[{id:'example.com',username:'hostingdemo',domain:'example.com',path:'',wp_version:'6.8',admin_user:'siteadmin',url:'https://example.com'}],domains:[{username:'hostingdemo',domain:'example.com'},{username:'hostingdemo',domain:'clone.example.com'}],errors:[]}
      else if(p.endsWith('/wordpress/scan')) data={found:1,errors:[]}
      else if(p.endsWith('/wordpress/refresh')) data={status:'refreshed'}
      else if(p.endsWith('/wordpress') && route.request().method()==='POST') {installed=true;requests.push(route.request().postDataJSON());data={id:88,status:'pending'}}
      else if(p.endsWith('/jobs/88')) data={id:88,status:'completed'}
      else if(p.endsWith('/manage') || p.endsWith('/actions')) {const body=route.request().postDataJSON();requests.push(body);lastAction=body.action;data={id:requests.length,status:'pending'}}
      else if(p.includes('/actions/runs/')) data={id:Number(p.split('/').at(-1)),status:'completed',stdout:JSON.stringify(lastAction==='backups'?{backups:[]}:lastAction==='backup'?{name:'20260913-120000-aabbccdd.tar.gz',size:1048576,created_at:1789300800}:lastAction==='plugin_list'?[{name:'akismet',version:'5.4',status:'inactive',update:'none'}]:{message:'Done',...(lastAction==='clone'?{url:'https://clone.example.com'}:{})})}
      else if(p.endsWith('/domains')) data={domains:[{domain:'example.com',is_primary:true}]}
      else if(p.endsWith('/usage')) data={current:{}}
      await route.fulfill({json:data})
    })
    await page.goto('/app/dashboard')
    await page.getByRole('textbox',{name:'Filter tools'}).fill('DNS zone edito')
    await expect(page.locator('.tool-link').filter({hasText:'DNS'})).toBeVisible()
    await page.getByRole('textbox',{name:'Filter tools'}).fill('softaculous')
    await page.locator('.tool-link').filter({hasText:'WordPress'}).click()
    await expect(page.getByRole('heading',{name:'WordPress Manager',exact:true})).toBeVisible()
    await page.getByRole('button',{name:'Scan for installations'}).click()
    await expect(page.getByText('Scan complete. 1 WordPress installation refreshed.')).toBeVisible()
    await page.getByRole('button',{name:'Refresh example.com',exact:true}).click()
    await expect(page.getByText('example.com refreshed from WordPress.')).toBeVisible()
    await page.screenshot({path:info.outputPath(`${skin}-wordpress.png`),fullPage:true})
    await page.getByRole('button',{name:'Install WordPress',exact:true}).click()
    await page.getByRole('combobox',{name:'Installation domain',exact:true}).selectOption('hostingdemo:clone.example.com')
    await page.getByRole('combobox',{name:'Website address format'}).selectOption('http-www')
    await page.getByRole('button',{name:'Continue'}).click()
    await page.getByRole('textbox',{name:'Website name',exact:true}).fill('My new website')
    await page.getByRole('textbox',{name:'Administrator email',exact:true}).fill('owner@example.com')
    await page.getByRole('button',{name:'Continue'}).click()
    await page.screenshot({path:info.outputPath(`${skin}-install.png`),fullPage:true})
    await page.getByRole('dialog').getByRole('button',{name:'Install WordPress',exact:true}).click()
    await expect(page.getByRole('heading',{name:'Your website is ready'})).toBeVisible()
    expect(installed).toBe(true);expect(requests[0].protocol).toBe('http');expect(requests[0].use_www).toBe(true);expect(requests[0].admin_password.length).toBeGreaterThanOrEqual(20)
    await page.getByRole('button',{name:'Manage my website'}).click()
    await page.getByRole('button',{name:'Manage website',exact:true}).click()
    await page.getByRole('tab',{name:'Plugins',exact:true}).click()
    await expect(page.getByText('akismet',{exact:true})).toBeVisible()
    await page.getByRole('button',{name:'Activate',exact:true}).click()
    await expect(page.getByRole('button',{name:'Done',exact:true})).toBeEnabled()
    await page.getByRole('tab',{name:'Backups',exact:true}).click()
    await expect(page.getByRole('button',{name:'Back up now'})).toBeEnabled()
    await page.getByRole('button',{name:'Back up now'}).click()
    await expect(page.getByRole('button',{name:'Restore',exact:true})).toBeVisible()
    await page.getByRole('button',{name:'Restore',exact:true}).click()
    await expect(page.getByText('This replaces your current website files and database.',{exact:false})).toBeVisible()
    await page.getByRole('button',{name:'Cancel',exact:true}).click()
    expect(requests.some(r=>r.action==='restore')).toBe(false)
    await page.getByRole('tab',{name:'Clone site',exact:true}).click()
    await page.getByRole('combobox',{name:'Clone destination',exact:true}).selectOption('clone.example.com')
    await page.getByRole('textbox',{name:'Clone folder',exact:true}).fill('')
    await page.getByRole('button',{name:'Clone website',exact:true}).click()
    await expect(page.getByRole('link',{name:'Open cloned website'})).toHaveAttribute('href','https://clone.example.com')
    await page.getByRole('button',{name:'Done',exact:true}).click()
    await page.getByRole('button',{name:'Manage website',exact:true}).click()
    await page.getByRole('tab',{name:'Remove',exact:true}).click()
    await expect(page.getByRole('combobox',{name:'Removal type'})).toHaveValue('soft')
    await page.getByRole('combobox',{name:'Removal type'}).selectOption('hard')
    await expect(page.getByRole('button',{name:'Permanently delete installation'})).toBeDisabled()
    await page.getByRole('textbox',{name:'Confirm installation address'}).fill('wrong.example.com')
    await expect(page.getByRole('button',{name:'Permanently delete installation'})).toBeDisabled()
    await page.getByRole('textbox',{name:'Confirm installation address'}).fill('example.com')
    await expect(page.getByRole('button',{name:'Permanently delete installation'})).toBeEnabled()
    await page.getByRole('button',{name:'Done',exact:true}).click()
    for(const width of [320,390,768]){
      await page.setViewportSize({width,height:844})
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
      await page.getByRole('button',{name:'Manage website',exact:true}).click()
      expect(await page.getByRole('dialog').evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true)
      await page.getByRole('button',{name:'Done',exact:true}).click()
    }
    expect(errors).toEqual([])
  })
}
