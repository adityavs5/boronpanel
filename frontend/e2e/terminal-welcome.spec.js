import {test,expect} from '@playwright/test'
for(const skin of ['evolution','paper-lantern']) for(const mode of ['light','dark']) {
 test(`${skin} ${mode}: customize and reset terminal welcome`,async({page},info)=>{
  await page.addInitScript(({skin,mode})=>{
   localStorage.setItem('boron.ui',JSON.stringify({state:{skin,theme:mode},version:0}))
   localStorage.setItem('boron.auth',JSON.stringify({state:{role:'admin',username:'admin'},version:0}))
  },{skin,mode})
  let banner=null,requests=[]
  await page.route('**/api/**',async route=>{
   const p=new URL(route.request().url()).pathname;let data={}
   if(p.endsWith('/whoami'))data={role:'admin',username:'admin'}
   else if(p.endsWith('/onboarding'))data={completed:true}
   else if(p==='/api/v1/admin/branding'){
    if(route.request().method()==='PATCH'){const body=route.request().postDataJSON();requests.push(body);banner=body.terminal_banner}
    data={terminal_banner:banner,default_terminal_banner:'BBBB  OOO  RRRR  OOO  N  N'}
   }
   else if(p==='/api/v1/branding')data={panel_name:'Boron',support_email:null,support_url:null}
   await route.fulfill({json:data})
  })
  await page.goto('/app/branding')
  await page.getByLabel('Terminal banner',{exact:true}).fill('BORON TEST\n$(literal text)')
  await expect(page.getByLabel('Terminal banner preview')).toHaveText('BORON TEST\n$(literal text)')
  await page.getByRole('button',{name:'Save terminal welcome',exact:true}).click()
  await expect.poll(()=>requests.length).toBe(1)
  expect(requests[0]).toEqual({terminal_banner:'BORON TEST\n$(literal text)'})
  await page.getByRole('button',{name:'Reset to BORON',exact:true}).click()
  await expect(page.getByLabel('Terminal banner',{exact:true})).toHaveValue('BBBB  OOO  RRRR  OOO  N  N')
  expect(requests[1]).toEqual({terminal_banner:null})
  while(await page.getByRole('button',{name:'Dismiss notification',exact:true}).count())await page.getByRole('button',{name:'Dismiss notification',exact:true}).first().click()
  await page.setViewportSize({width:390,height:844})
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  await page.screenshot({path:info.outputPath(`${skin}-${mode}-terminal-branding.png`),fullPage:true})
 })
}
