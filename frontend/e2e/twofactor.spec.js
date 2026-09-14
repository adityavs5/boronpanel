import {test,expect} from '@playwright/test'
for(const role of ['admin','customer']) for(const skin of ['evolution','paper-lantern']) for(const theme of ['light','dark']) {
 test(`${role} ${skin} ${theme}: enroll and disable 2FA`,async({page},info)=>{
  let enabled=false,submitted=null
  const errors=[];page.on('pageerror',error=>errors.push(error.message))
  await page.addInitScript(({role,skin,theme})=>{
   localStorage.setItem('boron.ui',JSON.stringify({state:{skin,theme},version:0}))
   localStorage.setItem('boron.auth',JSON.stringify({state:{role,username:'qa'},version:0}))
  },{role,skin,theme})
  await page.route('**/api/**',async route=>{
   const path=new URL(route.request().url()).pathname;let data={}
   if(path.endsWith('/whoami'))data={role,username:'qa'}
   else if(path.endsWith('/onboarding'))data={completed:true}
   else if(path.endsWith('/2fa/status'))data={enabled}
   else if(path.endsWith('/2fa/setup'))data={secret:'JBSWY3DPEHPK3PXP',otpauth_uri:'otpauth://totp/Boron:qa?secret=JBSWY3DPEHPK3PXP',qr_data_uri:'data:image/svg+xml;base64,'+Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="176" height="176"><rect width="176" height="176" fill="black"/></svg>').toString('base64')}
   else if(path.endsWith('/2fa/verify')){submitted=route.request().postDataJSON();enabled=true;data={enabled,recovery_codes:Array.from({length:8},(_,i)=>`ABCDE-0000${i}`)}}
   else if(path.endsWith('/2fa/disable')){expect(route.request().postDataJSON()).toEqual({current_password:'qa-password'});enabled=false;data={enabled}}
   await route.fulfill({json:data})
  })
  await page.goto('/app/security')
  await page.getByRole('button',{name:'Enable 2FA',exact:true}).click()
  await expect(page.getByRole('img',{name:'2FA setup QR code'})).toBeVisible()
  await page.setViewportSize({width:390,height:844})
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  await page.getByRole('textbox',{name:/Verification code/}).fill('123456')
  await page.getByRole('button',{name:'Verify and enable',exact:true}).click()
  await expect(page.getByText('ABCDE-00000',{exact:true})).toBeVisible()
  expect(submitted).toEqual({code:'123456'})
  await page.screenshot({path:info.outputPath(`${role}-${skin}-${theme}-recovery.png`),fullPage:true})
  await page.getByRole('button',{name:'I have saved my codes',exact:true}).click()
  await expect(page.getByText('ABCDE-00000',{exact:true})).not.toBeVisible()
  await page.getByRole('button',{name:'Disable 2FA',exact:true}).first().click()
  const dialog=page.getByRole('dialog',{name:'Disable two-factor authentication?'})
  await expect(dialog.getByRole('button',{name:'Disable 2FA',exact:true})).toBeDisabled()
  await dialog.getByLabel('Current password',{exact:false}).fill('qa-password')
  await dialog.getByRole('button',{name:'Disable 2FA',exact:true}).click()
  await expect(page.getByRole('button',{name:'Enable 2FA',exact:true})).toBeVisible()
  expect(errors).toEqual([])
 })
}
