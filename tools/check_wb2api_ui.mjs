#!/usr/bin/env node
/**
 * dsh-plugin-wb2api-ui 综合自检。
 *
 * 不碰付费上游、不写真 auths/、不启浏览器，四层从弱到强：
 *   ① 宿主模块：加载 lib/index.js，校验导出面；
 *   ② 路由：mock webServer 走真 apply()，13 条 /dsh-wb2api/* 全挂载；
 *      实测 /state 全链路（只碰本机网关）+ 405 语义 + 写路由的「会拒绝」路径
 *      （路径穿越 / 缺 raw / 坏 JSON / 缺 state）—— 不测「会写入」；
 *   ③ 纯函数单元：normalizeDocument / toUnixSeconds / inferRealm /
 *      classifyCheckin / tokenIssuerOrigin / localDay；
 *   ④ 浏览器半侧：stub react 把 Panel 从 loading 渲染到 ready，校验关键文案。
 *      createElement 带 type 校验 —— `h(样式对象, …)` 这类白屏元凶当场抛错
 *      （2026-09-27 白屏 bug 的教训）；文案提取用 children 递归收集，
 *      旧冒烟测试「props.children 提取恒空」的坑已修。
 *
 * 纪律：脚本内置 WB2API_NO_AUTO_ACTIVITY=1；/credits、/growth、/checkin、
 * /keepalive、/login/* 这些会碰上游或真写凭证的路由一律不进用例。
 */
process.env.WB2API_NO_AUTO_ACTIVITY = '1'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

const assert = (cond, msg) => { if (!cond) { console.error('FAIL: ' + msg); process.exitCode = 1 } else { console.log('PASS: ' + msg) } }
const group = (title) => console.log(`\n── ${title} ──────────────────────────`)

const pluginDir = join(homedir(), '.dsh', 'plugins', 'dsh-plugin-wb2api-ui')
const lib = await import(pathToFileURL(join(pluginDir, 'lib', 'index.js')).href)

// ── ① 宿主模块 ──
group('① 宿主模块加载与导出')
assert(lib.name === 'wb2api-ui', `name = ${lib.name}`)
assert(Array.isArray(lib.inject) && lib.inject.length === 0, 'inject 为空数组（只借宿主 webServer）')
assert(typeof lib.apply === 'function', 'apply 是函数')
for (const fn of ['normalizeDocument', 'toUnixSeconds', 'inferRealm', 'listAccounts', 'writeAuthDocument', 'readCredential', 'collectCredits', 'classifyCheckin', 'tokenIssuerOrigin', 'localDay', 'collectGrowth']) {
  assert(typeof lib[fn] === 'function', `导出 ${fn}`)
}

// ── ② 路由（mock 宿主）──
group('② /dsh-wb2api/* 路由（mock webServer）')
const routes = new Map()
const host = {
  effect(fn) { fn() },
  webServer: { register(route, label) { routes.set(route.path, { route, label }) } },
}
lib.apply({ inject(deps, cb) { cb(host) }, logger: { info() {} } })
assert(routes.size === 13, `路由挂载数 = ${routes.size}（期望 13）`)

const callRoute = async (path, method, body) => {
  const entry = routes.get(path)
  if (entry === undefined) throw new Error('route missing: ' + path)
  const res = { statusCode: 0, body: '', writeHead(s) { this.statusCode = s }, end(b) { this.body = b ?? '' } }
  const req = { method, url: path, ...(body !== undefined ? { async *[Symbol.asyncIterator]() { yield Buffer.from(JSON.stringify(body)) } } : {}) }
  await entry.route.handler(req, res)
  return { status: res.statusCode, body: res.body === '' ? null : JSON.parse(res.body) }
}

const state = await callRoute('/dsh-wb2api/state', 'GET')
assert(state.status === 200 && state.body.ok === true, '/state 返回 200/ok')
assert(String(state.body.runtimeDir).replace(/\\/gu, '/').endsWith('.dsh/wb2api'), `runtimeDir = ${state.body.runtimeDir}`)
assert(Array.isArray(state.body.accounts) && state.body.accounts.every((a) => typeof a.file === 'string'), `/state 账号清单 ${state.body.accounts.length} 条`)
assert(state.body.gateway !== undefined && typeof state.body.admin === 'boolean', '/state 带网关探测与 admin 标志')
const wrongMethod = await callRoute('/dsh-wb2api/state', 'POST')
assert(wrongMethod.status === 405, 'POST /state → 405')
const missingBody = await callRoute('/dsh-wb2api/import', 'POST', {})
assert(missingBody.status === 500 && /需要 raw/.test(missingBody.body.error), '/import 缺 raw/document → 明确报错')
const badJson = await callRoute('/dsh-wb2api/import', 'POST', { raw: '{not json' })
assert(badJson.status === 500 && /JSON 解析失败/.test(badJson.body.error), '/import 坏 JSON → 明确报错')
const traversal = await callRoute('/dsh-wb2api/account/delete', 'POST', { file: 'workbuddy-..\\..\\..\\evil.json' })
assert(traversal.status === 500 && /不合法/.test(traversal.body.error), '删除带路径穿越的文件名 → 拒绝')
const badToggle = await callRoute('/dsh-wb2api/account/toggle', 'POST', { file: 'not-a-credential.txt', enabled: true })
assert(badToggle.status === 500 && /不合法/.test(badToggle.body.error), 'toggle 非凭证文件名 → 拒绝')
const noState = await callRoute('/dsh-wb2api/login/poll', 'POST', { realm: 'cn' })
assert(noState.status === 500 && /缺少 state/.test(noState.body.error), '/login/poll 缺 state → 拒绝（不打上游）')
const credits405 = await callRoute('/dsh-wb2api/credits', 'POST', {})
assert(credits405.status === 405, 'POST /credits → 405（不会误触发上游查询）')

// ── ③ 纯函数单元 ──
group('③ 纯函数单元')
const nested = lib.normalizeDocument({
  account: { uid: 'u-1', nickname: '甲', enterpriseId: 'e-1' },
  auth: { accessToken: 'at', refreshToken: 'rt', expiresAt: 1794480957, domain: 'www.codebuddy.cn', realm: 'cn' },
})
assert(nested.auth.expiresAt === 1794480957 && nested.auth.realm === 'cn', '嵌套 camelCase 原样通过')
const flat = lib.normalizeDocument({ uid: 'u-2', access_token: 'at2', expires_at: 1794480957832, domain: 'www.workbuddy.ai' })
assert(flat.account.uid === 'u-2' && flat.auth.expiresAt === 1794480957, '扁平 snake_case + 毫秒 → 归一为秒')
assert(flat.auth.realm === 'global', 'workbuddy.ai 域推断为 global')
try { lib.normalizeDocument([]); assert(false, '顶层数组应抛错') } catch { assert(true, '顶层数组抛错（由调用方拆项）') }
try { lib.normalizeDocument({ auth: { accessToken: 'x' } }); assert(false, '缺 uid 应抛错') } catch { assert(true, '缺 uid 抛错') }
try { lib.normalizeDocument({ account: { uid: 'x' } }); assert(false, '缺 accessToken 应抛错') } catch { assert(true, '缺 accessToken 抛错') }
assert(lib.toUnixSeconds(1794480957832) === 1794480957, 'toUnixSeconds 毫秒 → 秒')
assert(lib.toUnixSeconds(1794480957) === 1794480957, 'toUnixSeconds 秒原样')
assert(lib.toUnixSeconds(0) === 0 && lib.toUnixSeconds(undefined) === 0, 'toUnixSeconds 非法 → 0')
assert(lib.inferRealm('global', '') === 'global' && lib.inferRealm('', 'www.codebuddy.cn') === 'cn', 'inferRealm 显式优先 / 域名推断')
assert(lib.inferRealm('', 'workbuddy.ai') === 'global' && lib.inferRealm('', 'other.example') === 'cn', 'inferRealm 后缀与默认')
const c1 = lib.classifyCheckin(true, 0, 'ok')
assert(c1.ok && !c1.already, 'classifyCheckin code=0 → 成功')
const c2 = lib.classifyCheckin(true, 10001, '今天已经签到，请明天再来')
assert(c2.ok && c2.already, 'classifyCheckin 10001+已签到 → 达成')
const c3 = lib.classifyCheckin(true, 10001, '活动未开始')
assert(!c3.ok && c3.inactive, 'classifyCheckin 10001+未开始 → 不算成功')
const c4 = lib.classifyCheckin(true, 5, '活动已结束')
assert(!c4.ok && c4.inactive, 'classifyCheckin 活动结束 → inactive')
const fakeJwt = `x.${Buffer.from(JSON.stringify({ iss: 'https://www.codebuddy.cn/v2' })).toString('base64url')}.y`
assert(lib.tokenIssuerOrigin(fakeJwt) === 'https://www.codebuddy.cn', 'tokenIssuerOrigin 从 iss 取 origin')
assert(lib.tokenIssuerOrigin('not-a-jwt') === null, 'tokenIssuerOrigin 非法 → null')
assert(/^\d{4}-\d{2}-\d{2}$/u.test(lib.localDay()), `localDay = ${lib.localDay()}`)
assert(String(lib.authDir()).replace(/\\/gu, '/').endsWith('.dsh/wb2api/auths'), `authDir = ${lib.authDir()}`)

// ── ④ 浏览器半侧渲染冒烟 ──
group('④ 浏览器半侧渲染（stub react）')
const stateStore = []
let stateCursor = 0
const effects = []
const ReactStub = {
  createElement(tag, props, ...children) {
    if (typeof tag !== 'string' && typeof tag !== 'function') {
      throw new Error('Element type is invalid（白屏元凶：把样式对象当组件传了？）: ' + String(tag))
    }
    return { tag, props: props || {}, children: children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false && c !== true) }
  },
  useState(initial) {
    const i = stateCursor++
    if (i >= stateStore.length) stateStore.push(typeof initial === 'function' ? initial() : initial)
    const set = (v) => { stateStore[i] = typeof v === 'function' ? v(stateStore[i]) : v }
    return [stateStore[i], set]
  },
  useEffect(cb) { effects.push(cb) },
  useCallback(fn) { return fn },
  useRef(v) { return { current: v } },
}
const nowSec = Math.floor(Date.now() / 1000)
const syntheticState = {
  ok: true, runtimeDir: 'C:\\tmp', authDir: 'C:\\tmp\\auths', configured: true,
  binary: { path: 'C:\\tmp\\wb2a-server.exe', exists: true },
  gateway: { running: true, baseURL: 'http://127.0.0.1:7863', health: { healthy: 2, total: 3 } },
  accounts: [
    { file: 'workbuddy-a.json', uid: 'uid-a', nickname: '快乐不打烊', enterpriseId: '', realm: 'cn', domain: 'codebuddy.cn', expiresAt: nowSec + 30 * 86400, expired: false, disabled: false },
    { file: 'workbuddy-b.json', uid: 'uid-b', nickname: 'Shantanu', enterpriseId: '', realm: 'cn', domain: 'codebuddy.cn', expiresAt: nowSec + 3600, expired: false, disabled: true },
    { file: 'workbuddy-c.json', uid: 'uid-c', nickname: 'Lailani', enterpriseId: 'ent-1', realm: 'global', domain: 'workbuddy.ai', expiresAt: 0, expired: false, disabled: false },
  ],
  models: [{ id: 'cn:auto', contextLength: 131072, maxOutputTokens: 8192, supportsImages: true }],
  pool: { global: { cooling: 1, disabled: 0, healthy: 2, inFlightFull: 0 }, accounts: [
    { uid: 'uid-a', nickname: '快乐不打烊', cooling: false, until: '0001-01-01T00:00:00Z', breakerUntil: '0001-01-01T00:00:00Z', inFlight: 1, consecutiveFails: 0, successCount: 5, lastSuccess: new Date().toISOString(), lastErr: '0001-01-01T00:00:00Z', disabled: false, manualDisabled: false },
    { uid: 'uid-b', nickname: 'Shantanu', cooling: true, until: new Date(Date.now() + 60000).toISOString(), breakerUntil: '0001-01-01T00:00:00Z', inFlight: 0, consecutiveFails: 2, successCount: 1, lastSuccess: '', lastErr: '', disabled: false, manualDisabled: true },
  ] },
  activity: { 'checkin:uid-a': { ok: true, message: 'ok' }, 'checkin:uid-b': { ok: false, message: 'token 失效' } },
}
const syntheticCredits = { ok: true, credits: {
  total: 2924.53, unlimited: 0, counts: { ok: 3, error: 0, unreadable: 0, disabled: 0 }, oldestFetch: Date.now() - 5000,
  accounts: [
    { file: 'workbuddy-a.json', uid: 'uid-a', state: 'ok', total: 2924.53, unlimited: false, packageCount: 2, sumCapacityRemain: 3000, todayUsage: { used: 82.59, count: 9, date: '2026-09-28' }, segments: [
      { source: '月度包', remaining: 2000, total: 2000, expiresAt: Date.now() + 20 * 86400000 },
      { source: '签到包', remaining: 924.53, total: 1000, expiresAt: Date.now() + 2 * 86400000 },
    ] },
    { file: 'workbuddy-b.json', uid: 'uid-b', state: 'ok', total: 0, unlimited: false, packageCount: 0, sumCapacityRemain: 0, segments: [] },
    { file: 'workbuddy-c.json', uid: 'uid-c', state: 'ok', total: 0, unlimited: true, packageCount: 0, sumCapacityRemain: 0, segments: [] },
  ],
} }
const syntheticGrowth = { ok: true, growth: { claimable: 1, pendingCredits: 100, accounts: [
  { uid: 'uid-a', nickname: '快乐不打烊', streakDays: 3, summary: { completed: 17, total: 19, claimable: 1, pendingCredits: 100 }, tasks: [
    { code: 't1', title: '体验任务', state: 'claimed', current: 1, target: 1, credits: 100, claimable: true, auto: true },
  ] },
  { uid: 'uid-b', streakDays: 1, summary: { completed: 0, total: 0, claimable: 0, pendingCredits: 0 }, tasks: [] },
] } }
globalThis.fetch = async (url) => {
  const body = String(url).includes('/credits') ? syntheticCredits : String(url).includes('/growth') ? syntheticGrowth : syntheticState
  return { ok: true, status: 200, json: async () => body }
}
let captured = null
globalThis.window = { __ModuleLoader__: { load(mod) { captured = mod.factory((name) => { if (name === 'react') return ReactStub; throw new Error('unexpected require: ' + name) }) } } }
await import(pathToFileURL(join(pluginDir, 'client.js')).href)
assert(typeof captured?.apply === 'function', 'client factory 执行且 exports.apply 存在')
let Panel = null
captured.apply({ slots: { inject(_slot, fn) { fn() }, register(_meta, render) { Panel = render } } })
assert(typeof Panel === 'function', 'Panel 组件已捕获')
const flatTree = (node, acc = []) => {
  if (node === null || node === undefined || typeof node !== 'object') return acc
  acc.push(node)
  if (typeof node.tag === 'function') return flatTree(node.tag(node.props || {}), acc)
  for (const c of node.children || []) flatTree(c, acc)
  return acc
}
const settle = async () => {
  const list = effects.splice(0)
  for (const cb of list) cb()
  for (let i = 0; i < 3; i++) await new Promise((r) => setImmediate(r))
}
flatTree(Panel())
await settle()
stateCursor = 0
await settle()
stateCursor = 0
const rendered = flatTree(Panel())
assert(rendered.length > 20, `ready 态渲染出 ${rendered.length} 个节点`)
const collectText = (n, acc = []) => {
  if (typeof n === 'string') { acc.push(n); return acc }
  if (n === null || n === undefined || typeof n !== 'object') return acc
  for (const c of n.children || []) collectText(c, acc)
  return acc
}
const allText = rendered.map((n) => collectText(n).join('')).join('|')
for (const text of ['快乐不打烊', '有效期至', '今日已使用', '今日已签到', '签到失败', '连续登录 3 天', '可领 1 个任务', '未参与轮换', '冷却中', '128k 上下文']) {
  assert(allText.includes(text), `文案渲染：${text}`)
}

console.log('\n=== check_wb2api_ui 自检完成 ===')
