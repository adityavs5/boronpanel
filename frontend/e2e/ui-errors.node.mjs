import {test} from 'node:test'
import assert from 'node:assert/strict'
import {normalizeError,humanErrorMessage,api,registerAuthHandlers} from '../src/lib/api.js'

test('validation fields use the displayed units and keep actionable domain errors',()=>{
 const error=normalizeError({response:{status:422,data:{detail:[{loc:['body','mem_mb'],msg:'mem_mb must be between 64 and 65536'},{loc:['body','bandwidth_limit_mb'],msg:'bandwidth_limit_mb must be at least 1 (use empty/None)'}]}}})
 assert.equal(error.status,422)
 assert.equal(error.fields.mem_mb,'Memory must be between 0.0625 and 64 GB.')
 assert.match(error.fields.bandwidth_limit_mb,/in GB/)
 assert.equal(humanErrorMessage('CNAME records cannot be created at the zone apex.'),'CNAME records cannot be created at the zone apex.')
})
test('internal and HTML failures are redacted but preserve server correlation IDs',()=>{
 for(const detail of ['internal operation failure','Traceback: SELECT password FROM accounts','<!doctype html><html>proxy failure</html>']){
  const error=normalizeError({response:{status:502,headers:{'x-boron-error-reference':'abcdef0123456789'},data:{detail}}})
  assert.match(error.message,/check the Error Log/)
  assert.match(error.message,/reference: abcdef0123456789/)
  assert.equal(error.reference,'abcdef0123456789')
  assert.doesNotMatch(error.message,/SELECT password|doctype|internal operation failure/)
 }
 const body=normalizeError({response:{status:502,data:{detail:'internal operation failure (reference: 0123456789abcdef)'}}})
 assert.match(body.message,/reference: 0123456789abcdef/)
 assert.match(humanErrorMessage('internal operation failure (reference: 0123456789abcdef)'),/reference: 0123456789abcdef/)
})
test('network errors do not fabricate server IDs',()=>{
 const error=normalizeError({message:'Network Error'})
 assert.equal(error.reference,null)
 assert.equal(error.message,'Cannot reach the server. Check your connection.')
})
test('the compatible Axios update preserves credentials and authorization interceptors',async()=>{
 assert.equal(api.defaults.withCredentials,true)
 let expired=0,maintenance=0
 registerAuthHandlers({unauthorized:()=>expired++,maintenance:()=>maintenance++})
 for(const status of [401,503])await assert.rejects(api.get('/test',{adapter:()=>Promise.reject({response:{status,data:{detail:'session expired'}}})}),error=>error.status===status)
 assert.equal(expired,1);assert.equal(maintenance,1)
})
