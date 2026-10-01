import assert from 'node:assert/strict'
import {dnsOwnerPreview} from '../src/lib/dns-owner.js'
assert.deepEqual(dnsOwnerPreview('example.com','@'),{fqdn:'example.com',confirmRelative:false})
assert.deepEqual(dnsOwnerPreview('example.com','www.example.com.'),{fqdn:'www.example.com',confirmRelative:false})
assert.equal(dnsOwnerPreview('example.com','x.other-zone.com').confirmRelative,true)
assert.equal(dnsOwnerPreview('example.com','x.other-zone.com').fqdn,'x.other-zone.com.example.com')
assert.ok(dnsOwnerPreview('example.com','x.other-zone.com.').error)
for(const name of ['_sip._tcp','default._domainkey','dev.app'])assert.equal(dnsOwnerPreview('example.com',name).fqdn,`${name}.example.com`)
console.log('DNS owner preview contracts passed.')
