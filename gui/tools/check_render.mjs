// 渲染层逻辑自检 —— 不开 Electron，用最小 DOM 桩真跑 app.js 的卡片构建。
// 2077 版结构：块状卡 + 部署⇄还原滑块 + DSH 四档下拉（select）+ 底部日志。
// 用法：node gui/tools/check_render.mjs
import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const GUI = path.dirname(HERE);

// ── 最小 DOM ──────────────────────────────────────────────
class El {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.parent = null;
    this._cls = new Set(); this._attrs = {}; this.dataset = {}; this.style = {};
    this.id = ''; this.textContent = ''; this.title = ''; this.type = ''; this.checked = false;
    this.disabled = false; this.value = ''; this.selected = false; this._html = '';
    const self = this;
    this.classList = {
      add: (...cs) => cs.forEach((c) => self._cls.add(c)),
      remove: (...cs) => cs.forEach((c) => self._cls.delete(c)),
      contains: (c) => self._cls.has(c),
      toggle: (c, force) => {
        const on = force === undefined ? !self._cls.has(c) : !!force;
        if (on) self._cls.add(c); else self._cls.delete(c);
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
    c.parent = this;
    this.children.splice(i < 0 ? this.children.length : i, 0, c);
    return c;
  }
  replaceChild(nw, old) {
    const i = this.children.indexOf(old);
    if (i < 0) return this.appendChild(nw);
    nw.parent = this; this.children[i] = nw; return old;
  }
  replaceChildren(...ns) { this.children = ns.slice(); ns.forEach((n) => { n.parent = this; }); }
  removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); return c; }
  remove() { if (this.parent) this.parent.removeChild(this); }
  setAttribute(k, v) { this._attrs[k] = v; }
  getAttribute(k) { return this._attrs[k]; }
  addEventListener() { /* 绑定不在本自检范围 */ }
  closest() { return null; }
  get firstChild() { return this.children[0]; }
  get scrollHeight() { return 0; }
  get scrollWidth() { return 0; }

  _match(sel) {
    let m = /^#([\w-]+)$/.exec(sel);
    if (m) return this.id === m[1];
    m = /^([a-zA-Z][\w-]*)?((?:\.[\w-]+)*)((?:\[[^\[\]]+\])*)$/.exec(sel);
    if (!m) return false;
    const [, tag, classPart, attrPart] = m;
    if (!tag && !classPart && !attrPart) return false;
    if (tag && this.tagName.toLowerCase() !== tag.toLowerCase()) return false;
    for (const c of classPart.split('.').filter(Boolean)) {
      if (!this._cls.has(c)) return false;
    }
    for (const a of (attrPart.match(/\[[^\[\]]+\]/g) || [])) {
      const mm = /^\[([\w-]+)(?:=(?:"([^"]*)"|([^\]"]+)))?\]$/.exec(a);
      if (!mm) return false;
      const key = mm[1];
      const want = mm[2] !== undefined ? mm[2] : mm[3];
      let cur;
      if (key.startsWith('data-')) cur = this.dataset[key.slice(5)];
      else if (key in this._attrs) cur = this._attrs[key];
      else cur = this[key];
      if (want === undefined) { if (cur === undefined) return false; }
      else if (cur !== want) return false;
    }
    return true;
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

const byId = new Map();
function findById(node, id) {
  if (node.id === id) return node;
  for (const c of node.children) {
    const hit = findById(c, id);
    if (hit) return hit;
  }
  return null;
}
const doc = {
  createElement: (t) => new El(t),
  getElementById: (id) => {
    const hit = findById(doc.body, id);
    if (hit) return hit;
    if (!byId.has(id)) byId.set(id, new El('div'));
    return byId.get(id);
  },
  querySelectorAll: () => [],
  addEventListener: () => {},
  body: new El('body'),
};

globalThis.window = { pojia: {} };
globalThis.document = doc;
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
globalThis.setTimeout = setTimeout;

let src = fs.readFileSync(path.join(GUI, 'renderer', 'app.js'), 'utf8');
src = src.replace(/\nloadOpts\(\);\s*\nbind\(\);\s*\nboot\(\);\s*$/m, '\n');
if (/^bind\(\);$/m.test(src)) throw new Error('没能剥掉启动三行 —— app.js 末尾格式变了');
globalThis.__export = {};
vm.runInThisContext(src + '\n;globalThis.__export = { buildCard, upsertCard, renderCards, renderSkeleton, state, ORDER, LABELS, hasDsh, optMode, flagsForOpts, revertAsk, buildSlider };', {
  filename: 'app.js',
});
const { buildCard, upsertCard, renderCards, renderSkeleton, state, ORDER, LABELS, hasDsh,
        optMode, flagsForOpts, revertAsk, buildSlider } = globalThis.__export;

let pass = 0, fail = 0;
const check = (name, cond, extra) => {
  if (cond) { pass++; console.log('  ✓ ' + name); }
  else { fail++; console.log('  ✗ ' + name + (extra ? '  → ' + extra : '')); }
};

const CARD = (k, extra) => Object.assign(
  { key: k, label: LABELS[k] || k, home: 'h', state: 'none', rows: [], files: [], metrics: [] },
  extra || {});

console.log('【1】七张卡 = 六个命令行目标 + DSH 桌面版');
check('ORDER 不含 pojia', !ORDER.includes('pojia'), ORDER.join(','));
check('七张卡', ORDER.length === 7, ORDER.join(','));
check('DSH 桌面版紧跟 DSH',
  ORDER.indexOf('dsh-desktop') === ORDER.indexOf('dsh') + 1, ORDER.join(','));
check('每张卡都是真目标', ORDER.every((k) => LABELS[k]), ORDER.join(','));
renderSkeleton(ORDER);
check('骨架就是七张', doc.getElementById('cards').children.length === 7);
[{ key: 'wb', label: 'WorkBuddy', home: '', state: 'none', rows: [], files: [], metrics: [] },
 { key: 'codex', label: 'Codex', home: '', state: 'current', rows: [], files: [], metrics: [] }]
  .forEach(upsertCard);
check('插入后还是七张（替换骨架，不会凭空多一张）',
  doc.getElementById('cards').children.length === 7,
  String(doc.getElementById('cards').children.length));
check('顺序仍是 ORDER',
  doc.getElementById('cards').children.map((c) => c.dataset.key).join(',') === ORDER.join(','),
  doc.getElementById('cards').children.map((c) => c.dataset.key).join(','));
check('真卡带勾选框与滑块（骨架卡先占位，不算）',
  doc.getElementById('cards').children
    .filter((c) => !c.classList.contains('skeleton'))
    .every((c) => c.querySelectorAll('input').length >= 1 && c.querySelectorAll('.slider').length === 1));
check('只勾桌面版也算"这批里有 DSH"',
  hasDsh(['dsh-desktop']) && hasDsh(['dsh']) && !hasDsh(['wb', 'codex']));

console.log('【2】块状卡 + 滑块（部署 ⇄ 还原，还原走快照）');
const cardCur = buildCard(CARD('wb', { state: 'current' }));
const sliderCur = cardCur.querySelector('.slider');
check('已部署卡：滑块在「部署」位（on）', sliderCur.classList.contains('on'),
  sliderCur.className);
check('滑块挂了 seat（桥要按席位查快照）', sliderCur.dataset.seat === 'wb');
const cardNone = buildCard(CARD('wb', { state: 'none' }));
check('未部署卡：滑块在「还原」位（off）', !cardNone.querySelector('.slider').classList.contains('on'));
const cardStale = buildCard(CARD('wb', { state: 'stale' }));
check('旧版卡：滑块也在部署位（on）', cardStale.querySelector('.slider').classList.contains('on'));
check('滑块态文案映射齐四档',
  ['current', 'stale', 'none', 'broken'].every((s) => {
    const c = buildCard(CARD('wb', { state: s }));
    const st = c.querySelector('.sliderstate');
    return !!st && st.textContent.length > 0;
  }));
check('碎卡带 err 类（品红）', buildCard(CARD('wb', { state: 'broken' })).className.includes('err'));

console.log('【3】DSH 卡的「本次动作」下拉（两张 DSH 卡共享）');
const dshCard = buildCard(CARD('dsh', { state: 'current' }));
const sel = dshCard.querySelector('select.skselect');
check('DSH 卡带四档下拉', !!sel, dshCard.className);
check('四个选项 = 四种组合',
  !!sel && sel.children.map((o) => o.value).join(',') === 'both,prompt,skills,none',
  sel && sel.children.map((o) => o.value).join(','));
check('桌面版卡也有下拉',
  !!buildCard(CARD('dsh-desktop', { state: 'current' })).querySelector('select.skselect'));
check('别的目标卡没有下拉',
  !buildCard(CARD('wb')).querySelector('select.skselect'));
check('optMode 四档映射',
  optMode({ prompt: true, skills: true }) === 'both'
  && optMode({ prompt: true, skills: false }) === 'prompt'
  && optMode({ prompt: false, skills: true }) === 'skills'
  && optMode({ prompt: false, skills: false }) === 'none');

console.log('【4】组合 → 命令行开关');
state.opts.prompt = true; state.opts.skills = true;
check('both → 不带开关', flagsForOpts().join(',') === '');
state.opts.prompt = true; state.opts.skills = false;
check('只写提示词 → no-skills', flagsForOpts().join(',') === 'no-skills');
state.opts.prompt = false; state.opts.skills = true;
check('只投放 skills → no-agents', flagsForOpts().join(',') === 'no-agents');
state.opts.prompt = false; state.opts.skills = false;
check('都不做 → 不带开关（doRun 把 dsh 剔出目标）', flagsForOpts().join(',') === '');
state.opts.prompt = true; state.opts.skills = true;

console.log('【5】还原确认文案按档位变');
const askBoth = revertAsk(['dsh']);
state.opts.prompt = false; state.opts.skills = true;
const askSkills = revertAsk(['dsh']);
state.opts.prompt = true; state.opts.skills = false;
const askPrompt = revertAsk(['dsh']);
const askOther = revertAsk(['wb']);
check('both → 说明 AGENTS.md 与 skills 都会撤',
  askBoth.includes('AGENTS.md 标记块会摘掉'), askBoth.split('\n').pop());
check('只投放 skills → 说明 AGENTS.md 不动',
  askSkills.includes('AGENTS.md 保持不动'), askSkills.split('\n').pop());
check('只写提示词 → 说明 skills 留着',
  askPrompt.includes('skills 留着'), askPrompt.split('\n').pop());
check('还原走快照不是二进制基线（文案写死）',
  askOther.includes('事务快照') && askOther.includes('备份还原'));
check('目标里没有 dsh → 不提 AGENTS.md', !askOther.includes('AGENTS.md'));
state.opts.prompt = true; state.opts.skills = true;

console.log('【6】刷新时复用节点（不整卡重建）');
renderSkeleton(ORDER);
const wrap = doc.getElementById('cards');
const pick = (k) => wrap.children.find((c) => c.dataset.key === k);
upsertCard({ key: 'wb', label: 'WorkBuddy', home: '/x/wb', state: 'none', rows: [], files: [], metrics: [] });
const node1 = pick('wb');
upsertCard({ key: 'wb', label: 'WorkBuddy', home: '/y/wb', state: 'current',
             rows: [{ level: 'ok', path: '/y/wb/f', note: 'n' }],
             files: [{ path: '/y/wb/f', exists: true, role: 'r' }],
             metrics: [{ name: 'm', value: 1, ok: true }] });
const node2 = pick('wb');
check('同 key 二次 upsert 复用同一节点', node1 === node2);
check('状态类名就地更新（on = 已部署）', !!node2 && node2.className.includes('on'),
  node2 && node2.className);
check('落点路径就地更新',
  !!node2 && node2.querySelectorAll('.cmeta')[0].textContent === '/y/wb',
  node2 && node2.querySelectorAll('.cmeta')[0].textContent);
check('指标块随新结果补上', !!node2 && !!node2.querySelector('.cstats'));
check('滑块随状态翻位', !!node2 && node2.querySelector('.slider').classList.contains('on'));
check('卡片总数与顺序不变',
  wrap.children.length === ORDER.length
  && wrap.children.map((c) => c.dataset.key).join(',') === ORDER.join(','));

console.log('【7】全选 / 全不选与卡上勾选框同源');
renderSkeleton(ORDER);
ORDER.forEach((k) => upsertCard(CARD(k, { state: 'current' })));
const w2 = doc.getElementById('cards');
const boxes = () => w2.children.map((c) => {
  const b = c.querySelector('input[type=checkbox]');
  return b ? b.checked : null;
});
state.selected = new Set();
renderCards(state.cards);
check('全不选：七张卡的勾选框全空', boxes().every((b) => b === false), String(boxes()));
check('全不选：七张卡都带 dimmed', w2.children.every((c) => c.className.includes('dimmed')));
state.selected = new Set(ORDER);
renderCards(state.cards);
check('全选：七张卡的勾选框全打上', boxes().every((b) => b === true), String(boxes()));
check('全选：没有卡还留灰', w2.children.every((c) => !c.className.includes('dimmed')));
state.selected = new Set([ORDER[0], ORDER[1]]);
renderCards(state.cards);
check('半选时就地同步：勾上的只头两张',
  boxes().map((b, i) => (b ? i : -1)).filter((i) => i >= 0).join(',') === '0,1',
  String(boxes()));
state.selected = new Set(ORDER);

console.log('\n' + (fail ? '✗ ' + fail + ' 项不过' : '✓ 全过') + '（' + pass + ' 项）');
process.exit(fail ? 1 : 0);
