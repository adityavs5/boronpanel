import { test, expect } from '@playwright/test'

async function fixture(page, skin, theme, role = 'customer') {
  await page.addInitScript(({skin,theme,role}) => {
    localStorage.setItem('boron.ui', JSON.stringify({state:{skin,theme},version:0}))
    localStorage.setItem('boron.auth', JSON.stringify({state:{role,username:'preview'},version:0}))
  }, {skin,theme,role})
  await page.route('**/api/**', route => {
    const path=new URL(route.request().url()).pathname
    let data={}
    if(path.endsWith('/whoami')) data={role,username:'preview',impersonating:role==='customer',impersonator:role==='customer'?'admin':undefined}
    else if(path.endsWith('/branding')) data={panel_name:'SiteCountry BoronPanel'}
    else if(path.endsWith('/onboarding')) data={completed:true}
    else if(path.endsWith('/domains')) data={domains:[{domain:'a-long-hosting-account.example.com',kind:'primary'}]}
    else if(path.endsWith('/accounts')) data=[]
    else if(path.endsWith('/apps')) data={apps:[]}
    else if(path.endsWith('/tasks')) data={tasks:[],kinds:[]}
    else if(path.endsWith('/imports')) data={imports:[]}
    return route.fulfill({json:data})
  })
}

for(const skin of ['evolution','paper-lantern']) for(const theme of ['light','dark']) {
  test(`shell keyboard, contrast and mobile geometry ${skin} ${theme}`,async({page})=>{
    const errors=[]; page.on('pageerror',e=>errors.push(e.message))
    await fixture(page,skin,theme)
    await page.goto('/app/dashboard')
    const search=page.getByRole('combobox',{name:'Search hosting tools'})
    await search.fill('mail')
    const options=page.getByRole('option')
    await expect(options).toHaveCount(6)
    // Force a scrollable popup so visibility is checked even for the shorter
    // customer result list (the administrator has nine matching tools).
    await page.locator('.dashboard-search-results').evaluate(el=>el.style.maxHeight='160px')
    for(let n=0;n<6;n++)await search.press('ArrowDown')
    const active=page.locator(`[id="${await search.getAttribute('aria-activedescendant')}"]`)
    await expect(active).toHaveAttribute('aria-selected','true')
    expect(await active.evaluate(el=>{const a=el.getBoundingClientRect(),b=el.parentElement.getBoundingClientRect();return a.top>=b.top&&a.bottom<=b.bottom})).toBe(true)
    await search.press('Escape')
    await expect(search).toHaveAttribute('aria-expanded','false')
    // The entire user colour range must yield readable header/text pairs.
    const ratios=await page.evaluate(({skin,theme})=>{
      const checks=[]
      for(const color of ['#bc942c','#307bb8','#ffffff','#000000','#ffcc00']) {
        window.BoronAppearance.apply({colors:{[skin]:color}},skin,theme==='dark')
        const css=getComputedStyle(document.documentElement), token=k=>css.getPropertyValue('--pref-'+k).trim().split(' ').map(Number)
        checks.push(window.BoronAppearance.contrast(token('group'),[255,255,255]))
        for(const bg of theme==='dark'?[[30,38,49],[37,47,60]]:[[255,255,255],[244,246,249]])checks.push(window.BoronAppearance.contrast(token('link'),bg))
      }
      window.BoronAppearance.apply({},skin,theme==='dark');return checks
    },{skin,theme})
    expect(Math.min(...ratios)).toBeGreaterThanOrEqual(4.5)
    await page.goto('/app/appearance')
    for(const width of [1440,768,375]) {
      await page.setViewportSize({width,height:900})
      await expect(page.getByRole('heading',{name:'Appearance',exact:true})).toBeVisible()
      const geometry=await page.evaluate(()=>{const logo=document.querySelector('.panel-brand').getBoundingClientRect(),search=document.querySelector('.global-search-trigger').getBoundingClientRect(),banner=document.querySelector('.impersonation-banner')?.getBoundingClientRect();return {overflow:document.documentElement.scrollWidth>innerWidth,overlap:Math.min(logo.right,search.right)-Math.max(logo.left,search.left),bannerHeight:banner?.height||0}})
      expect(geometry.overflow).toBe(false);expect(geometry.overlap).toBeLessThanOrEqual(0);expect(geometry.bannerHeight).toBeLessThanOrEqual(120)
    }
    expect(errors).toEqual([])
  })
}

test('structural breadcrumbs do not navigate to missing routes; Tasks is in navigation',async({page})=>{
  await fixture(page,'evolution','light','admin')
  for(const [path,heading] of [['software/node-apps','Software'],['import/accounts','Import']]){
    await page.goto('/app/'+path)
    const crumbs=page.getByRole('navigation',{name:'Breadcrumb'})
    await expect(crumbs.getByText(heading,{exact:true})).toBeVisible()
    await expect(crumbs.getByRole('link',{name:heading,exact:true})).toHaveCount(0)
  }
  await page.getByRole('textbox',{name:'Search navigation'}).fill('tasks')
  await expect(page.getByRole('link',{name:'Tasks',exact:true})).toBeVisible()
})

test('File Manager adapter preserves selection and names controls without disabling zoom',async({page})=>{
  await page.goto('/app/login')
  const items=Array.from({length:20},(_,index)=>`<a class="listing-item" role="button" tabindex="0" aria-selected="false">Folder ${index+1}</a>`).join('')
  await page.setContent(`<html><head><meta name="viewport" content="width=device-width,user-scalable=no"></head><body><div class="listing-items">${items}</div><input type="range" id="gallery-size"><span class="size-label" data-v-upstream="">Size</span></body></html>`)
  // Upstream scoped CSS is more specific and may load after our adapter.
  await page.addStyleTag({url:'/static/dist/filebrowser-accessibility.css'})
  await page.addStyleTag({content:':root{--primaryColor:rgb(33,150,243)}.size-label[data-v-upstream]{font-size:11.9px}.listing-items{height:120px;overflow:auto}.listing-item{display:block;height:36px;background:white;color:black}.listing-item[aria-selected=true]{background-color:color-mix(in srgb,var(--primaryColor) 85%,transparent)!important;color:white!important;scroll-margin-top:10px;scroll-margin-bottom:10px}'})
  expect(await page.locator('.size-label').evaluate(el=>parseFloat(getComputedStyle(el).fontSize))).toBeGreaterThanOrEqual(12)
  await page.addScriptTag({url:'/static/dist/filebrowser-accessibility.js'})
  await expect(page.locator('#gallery-size')).toHaveAccessibleName('File preview size')
  // Preserve the installed upstream keyboard contract, including its delayed
  // selector lookup. The adapter must not consume or replace this attribute.
  await page.evaluate(()=>{
    document.querySelector('.listing-items').addEventListener('keydown',event=>{
      if(event.key!=='ArrowDown')return
      event.preventDefault()
      const items=[...document.querySelectorAll('.listing-item')]
      const index=items.findIndex(item=>item.getAttribute('aria-selected')==='true')
      items.forEach((item,i)=>item.setAttribute('aria-selected',String(i===Math.min(index+1,items.length-1))))
      setTimeout(()=>document.querySelector('.listing-item[aria-selected="true"]')?.scrollIntoView({behavior:'smooth',block:'nearest',inline:'nearest'}),50)
    })
  })
  const first=page.getByRole('button',{name:'Folder 1',exact:true})
  await expect(first).toHaveAttribute('aria-selected','false')
  await first.focus()
  for(let index=0;index<20;index++)await page.keyboard.press('ArrowDown')
  const selected=page.locator('.listing-item[aria-selected="true"]')
  await expect(selected).toHaveCount(1)
  await expect(selected).toHaveText('Folder 20')
  await expect(selected).toHaveCSS('color','rgb(255, 255, 255)')
  // The installed upstream rule above loads last and is !important. Selected
  // text must remain readable even when the primary accent is pure white.
  for(const accent of ['rgb(33,150,243)','#ffffff']){
    const contrast=await selected.evaluate((el,accent)=>{
      document.documentElement.style.setProperty('--primaryColor',accent)
      const canvas=document.createElement('canvas'),context=canvas.getContext('2d')
      canvas.width=canvas.height=1
      const rgba=color=>{context.clearRect(0,0,1,1);context.fillStyle=color;context.fillRect(0,0,1,1);return [...context.getImageData(0,0,1,1).data]}
      const style=getComputedStyle(el),background=rgba(style.backgroundColor),foreground=rgba(style.color)
      const luminance=color=>color.slice(0,3).map(value=>{value/=255;return value<=.04045?value/12.92:((value+.055)/1.055)**2.4}).reduce((total,value,index)=>total+value*[.2126,.7152,.0722][index],0)
      const a=luminance(background),b=luminance(foreground)
      return {ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05),alpha:background[3]}
    },accent)
    expect(contrast.alpha).toBe(255)
    expect(contrast.ratio).toBeGreaterThanOrEqual(4.5)
  }
  await expect(first).toHaveCSS('background-color','rgb(255, 255, 255)')
  await expect.poll(()=>page.locator('.listing-items').evaluate(el=>el.scrollTop)).toBeGreaterThan(0)
  await expect.poll(()=>selected.evaluate(el=>{const a=el.getBoundingClientRect(),b=el.parentElement.getBoundingClientRect();return a.top>=b.top&&a.bottom<=b.bottom})).toBe(true)
  await expect(page.locator('.listing-item[aria-pressed]')).toHaveCount(0)
  await expect(page.locator('meta[name=viewport]')).not.toHaveAttribute('content',/user-scalable=no/)
})
