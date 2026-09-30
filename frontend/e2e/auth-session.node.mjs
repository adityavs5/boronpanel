import { test } from 'node:test'
import assert from 'node:assert/strict'
import { build } from 'esbuild'
import { createRequire } from 'node:module'
import { runInNewContext } from 'node:vm'
import path from 'node:path'

const persisted = new Map()
globalThis.localStorage = {getItem: k => persisted.get(k) || null, setItem: (k, v) => persisted.set(k, v), removeItem: k => persisted.delete(k)}
const root = path.resolve(import.meta.dirname, '..')
async function fixture(api = {}) {
  const result = await build({
    stdin: { contents: "export {useAuth} from './src/store/auth.js'; export {queryClient} from './src/lib/queryClient.js'", resolveDir: root },
    bundle: true, write: false, format: 'cjs', platform: 'node', packages: 'external',
    plugins: [{ name: 'test-api', setup(b) {
      b.onResolve({ filter: /^@\/lib\/api$/ }, () => ({ path: 'api', namespace: 'mock' }))
      b.onLoad({ filter: /.*/, namespace: 'mock' }, () => ({ contents: `export const BASE_PATH='/app'; export const returnToAdmin=()=>globalThis.mockApi.returnToAdmin(); export const login=(...a)=>globalThis.mockApi.login(...a); export const logout=()=>globalThis.mockApi.logout(); export const whoami=()=>globalThis.mockApi.whoami(); export const loginVerify2fa=(...a)=>globalThis.mockApi.loginVerify2fa(...a);` }))
      b.onResolve({ filter: /^@\// }, args => ({ path: path.join(root, 'src', args.path.slice(2) + '.js') }))
    } }],
  })
  const module = { exports: {} }
  const storage = new Map()
  runInNewContext(result.outputFiles[0].text, {
    module, exports: module.exports, require: createRequire(import.meta.url), mockApi: api,
    console, setTimeout, clearTimeout,
    localStorage: { getItem: k => storage.get(k) || null, setItem: (k, v) => storage.set(k, v), removeItem: k => storage.delete(k) },
  })
  return module.exports
}

test('logout failure clears cached private data and identity', async () => {
  const { useAuth, queryClient } = await fixture({ logout: async () => { throw Error('offline') } })
  useAuth.setState({ role: 'reseller', username: 'first' })
  queryClient.setQueryData(['reseller-dashboard'], { secret: 'first' })
  await assert.rejects(useAuth.getState().logout())
  assert.equal(queryClient.getQueryData(['reseller-dashboard']), undefined)
  assert.equal(useAuth.getState().username, null)
})

test('login cancels old requests so late responses cannot refill the new session', async () => {
  const { useAuth, queryClient } = await fixture({ login: async () => ({ role: 'reseller', username: 'second' }) })
  let finish
  const pending = queryClient.fetchQuery({ queryKey: ['reseller-dashboard'], queryFn: () => new Promise(r => { finish = r }) }).catch(() => {})
  await useAuth.getState().login('second', 'test')
  finish({ secret: 'first' }); await pending
  assert.equal(queryClient.getQueryData(['reseller-dashboard']), undefined)
  assert.equal(useAuth.getState().username, 'second')
  queryClient.clear()
})

test('401 clearing and a changed server identity discard session data', async () => {
  const { useAuth, queryClient } = await fixture({ whoami: async () => ({ role: 'customer', username: 'new-user' }) })
  queryClient.setQueryData(['private'], 'old'); useAuth.getState().clear()
  assert.equal(queryClient.getQueryData(['private']), undefined)
  queryClient.setQueryData(['private'], 'old'); await useAuth.getState().syncIdentity()
  assert.equal(queryClient.getQueryData(['private']), undefined)
  assert.equal(useAuth.getState().username, 'new-user')
})
