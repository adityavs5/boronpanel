import {test,expect} from '@playwright/test'
import {searchEntries} from '../src/config/search.js'
test('search understands aliases, mixed case and typos without exposing absent tools',()=>{
 const entries=[{label:'DNS Management',to:'/dns'},{label:'WordPress Manager',to:'/wordpress'},{label:'Databases',to:'/databases'}]
 for(const query of ['DNS zone edito','zone editor','NAMESERVERS','dns managment','how can I add DNS records']) expect(searchEntries(entries,query)[0]?.to).toBe('/dns')
 for(const query of ['Softaculous','wp plugins','Wordpres']) expect(searchEntries(entries,query)[0]?.to).toBe('/wordpress')
 expect(searchEntries(entries,'MySQL')[0]?.to).toBe('/databases')
 expect(searchEntries(entries,'accounts')).toEqual([])
 expect(searchEntries(entries,'unrelated nonsense')).toEqual([])
})
