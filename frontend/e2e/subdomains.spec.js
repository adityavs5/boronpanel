import {test,expect} from '@playwright/test'
for(const skin of ['evolution','paper-lantern']) for(const mode of ['light','dark']) {
 test(`${skin} ${mode}: create a subdomain as an independent site`,async({page},info)=>{
  await page.addInitScript(({skin,mode})=>{
   localStorage.setItem('boron.ui',JSON.stringify({state:{skin,theme:mode},version:0}))
   localStorage.setItem('boron.auth',JSON.stringify({state:{role:'customer',username:'alpha'},version:0}))
  },{skin,mode})
  let request=null
  await page.route('**/api/**',async route=>{
   const p=new URL(route.request().url()).pathname;let data={}
   if(p.endsWith('/whoami'))data={role:'customer',username:'alpha'}
   else if(p.endsWith('/onboarding'))data={completed:true}
   else if(p.endsWith('/domains')){
    if(route.request().method()==='POST'){request=route.request().postDataJSON();data={...request,docroot:'/home/alpha/blog.example.com/public_html',dns_record_created:true}}
    else data={domains:[{domain:'example.com',kind:'primary',docroot:'/home/alpha/public_html'}]}
   }
   else if(p.endsWith('/parked-domains'))data={parked_domains:[]}
   await route.fulfill({json:data})
  })
  await page.goto('/app/domains')
  await page.getByRole('button',{name:'Add domain',exact:true}).first().click()
  const dialog=page.getByRole('dialog')
  await dialog.getByLabel('Site type',{exact:true}).selectOption('subdomain')
  await expect(dialog.getByLabel('Parent domain',{exact:true})).toHaveValue('example.com')
  await dialog.getByRole('textbox',{name:/^Subdomain required$/}).fill('blog')
  await expect(dialog.getByText('blog.example.com',{exact:true})).toBeVisible()
  await page.setViewportSize({width:390,height:844})
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  await page.screenshot({path:info.outputPath(`${skin}-${mode}-subdomain.png`),fullPage:true})
  await dialog.getByRole('button',{name:'Add subdomain',exact:true}).click()
  await expect(dialog).not.toBeVisible()
  expect(request).toEqual({domain:'blog.example.com',kind:'subdomain',parent_domain:'example.com',document_root_mode:'default'})
 })
}
