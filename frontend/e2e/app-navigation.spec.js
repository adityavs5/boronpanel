import { test, expect } from '@playwright/test'
for (const skin of ['modern', 'classic', 'grid']) {
  test(`${skin}: separate Python and Node.js app workflows`, async ({page}) => {
    await page.addInitScript(skin=>{
      localStorage.setItem('boron.public-branding.v1',JSON.stringify({panel_name:'Boron',theme:skin}));localStorage.setItem('boron.ui',JSON.stringify({state:{skin,theme:'light'},version:0}))
      localStorage.setItem('boron.auth',JSON.stringify({state:{role:'customer',username:'hostingdemo'},version:0}))
    },skin)
    await page.route('**/api/**',async route=>{
      const path=new URL(route.request().url()).pathname
      const data=path.endsWith('/whoami')?{role:'customer',username:'hostingdemo'}:path.endsWith('/onboarding')?{completed:true}:path.endsWith('/domains')?{domains:[]}:{apps:[]}
      await route.fulfill({json:data})
    })
    for (const [query,label,route] of [['django','Python apps','python-apps'],['nodejs','Node.js apps','node-apps']]) {
      await page.goto('/app/dashboard')
      if(skin==='grid')await page.getByRole('button',{name:'Search the panel'}).click()
      await page.getByRole('combobox',{name:skin==='grid'?'Search tools and settings':'Search hosting tools'}).fill(query)
      await page.getByRole('option').filter({hasText:label}).click()
      await expect(page).toHaveURL(new RegExp('/'+route+'$'))
      await expect(page.getByRole('heading',{name:label,exact:true})).toBeVisible()
      await expect(page.getByRole('tab')).toHaveCount(0)
      await page.getByRole('button',{name:`Create ${label==='Python apps'?'Python':'Node.js'} app`,exact:true}).click()
      await expect(page.getByRole('dialog')).toBeVisible()
      await expect(page.getByRole('heading',{name:`Create ${label==='Python apps'?'Python':'Node.js'} application`,exact:true})).toBeVisible()
    }
  })
}
