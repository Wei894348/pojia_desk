// 启动与点击路径自检 —— 把 app.js 的 bind() / boot() / doRun() 真跑一遍。
//
// 和 check_render.mjs 的分工：
//   check_render   —— 只建卡片，验证 DOM 结构、回显、四条命令开关的映射（函数级）。
//   check_boot     —— 起一套"够真的"桩 DOM（按 index.html 的静态 id 建好整棵树），
//                     真跑 boot() 与按钮回调，验证「勾选 → 实际发给桥的参数」（端到端）。
// 为什么需要：doRun 里"两项都不勾就把 dsh 剔出目标"这类逻辑，只有真点一次按钮才验得到；
// 而沙箱里不能起 Electron（CEF GPU 崩溃风暴，见项目记忆）。
//
// 用法：node gui/tools/check_boot.mjs
import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const GUI = path.dirname(HERE);

// ── 够真的 DOM 桩 ────────────────────────────────────────
class El {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.parent = null;
    this._cls = new Set(); this._attrs = {}; this.dataset = {}; this.style = {};
    this.id = ''; this.textContent = ''; this.title = ''; this.type = ''; this.checked = false;
    this.disabled = false; this.value = ''; this._html = ''; this._ls = [];
    const cls = this._cls;
    this.classList = {
      add: (...cs) => cs.forEach((c) => cls.add(c)),
      remove: (...cs) => cs.forEach((c) => cls.delete(c)),
      contains: (c) => this._cls.has(c),
      toggle: (c, force) => {
        const on = force === undefined ? !this._cls.has(c) : !!force;
        if (on) this._cls.add(c); else this._cls.delete(c);
        return on;
      },
    };
  }
  set className(v) { this._cls = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get className() { return [...this._cls].join(' '); }
  set innerHTML(v) { this._html = v; if (!v) this.children = []; }
  get innerHTML() { return this._html; }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  insertBefore(c, ref) {
    const i = this.children.indexOf(ref);
    c.parent = this; this.children.splice(i < 0 ? this.children.length : i, 0, c); return c;
  }
  replaceChild(nw, old) {
    const i = this.children.indexOf(old);
    if (i < 0) return this.appendChild(nw);
    nw.parent = this; this.children[i] = nw; return old;
  }
  removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); return c; }
  remove() { if (this.parent) this.parent.removeChild(this); }
  setAttribute(k, v) { this._attrs[k] = v; }
  getAttribute(k) { return this._attrs[k]; }
  addEventListener(t, fn) { this._ls.push({ t, fn }); }
  // 真点一下：跑所有该类型的监听器（app.js 里都是无参或 (e)）
  fire(t, e) { this._ls.filter((x) => x.t === t).forEach((x) => x.fn(Object.assign({ target: this, preventDefault() {} }, e || {}))); }
  get firstChild() { return this.children[0]; }
  get scrollHeight() { return 0; }

  _match(sel) {
    // 支持：标签、.类、#id、.类[data-x="y"]、.类A,类B
    for (const part of String(sel).split(',')) {
      const s = part.trim();
      if (!s) continue;
      let m = /^\.([\w-]+)(?:\[data-([\w-]+)="([^"]+)"\])?$/.exec(s);
      if (m) {
        if (!this._cls.has(m[1])) continue;
        if (m[2] !== undefined && String(this.dataset[m[2]]) !== m[3]) continue;
        return true;
      }
      m = /^#([\w-]+)$/.exec(s);
      if (m) { if (this.id === m[1]) return true; continue; }
      m = /^([a-zA-Z][\w-]*)$/.exec(s);
      if (m && this.tagName.toLowerCase() === m[1].toLowerCase()) return true;
    }
    return false;
  }
  _walk(sel, out) {
    for (const c of this.children) {
      if (c._match(sel)) out.push(c);
      c._walk(sel, out);
    }
    return out;
  }
  querySelectorAll(sel) { return this._walk(sel, []); }
  querySelector(sel) { return this._walk(sel, [])[0] || null; }
}

const body = new El('body');
const doc = {
  createElement: (t) => new El(t),
  getElementById: (id) => doc.body._walk('#' + id, [])[0] || null,
  querySelector: (s) => doc.body.querySelector(s),
  querySelectorAll: (s) => doc.body.querySelectorAll(s),
  addEventListener: () => {},
  body,
};

// 按 index.html 的静态 id 把整棵树搭起来 —— app.js 的 $() 全靠 getElementById
const html = fs.readFileSync(path.join(GUI, 'renderer', 'index.html'), 'utf8');
for (const m of html.matchAll(/<(\w+)[^>]*\bid="([\w-]+)"/g)) {
  const el = new El(m[1]);
  el.id = m[2];
  body.appendChild(el);
}
// 有些 id 是靠 dbootstrap 才建的（cards / toasts），html 里有就不用补
for (const need of ['cards', 'toasts', 'overlay', 'logBody']) {
  if (!doc.getElementById(need)) { const e = new El('div'); e.id = need; body.appendChild(e); }
}
// 补一个 .workspace（折叠按钮会用到）
const ws = new El('div'); ws.className = 'workspace'; body.appendChild(ws);

// ── 桥的假实现：记录每次调用的参数 ─────────────────────────
const calls = { run: [], status: [], test: [], diff: [], tx: [] };
const CARD = (k) => ({
  key: k, label: k, home: 'F:/x', state: 'current',
  rows: [], metrics: [], deploy: { chip: '什么都没部署', level: 'idle', text: '当前：什么都没部署' },
});
// 版本号从仓库根的 VERSION 读 —— 桩里写死会让这个自检随着发版一起过期。
const SRC_VERSION = (() => {
  try { return fs.readFileSync(path.join(GUI, '..', 'VERSION'), 'utf8').trim(); }
  catch (_) { return ''; }
})();
const pojia = {
  boot: async () => ({
    meta: { version: SRC_VERSION, root: 'F:/project/pojia_desk', route: { size: 20518, hit: ['任务路由'], miss: [] } },
    python: 'C:\\py\\python.exe', pythonVersion: '3.13.12', root: 'F:/project/pojia_desk',
  }),
  status: async (tg) => { calls.status.push(tg); return { when: 't', cards: (tg || []).map(CARD) }; },
  test: async (tg) => { calls.test.push(tg); return { ok: true, items: [{ level: 'info', text: 'OK' }] }; },
  route: async (s) => { calls.route = s; return { main: 'game-aim', hits: [], washers: [], norm: s }; },
  tx: async (a) => { calls.tx.push(a); return { ok: true, root: 'r', items: [] }; },
  diff: async (a) => { calls.diff.push(a); return { target: a[0], support: 'none', note: 'n' }; },
  run: async (action, targets, flags) => { calls.run.push({ action, targets, flags }); return { ok: true, code: 0 }; },
  abort: () => {},
  setPython: async () => ({ ok: true }),
  pickPython: async () => ({ canceled: true }),
  openPath: () => {}, readLog: async () => ({ ok: true, lines: [] }), openConsole: async () => ({ ok: true }),
  onLog: () => {}, win: () => {},
};

globalThis.window = { pojia, confirm: () => true };
globalThis.document = doc;
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
// 定时器改成「收集起来手动 flush」：刷新节流的合并窗口要靠它才验得出来
// （原先是同步立即执行 —— 那样"排队等冷却"会退化成"立刻重跑"，窗口永远测不到）
const timers = [];
globalThis.setTimeout = (fn) => { timers.push(fn); return timers.length; };
const flushTimers = () => { timers.splice(0).forEach((f) => { try { f(); } catch (_) { /* toast 的清理定时器 */ } }); };

// ── 载入 app.js（剥掉末尾启动三行，改成我们自己按顺序调）──
let src = fs.readFileSync(path.join(GUI, 'renderer', 'app.js'), 'utf8');
src = src.replace(/\nloadOpts\(\);\s*\nbind\(\);\s*\nboot\(\);\s*$/m, '\n');
if (/^bind\(\);$/m.test(src)) throw new Error('没能剥掉启动三行 —— app.js 末尾格式变了');
vm.runInThisContext(src + '\n;globalThis.__x = { loadOpts, bind, boot, doRun, doTest, state, ORDER, refreshStatus, revertAsk, flagsForOpts };',
  { filename: 'app.js' });
const X = globalThis.__x;

let pass = 0, fail = 0;
const check = (name, cond, extra) => {
  if (cond) { pass++; console.log('  ✓ ' + name); }
  else { fail++; console.log('  ✗ ' + name + (extra ? '  → ' + extra : '')); }
};
const $ = (id) => doc.getElementById(id);
const lastRun = () => calls.run[calls.run.length - 1];
const setOpts = (p, q) => { X.state.opts.prompt = p; X.state.opts.preset = q; };

// ── 1. 启动路径 ──────────────────────────────────────────
console.log('【1】bind() + boot() 真跑');
// 先把标题写脏 —— 否则 index.html 里的初值恰好也是当前版本，就验不出"到底有没有被 meta 覆盖"
$('tbVersion').textContent = 'v0.0-占位';
$('tbSub').textContent = '占位';
$('sbPython').textContent = 'python —';
$('sbRoot').textContent = '项目 —';
let bootErr = null;
await (async () => { try { X.loadOpts(); X.bind(); await X.boot(); } catch (e) { bootErr = e; } })();
check('bind() + boot() 不抛异常', !bootErr, bootErr && (bootErr.message + '\n' + bootErr.stack));
check('版本号被 meta 覆盖（页面里那个只是占位）',
  $('tbVersion').textContent === 'v' + SRC_VERSION, $('tbVersion').textContent);
check('副标题按 ORDER 算（九张卡 → 九目标）',
  $('tbSub').textContent === '九目标统一控制台', $('tbSub').textContent);
check('底部栏回显解释器与项目根',
  $('sbPython').textContent.includes('python.exe') && $('sbRoot').textContent.includes('pojia_desk'),
  $('sbPython').textContent + ' | ' + $('sbRoot').textContent);
check('boot 后每张卡都渲染出来', $('cards').children.length === X.ORDER.length,
  String($('cards').children.length));
check('每张卡的 key 都对', $('cards').children.map((c) => c.dataset.key).join(',') === X.ORDER.join(','),
  $('cards').children.map((c) => c.dataset.key).join(','));
check('DSH 卡里有状态行（新加的那行真渲染出来了）',
  !!$('cards').querySelector('.deploystate'));

// ── 2. 部署按钮：勾选组合 → 真发给桥的参数 ─────────────────
console.log('【2】点「部署」→ 实际发给桥的参数');
const deploy = $('btnDeploy');
calls.run.length = 0;

setOpts(true, true);
await deploy.fire('click'); await new Promise((r) => setImmediate(r));
// 前端发的是**卡片 key**（含 dsh-desktop）；折回 dsh 是桥那一侧干的事（CARD_ONLY），
// 这里只确认它照原样全发出去了、且没夹带开关。
check('两项都勾 → apply / 全部卡片 / 无开关',
  lastRun().action === 'apply' && lastRun().targets.length === X.ORDER.length
    && lastRun().flags.length === 0,
  JSON.stringify(lastRun()));
check('前端把 dsh-desktop 原样发给桥（折叠在桥侧做）',
  lastRun().targets.includes('dsh-desktop'), JSON.stringify(lastRun()));

calls.run.length = 0;
setOpts(true, false);
await deploy.fire('click'); await new Promise((r) => setImmediate(r));
check('只勾「注入提示词」→ flags=no-preset，dsh 仍在目标里',
  lastRun().flags.join(',') === 'no-preset' && lastRun().targets.includes('dsh'),
  JSON.stringify(lastRun()));

calls.run.length = 0;
setOpts(false, true);
await deploy.fire('click'); await new Promise((r) => setImmediate(r));
check('只勾「增加破甲模式」→ flags=preset-only，所有卡片都还在',
  lastRun().flags.join(',') === 'preset-only'
    && lastRun().targets.length === X.ORDER.length,
  JSON.stringify(lastRun()));

calls.run.length = 0;
setOpts(false, false);
await deploy.fire('click'); await new Promise((r) => setImmediate(r));
check('都不勾 → dsh 被剔出目标，其余 7 个照跑、不带开关',
  lastRun().targets.length === 7 && !lastRun().targets.includes('dsh') && lastRun().flags.length === 0,
  JSON.stringify(lastRun()));

// ── 3. 恢复按钮：都不勾时不把 dsh 带上 ─────────────────────
console.log('【3】点「恢复」');
calls.run.length = 0;
setOpts(false, false);
await $('btnRevert').fire('click'); await new Promise((r) => setImmediate(r));
check('都不勾 → revert 的 targets 里没有 dsh',
  lastRun() && lastRun().action === 'revert' && !lastRun().targets.includes('dsh'),
  JSON.stringify(lastRun()));
setOpts(true, true);
calls.run.length = 0;
await $('btnRevert').fire('click'); await new Promise((r) => setImmediate(r));
check('都勾 → revert 带上 dsh（连带删 preset 目录）',
  lastRun().targets.includes('dsh') && lastRun().flags.length === 0, JSON.stringify(lastRun()));

// ── 4. 一个目标都不勾：必须拦住，不能打桥 ───────────────────
console.log('【4】没勾任何目标时的兜底');
X.state.selected.clear();
calls.run.length = 0;
await deploy.fire('click'); await new Promise((r) => setImmediate(r));
check('没勾目标 → 一条命令都不发（只弹提示）', calls.run.length === 0, JSON.stringify(calls.run));
X.ORDER.forEach((k) => X.state.selected.add(k));
setOpts(true, true);

// ── 4b. refreshStatus：合并 + 缓存 + 只刷目标 ───────────────
// 三层防回退：
//   ① 一次请求拿全部要探的卡 —— 旧实现是 9 个并发单卡请求、每个只取 cards[0]，
//      任何一个失败/超时那张卡就停在旧内容上（"某张卡不刷新"的根源）；
//   ② 冷却窗口内的多次刷新合并成一次真检测 —— 旧实现每次调用都真打桥，
//      一次部署要跑两轮（收尾那轮 + busy 期间攒下的那轮）；
//   ③ TTL 内探过的卡吃缓存 —— 旧实现切个窗口回来就重探 8 个目标。
console.log('【4b】状态刷新：合并 + 缓存 + 只刷目标');
const resetRefresh = (lastAt) => {
  X.state.busy = false;
  X.state._pendingRefresh = null;
  X.state._refreshTimer = 0;
  X.state._queuedKeys = null;
  X.state._lastRefreshAt = lastAt === undefined ? 0 : lastAt;
};

// ① force 全量：真打一次，带齐九张卡的 key
resetRefresh(0);
X.state._cardCache.clear();
calls.status.length = 0;
await X.refreshStatus(true, null, true);
await new Promise((r) => setImmediate(r));
check('force 全量 → 只发一次 status 请求',
  calls.status.length === 1, '实际 ' + calls.status.length + ' 次');
check('一次带齐全部卡片 key',
  !!calls.status[0] && calls.status[0].length === X.ORDER.length,
  JSON.stringify(calls.status[0]));

// ② 非 force：TTL 内直接吃缓存，不再打桥（切窗口回来就靠这条不重探）
resetRefresh(0);
calls.status.length = 0;
await X.refreshStatus(true);
await new Promise((r) => setImmediate(r));
check('TTL 内刷新吃缓存，不发 status 请求',
  calls.status.length === 0, '实际 ' + calls.status.length + ' 次');

// ③ 冷却窗口：窗口内多次请求只排一个定时器，到点只真探一次
resetRefresh(Date.now());
X.state._cardCache.clear();          // 让这次必须真探，才验得出"只探一次"
calls.status.length = 0;
timers.length = 0;
await X.refreshStatus(true);
await X.refreshStatus(true);
check('冷却窗口内多次刷新只排一个定时器', timers.length === 1,
  '实际 ' + timers.length + ' 个');
flushTimers();
await new Promise((r) => setImmediate(r));
check('合并后只真检测一次', calls.status.length === 1,
  '实际 ' + calls.status.length + ' 次');

// ④ 局部刷新：只把要刷的目标发给桥（部署完只重探动过的那几个）
resetRefresh(0);
calls.status.length = 0;
await X.refreshStatus(true, new Set(['wb']), true);
await new Promise((r) => setImmediate(r));
check('局部刷新只带指定目标',
  calls.status.length === 1 && JSON.stringify(calls.status[0]) === '["wb"]',
  JSON.stringify(calls.status));

// ⑤ busy 期间记待办，收尾补跑（不能静默丢弃，否则卡片停在旧内容上）
X.state.busy = true;
X.state._pendingRefresh = null;
await X.refreshStatus(true);
check('busy 时的刷新记待办、不静默丢弃', !!X.state._pendingRefresh);
check('待办里记下了要刷的目标', X.state._pendingRefresh
  && X.state._pendingRefresh.keys === null);
X.state.busy = true;
await X.refreshStatus(true, new Set(['wb', 'codex']));
check('带目标的请求也记进待办',
  X.state._pendingRefresh && [...X.state._pendingRefresh.keys].join(',') === 'wb,codex',
  JSON.stringify(X.state._pendingRefresh && [...X.state._pendingRefresh.keys]));
resetRefresh(0);

// ── 5. 其它按钮的绑定都在 ─────────────────────────────────
console.log('【5】工具条按钮都绑上了 click');
['btnDeploy', 'btnDry', 'btnDiff', 'btnTest', 'btnRevert', 'btnRefresh', 'btnTx', 'btnConsole',
 'btnPy', 'btnRoute', 'btnCollapse', 'btnLogFile', 'btnTxClose', 'btnTxPurge', 'btnDiffClose']
  .forEach((id) => {
    check(id + ' 有 click 监听', $_(id));
  });
function $_(id) { const e = $(id); return !!e && e._ls.some((x) => x.t === 'click'); }
// 键盘快捷键
check('routeInput 绑了 Enter', $('routeInput')._ls.some((x) => x.t === 'keydown'));

console.log('\n' + (fail ? '✗ ' + fail + ' 项不过' : '✓ 全过') + '（' + pass + ' 项）');
process.exit(fail ? 1 : 0);
