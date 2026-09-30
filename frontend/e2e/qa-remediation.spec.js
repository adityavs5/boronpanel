import {test,expect} from '@playwright/test'

async function fixture(page,skin,theme='light') {
  const paths=[];const plans=[]
  const account={id:1,username:'alpha',status:'active',uid:2000,gid:2000,primary_domain:'example.com',php_version:'8.3',email:'owner@example.com',cpu_pct:22200,cpu_cores:12,configured_cpu_cores:222,host_cpu_cores:12,host_memory_gb:7.7,mem_mb:1024,io_mb:50,pids_max:100,quota_soft_mb:10240,quota_hard_mb:12288}
  await page.addInitScript(({skin,theme})=>{
    localStorage.setItem('boron.ui',JSON.stringify({state:{skin,theme},version:0}))
    localStorage.setItem('boron.auth',JSON.stringify({state:{role:'admin',username:'admin'},version:0}))
  },{skin,theme})
  await page.route('**/api/**',async route=>{
    const req=route.request(),p=new URL(req.url()).pathname;paths.push(p)
    let data={}
    if(p.endsWith('/whoami')) data={role:'admin',username:'admin'}
    else if(p.endsWith('/branding')) data={panel_name:'QA Branded Panel'}
    else if(p.endsWith('/onboarding')) data={completed:true}
    else if(p==='/api/v1/accounts') data=[account]
    else if(p==='/api/v1/accounts/alpha') data=account
    else if(p==='/api/v1/admin/resources') data={host:{cpu_cores:12,memory_gb:7.7},users:[],policies:[]}
    else if(p==='/api/v1/admin/plans'&&req.method()==='POST') {plans.push(req.postDataJSON());data={id:1,...plans.at(-1)}}
    else if(p==='/api/v1/admin/plans') data={plans:[]}
    else if(p.endsWith('/alpha/domains')) data={domains:[{id:1,domain:'example.com',kind:'primary',docroot:'/home/alpha/public_html',ssl_status:'issued'}]}
    else if(p.endsWith('/alpha/ssl')) data={domains:[{domain:'example.com',cert_status:'valid',issuer:'QA issuer'}]}
    else if(p==='/api/v1/admin/ssl') data={domains:[{domain:'foreign.example.com',cert_status:'valid'},{domain:'panel.example.com',service:'panel',cert_status:'valid'}]}
    else if(p.endsWith('/parked-domains')) data={parked_domains:[]}
    else if(p.endsWith('/namespace')) data={enabled:true}
    else if(p.endsWith('/usage')) data={resources:{},current:{}}
    else if(p.endsWith('/alerts')) data={active:[]}
    await route.fulfill({json:data})
  })
  return {paths,plans,account}
}

for(const skin of ['evolution','paper-lantern']) {
 test(`${skin}: every hosting template is valid and saves`,async({page})=>{
   test.setTimeout(90000)
   const {plans}=await fixture(page,skin)
   await page.goto('/app/plans/new')
   await expect(page.getByLabel('Starting template',{exact:true}).locator('option')).toHaveCount(4)
   const options=await page.getByLabel('Starting template',{exact:true}).locator('option').evaluateAll(nodes=>nodes.map(node=>node.value))
   for(const id of options) {
     await page.goto('/app/plans/new')
     await page.getByLabel('Starting template',{exact:true}).selectOption(id)
     await page.getByLabel('Plan name',{exact:true}).fill(`QA ${id}`)
     expect(await page.locator('form.plan-editor').evaluate(form=>form.checkValidity())).toBe(true)
     await page.getByRole('button',{name:'Save plan',exact:true}).first().click()
     await expect(page).toHaveURL(/\/app\/plans$/)
   }
   expect(plans).toHaveLength(4)
   expect(plans.every(p=>Number.isInteger(p.memory_high_mb)&&p.memory_high_mb<=p.memory_max_mb)).toBe(true)
 })
 test(`${skin}: admin domain and certificate controls retain account scope`,async({page})=>{
   test.setTimeout(90000)
   const {paths}=await fixture(page,skin)
   await page.goto('/app/accounts/alpha?tab=identity')
   await expect(page.getByLabel('Contact email',{exact:true})).toHaveValue('owner@example.com')
   await page.goto('/app/accounts/alpha?tab=overview')
   await expect(page.getByLabel('CPU cores',{exact:true})).toHaveValue('12')
   await expect(page.getByText(/old allocation was 222 cores/)).toBeVisible()
   await page.goto('/app/accounts/alpha?tab=ssl')
   await expect(page.getByRole('heading',{name:'SSL/TLS',exact:true})).toBeVisible()
   expect(paths).toContain('/api/v1/accounts/alpha/ssl')
   expect(paths).not.toContain('/api/v1/admin/ssl')
   await expect(page.getByText('foreign.example.com',{exact:true})).toHaveCount(0)
   await page.goto('/app/accounts/alpha?tab=domains')
   await page.getByRole('row').filter({hasText:'example.com'}).click()
   await expect(page).toHaveURL(/\/app\/accounts\/alpha\/domains\/example\.com$/)
   await expect(page.getByRole('heading',{name:'example.com',exact:true})).toBeVisible()
   await page.setViewportSize({width:390,height:844})
   expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
 })
}
