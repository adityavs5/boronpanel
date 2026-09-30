import {test,expect} from '@playwright/test'
for(const skin of ['evolution','paper-lantern'])for(const theme of ['light','dark']){
 test(`${skin} ${theme}: advanced DNS requires preview and zone confirmation`,async({page})=>{
  await page.addInitScript(({skin,theme})=>{
   localStorage.setItem('boron.ui',JSON.stringify({state:{skin,theme},version:0}))
   localStorage.setItem('boron.auth',JSON.stringify({state:{role:'customer',username:'alpha'},version:0}))
  },{skin,theme})
  const fingerprint='a'.repeat(64),initial='$ORIGIN example.com.\n@ 3600 IN A 192.0.2.1\n'
  let applied=null,previewed=null
  await page.route('**/api/**',async route=>{
   const req=route.request(),path=new URL(req.url()).pathname;let data={}
   if(path.endsWith('/whoami'))data={role:'customer',username:'alpha'}
   else if(path.endsWith('/domains'))data={domains:[{domain:'example.com',kind:'primary'}]}
   else if(path.endsWith('/records'))data={zone:'example.com',managed:true,provider:'local',records:[{name:'example.com',type:'A',ttl:3600,values:['192.0.2.1']}]}
   else if(path.endsWith('/advanced/preview')){previewed=req.postDataJSON();data={zone:'example.com',fingerprint,changes:[{name:'example.com',type:'A',action:'replace',before:['192.0.2.1'],after:['192.0.2.2']}]}}
   else if(path.endsWith('/advanced')){
    if(req.method()==='PUT'){applied=req.postDataJSON();data={zone:'example.com',changed:1}}
    else data={zone:'example.com',text:initial,fingerprint,templates:[{id:'hosting',name:'Website, email and FTP records'}]}
   }
   await route.fulfill({json:data})
  })
  await page.goto('/app/dns')
  await page.getByRole('button',{name:'Zone editor and templates'}).click()
  const dialog=page.getByRole('dialog'),save=dialog.getByRole('button',{name:'Apply DNS changes',exact:true})
  await expect(save).toBeDisabled()
  await dialog.getByLabel('Editable zone records',{exact:true}).fill(initial.replace('192.0.2.1','192.0.2.2'))
  await dialog.getByRole('button',{name:'Preview changes',exact:true}).click()
  await expect(dialog.getByText('1 record sets will change',{exact:true})).toBeVisible()
  expect(previewed.fingerprint).toBe(fingerprint)
  await dialog.getByLabel('Confirm zone name',{exact:true}).fill('wrong.example.com')
  await expect(save).toBeDisabled()
  await dialog.getByLabel('Confirm zone name',{exact:true}).fill('example.com')
  await expect(save).toBeEnabled()
  await page.setViewportSize({width:390,height:844})
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  await save.click();await expect(dialog).not.toBeVisible()
  expect(applied).toEqual({text:initial.replace('192.0.2.1','192.0.2.2'),fingerprint,confirmation:'example.com'})
 })
}
