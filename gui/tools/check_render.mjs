// 渲染层逻辑自检 —— 不开 Electron，用最小 DOM 桩真跑 app.js 的卡片构建。
//
// 为什么需要它：沙箱里启 Electron 会触发 CEF 的 GPU 崩溃风暴（见项目记忆），
// 但又不能只靠肉眼看代码 —— DSH 卡片里那个下拉的两个勾选框有没有同步、
// 并行探测回来时会不会插错位置、flags 映射对不对，这些只有真跑一遍才知道。
//
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
    this.disabled = false; this.value = ''; this._html = '';
    // 必须每次现读 this._cls —— className 的 setter 会整个换掉这个 Set，
    // 闭包捕获旧 Set 的话，`className = 'card ...'` 之后的 classList.add('off')
    // 会写进已被丢弃的旧 Set（真实 DOM 里这俩是同一个 class 列表，不许走样）。
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
  // 只有清空语义（app.js 用 innerHTML='' 重置容器）
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
  removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); return c; }
  remove() { if (this.parent) this.parent.removeChild(this); }
  setAttribute(k, v) { this._attrs[k] = v; }
  getAttribute(k) { return this._attrs[k]; }
  addEventListener() { /* 绑定不在本自检范围 */ }
  get firstChild() { return this.children[0]; }
  get scrollHeight() { return 0; }

  _match(sel) {
    // 支持：#id、标签（input）、类（.card）、以及「标签? 类* 属性*」的组合，
    // 例如 label.optitem[data-opt]（两台破甲模式下拉的勾选框就按它找）。
    // 属性值可省（只判存在），data-* 走 dataset。
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
      // 值可以带引号也可以不带（真实 DOM 两者都认：input[type=checkbox]
      // 和 input[type="checkbox"] 等价）。桩里原本只认带引号的写法。
      const mm = /^\[([\w-]+)(?:=(?:"([^"]*)"|([^\]"]+)))?\]$/.exec(a);
      if (!mm) return false;
      const key = mm[1];
      const want = mm[2] !== undefined ? mm[2] : mm[3];
      // 真实 DOM 的属性选择器认的是"反映属性"：input[type=checkbox] 能命中
      // chk.type='checkbox' 这种直接赋值。桩里少这一步，app.js 用属性选择器就白写。
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
// 按 id 从已挂树的节点里找（app.js 的 $() 就是 getElementById —— 卡里的下拉得能找到）
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

// ── 载入 app.js（剥掉末尾的启动三行，避免真去绑事件/探网络）──
let src = fs.readFileSync(path.join(GUI, 'renderer', 'app.js'), 'utf8');
src = src.replace(/\nloadOpts\(\);\s*\nbind\(\);\s*\nboot\(\);\s*$/m, '\n');
if (/^bind\(\);$/m.test(src)) throw new Error('没能剥掉启动三行 —— app.js 末尾格式变了');
globalThis.__export = {};
vm.runInThisContext(src + '\n;globalThis.__export = { buildCard, upsertCard, renderCards, renderSkeleton, state, ORDER, LABELS, hasDsh, paintDshOpts, paintDshNodes, presetButtonFace, optMode, flagsForOpts, revertAsk };', {
  filename: 'app.js',
});
const { buildCard, upsertCard, renderCards, renderSkeleton, state, ORDER, LABELS, hasDsh,
        paintDshOpts, paintDshNodes, presetButtonFace, optMode, flagsForOpts, revertAsk } = globalThis.__export;

// ── 断言 ──────────────────────────────────────────────────
let pass = 0, fail = 0;
const check = (name, cond, extra) => {
  if (cond) { pass++; console.log('  ✓ ' + name); }
  else { fail++; console.log('  ✗ ' + name + (extra ? '  → ' + extra : '')); }
};

// 把卡挂到树上 —— paintDshOpts 走 getElementById，节点不挂树就找不到
const mount = (card) => { doc.body.children = []; doc.body.appendChild(card); return card; };
const dshCardOf = (extra) => buildCard(Object.assign(
  { key: 'dsh', label: 'DSH (DeepSeek Harness)', home: 'h', state: 'current', rows: [], metrics: [] },
  extra || {}));

console.log('【1】九张卡 = 八个命令行目标 + DSH 桌面版，没有「破甲模式」卡');
check('ORDER 不含 pojia', !ORDER.includes('pojia'), ORDER.join(','));
check('九张卡（八个目标 + dsh-desktop）', ORDER.length === 9, ORDER.join(','));
check('DSH 桌面版紧跟 DSH',
  ORDER.indexOf('dsh-desktop') === ORDER.indexOf('dsh') + 1, ORDER.join(','));
check('每张卡都是真目标或界面卡，没混进伪目标',
  ORDER.every((k) => k !== 'pojia' && LABELS[k]), ORDER.join(','));
renderSkeleton(ORDER);
check('骨架就是九张', doc.getElementById('cards').children.length === 9);
// 喂数据对象（upsertCard 取的是 c.key，不是 DOM 节点）
[{ key: 'wb', label: 'WorkBuddy', home: '', state: 'none', rows: [], metrics: [] },
 { key: 'codex', label: 'Codex', home: '', state: 'current', rows: [], metrics: [] }]
  .forEach(upsertCard);
check('插入后还是九张（替换骨架，不会凭空多一张）',
  doc.getElementById('cards').children.length === 9,
  String(doc.getElementById('cards').children.length));
check('顺序仍是 ORDER',
  doc.getElementById('cards').children.map((c) => c.dataset.key).join(',') === ORDER.join(','),
  doc.getElementById('cards').children.map((c) => c.dataset.key).join(','));
check('每张卡都带勾选框（没有"不可选"的特殊卡）',
  doc.getElementById('cards').children.every((c) => c.querySelectorAll('input').length >= 1));
// 桌面版卡执行时折回 dsh（bridge 的 CARD_ONLY）：只勾桌面版也要认得出"这批里有 DSH"，
// 否则破甲模式下拉的开关判定与恢复确认都会漏掉它。
check('只勾桌面版也算"这批里有 DSH"',
  hasDsh(['dsh-desktop']) && hasDsh(['dsh']) && !hasDsh(['wb', 'codex']),
  [hasDsh(['dsh-desktop']), hasDsh(['dsh']), hasDsh(['wb'])].join(','));

console.log('【2】DSH 卡片里的破甲模式下拉');
const dshCard = mount(dshCardOf());
const optMenu = dshCard.querySelector('.optmenu');
check('DSH 卡带下拉条', !!dshCard.querySelector('.card-opt'));
check('结构 = head → 路径 → 下拉 → 状态行 → 菜单 → 明细',
  dshCard.children.map((n) => (n.className || '').split(' ')[0]).join(',').startsWith('card-head,card-home,card-opt,deploystate,optmenu'),
  dshCard.children.map((n) => n.className).join(' | '));
check('菜单两项，顺序 = 注入提示词 / 增加破甲模式',
  optMenu && optMenu.children.map((it) => it.dataset.opt).join(',') === 'prompt,preset',
  optMenu && optMenu.children.map((it) => it.dataset.opt).join(','));
check('每一项都是 **勾选框**（不是单选圆点）',
  !!optMenu && optMenu.children.every((it) => {
    const b = it.querySelector('input');
    return b && b.type === 'checkbox' && b.id;
  }));
check('勾选框 id 成对：optPrompt / optPreset',
  !!optMenu && optMenu.children.map((it) => it.querySelector('input').id).join(',') === 'optPrompt,optPreset',
  optMenu && optMenu.children.map((it) => it.querySelector('input').id).join(','));
check('每项都带名字 + 说明',
  !!optMenu && optMenu.children.every((it) => it.querySelectorAll('.oname').length === 1
    && it.querySelectorAll('.osub').length === 1));
check('没有残留的单选 tick',
  !!optMenu && optMenu.querySelectorAll('.tick').length === 0);
check('别的目标卡片没有这个下拉',
  !buildCard({ key: 'wb', label: 'WorkBuddy', home: '', state: 'none', rows: [], metrics: [] })
    .querySelector('.card-opt'));

console.log('【3】两组勾选的组合与回显');
[
  [{ prompt: true,  preset: true  }, 'both',   '提示词 + 破甲模式', 'on'],
  [{ prompt: true,  preset: false }, 'prompt', '只注入提示词',      'half'],
  [{ prompt: false, preset: true  }, 'preset', '只加破甲模式',      'half'],
  [{ prompt: false, preset: false }, 'none',   '都不做',            'idle'],
].forEach(([o, mode, text, cls]) => {
  state.opts.prompt = o.prompt;
  state.opts.preset = o.preset;
  const f = paintDshOpts();
  const boxes = optMenu.children.map((it) => it.querySelector('input').checked);
  check(mode + ' → 「' + text + '」/' + cls + '，两个勾选框同步 [' + boxes.join(',') + ']',
    f.mode === mode && f.text === text && f.cls === cls
    && doc.getElementById('ddDshText').textContent === text
    && doc.getElementById('ddDshBtn').classList.contains(cls)
    && boxes[0] === o.prompt && boxes[1] === o.preset,
    f.mode + ' / ' + f.text + ' / ' + f.cls + ' / ' + boxes.join(','));
});
state.opts.prompt = false; state.opts.preset = false;
paintDshOpts();
paintDshOpts();
check('换组合时旧配色摘干净（不留 on / half）',
  !doc.getElementById('ddDshBtn').classList.contains('on')
  && !doc.getElementById('ddDshBtn').classList.contains('half'));
check('optMode 对空值有兜底（不返回 undefined）', !!optMode({}));

console.log('【3b】DSH 桌面版卡也挂一台下拉（两台共享 state.opts）');
const dshDesk = buildCard({ key: 'dsh-desktop', label: 'DSH 桌面版', home: 'h',
  state: 'current', rows: [], metrics: [],
  deploy: { chip: '提示词 + 破甲模式', level: 'on', any: true, both: true,
            prompt: '本版', preset: '本版',
            text: '当前：已部署 提示词（本版）+ 破甲模式（本版）' } });
mount(dshDesk);
const deskMenu = dshDesk.querySelector('.optmenu');
check('桌面版卡带下拉条 + 状态行',
  !!dshDesk.querySelector('.card-opt') && !!dshDesk.querySelector('.deploystate'));
check('下拉的勾选框 id 带后缀（不与 DSH 卡撞 id）',
  !!deskMenu && deskMenu.children.map((it) => it.querySelector('input').id).join(',')
    === 'optPrompt2,optPreset2',
  deskMenu && deskMenu.children.map((it) => it.querySelector('input').id).join(','));
state.opts.prompt = false; state.opts.preset = true;
paintDshOpts();
check('重画时桌面版那一台跟着 state.opts 走（不是只画 DSH 那台）',
  deskMenu.children.map((it) => it.querySelector('input').checked).join(',') === 'false,true',
  deskMenu.children.map((it) => it.querySelector('input').checked).join(','));
state.opts.prompt = true; state.opts.preset = true;
paintDshOpts();
// 本节的 mount 把原来那张 DSH 卡挤出了测试 DOM，这里挂回去 ——
// 后面【6】节的开合断言还指着它（$() 只在已挂树的节点里找）。
mount(dshCard);

console.log('【4】组合 → 命令行开关');
state.opts.prompt = true; state.opts.preset = true;
check('两个都勾 → 不带开关（走默认：连带 preset）', flagsForOpts().join(',') === '');
state.opts.prompt = true; state.opts.preset = false;
check('只注入提示词 → no-preset', flagsForOpts().join(',') === 'no-preset');
state.opts.prompt = false; state.opts.preset = true;
check('只加破甲模式 → preset-only', flagsForOpts().join(',') === 'preset-only');
state.opts.prompt = false; state.opts.preset = false;
check('都不做 → 不带开关（由 doRun 把 dsh 剔出目标）', flagsForOpts().join(',') === '');
state.opts.prompt = true; state.opts.preset = true;

console.log('【5】恢复前的确认文案按档位变');
const askBoth = revertAsk(['dsh']);
state.opts.prompt = false; state.opts.preset = true;
const askPreset = revertAsk(['dsh']);
state.opts.prompt = true; state.opts.preset = false;
const askPrompt = revertAsk(['dsh']);
const askOther = revertAsk(['wb']);
check('都勾 → 说明 preset 目录会一并删掉',
  askBoth.includes('破甲模式目录也会一并删掉'), askBoth.split('\n').pop());
check('只勾破甲模式 → 说明本体不动',
  askPreset.includes('本体的提示词补丁保持不动'), askPreset.split('\n').pop());
check('只勾提示词 → 说明 preset 留着',
  askPrompt.includes('破甲模式目录留着'), askPrompt.split('\n').pop());
check('目标里没有 dsh → 不提破甲模式', !askOther.includes('破甲模式'));
state.opts.prompt = true; state.opts.preset = true;

console.log('【6】菜单开合');
state.ddOpen = false; paintDshOpts();
check('ddOpen=false → 菜单收起', !optMenu.classList.contains('on'));
state.ddOpen = true; paintDshOpts();
check('ddOpen=true → 菜单展开', optMenu.classList.contains('on'));
state.ddOpen = false; paintDshOpts();

console.log('【7】破甲模式状态只在 DSH 卡里显示一次');
const chipNames = (c) => c.querySelector('.metrics').children
  .map((b) => b.querySelectorAll('.mname')[0].textContent).join(',');
const withChip = {
  key: 'dsh', label: 'DSH', home: 'h', state: 'current', rows: [],
  metrics: [{ name: '安装基点', value: '1 处', ok: true },
            { name: '人格文件', value: '9 / 9', ok: true },
            { name: '破甲模式', value: '本版', ok: true }],
};
[[true, true], [true, false], [false, true], [false, false]].forEach(([p, q]) => {
  state.opts.prompt = p; state.opts.preset = q;
  check('组合 ' + p + '/' + q + ' → chip 恒显示（不再有任何条件渲染）',
    chipNames(buildCard(withChip)) === '安装基点,人格文件,破甲模式',
    chipNames(buildCard(withChip)));
});
state.opts.prompt = true; state.opts.preset = true;

console.log('【8】下拉的回显不依赖卡片重建');
const fresh = mount(dshCardOf());
const f = paintDshNodes(fresh.querySelector('.optbtn'), fresh.querySelector('#ddDshText'),
  fresh.querySelector('.optmenu'));
check('按节点引用画也照样回显（建卡时节点还没进 DOM）',
  f.text === '提示词 + 破甲模式'
  && fresh.querySelectorAll('.optitem')[0].querySelector('input').checked === true
  && fresh.querySelectorAll('.optitem')[1].querySelector('input').checked === true, f.text);

console.log('【9】「当前部署了什么 / 什么都没部署」状态行');
// bridge 的 _deploy_summary 产出的四种形态（文案原样照搬，改词就得改这里）
const DEPLOY = {
  both:   { level: 'on',   chip: '提示词 + 破甲模式',
            text: '当前：已部署 提示词（本版）+ 破甲模式（本版）' },
  prompt: { level: 'half', chip: '只部署了提示词',
            text: '当前：只部署了提示词（本版）· 破甲模式尚未部署' },
  preset: { level: 'half', chip: '只部署了破甲模式',
            text: '当前：只部署了破甲模式（本版）· DSH 本体的提示词没动' },
  none:   { level: 'idle', chip: '什么都没部署',
            text: '当前：什么都没部署 —— 提示词与破甲模式都没写入' },
};
const dshWith = (dep) => buildCard({ key: 'dsh', label: 'DSH', home: 'h',
  state: 'current', rows: [], metrics: [], deploy: dep });

check('DSH 卡带上状态行', !!dshWith(DEPLOY.both).querySelector('.deploystate'));
check('别的目标卡片没有状态行',
  !buildCard({ key: 'wb', label: 'WorkBuddy', home: '', state: 'none', rows: [], metrics: [] })
    .querySelector('.deploystate'));

Object.keys(DEPLOY).forEach((k) => {
  const d = DEPLOY[k];
  const node = dshWith(d).querySelector('.deploystate');
  // 用 className 判色，不用 classList —— 桩里的 classList 闭包绑在构造时的那个
  // Set 上，className 赋值后换新 Set，contains 会查旧集合（真 DOM 无此问题）。
  check('「' + d.chip + '」→ 文案 + 配色 ' + d.level,
    !!node && node.querySelectorAll('.dtxt')[0].textContent === d.text
    && node.className.includes('st-' + d.level),
    node ? (node.className + ' | ' + node.querySelectorAll('.dtxt')[0].textContent) : '没有状态行');
});

// 勾选框是"这次要做什么"，状态行是"盘上现在是什么" —— 两者必须互不影响
state.opts.prompt = false; state.opts.preset = false;
paintDshOpts();
const untouched = dshWith(DEPLOY.both).querySelector('.deploystate');
check('两个勾选框都取消，状态行照样报「已部署」（不跟着勾选变）',
  untouched.querySelectorAll('.dtxt')[0].textContent === DEPLOY.both.text
  && untouched.className.includes('st-on'));
state.opts.prompt = true; state.opts.preset = true;

check('探测结果缺失时状态行不炸、给兜底文案',
  !!dshWith(undefined) && dshWith(undefined).querySelectorAll('.dtxt')[0]
    .textContent.includes('还没探测到'));

console.log('【10】刷新时复用节点（不再整卡重建）');
// 防回退：upsertCard 曾经对同 key 直接 replaceChild 重建整张卡 ——
// 表现就是每次刷新整屏跳一下、滚动位置与 hover 全丢。现在只有骨架卡才新建节点。
renderSkeleton(ORDER);
const wrap = doc.getElementById('cards');
const pick = (k) => wrap.children.find((c) => c.dataset.key === k);
upsertCard({ key: 'wb', label: 'WorkBuddy', home: '/x/wb', state: 'none', rows: [], metrics: [] });
const node1 = pick('wb');
upsertCard({ key: 'wb', label: 'WorkBuddy', home: '/y/wb', state: 'current',
             rows: [{ level: 'ok', path: '/y/wb/f', note: 'n' }],
             metrics: [{ name: 'm', value: 1, ok: true }] });
const node2 = pick('wb');
check('同 key 二次 upsert 复用同一节点（不 replaceChild）', node1 === node2);
check('状态类名就地更新', !!node2 && node2.className.includes('s-current'),
  node2 && node2.className);
check('落点路径就地更新',
  !!node2 && node2.querySelectorAll('.card-home')[0].textContent === '/y/wb',
  node2 && node2.querySelectorAll('.card-home')[0].textContent);
check('指标块随新结果补上', !!node2 && !!node2.querySelector('.metrics'));
check('明细块随新结果补上并保持在下一位',
  !!node2 && !!node2.querySelector('.detail')
  && node2.children[node2.children.length - 1].className.split(' ')[0] === 'detail');
check('卡片总数与顺序不变（九张、按 ORDER）',
  wrap.children.length === ORDER.length
  && wrap.children.map((c) => c.dataset.key).join(',') === ORDER.join(','),
  wrap.children.map((c) => c.dataset.key).join(','));

console.log('【11】全选 / 全不选 与卡片左侧勾选框同源');
// 防回退：renderCards 的原地分支曾经只切 'off' 类、不切 checkbox.checked，
// 表现就是"点了全选，右边卡片亮了，左边勾号一个没动"。
renderSkeleton(ORDER);
ORDER.forEach((k) => upsertCard({ key: k, label: LABELS[k], home: '', state: 'current', rows: [], metrics: [] }));
const w2 = doc.getElementById('cards');
const boxes = () => w2.children.map((c) => {
  const b = c.querySelector('input[type=checkbox]');
  return b ? b.checked : null;
});
state.selected = new Set();
renderCards(state.cards);
check('全不选：九张卡的勾选框全空', boxes().every((b) => b === false), String(boxes()));
check('全不选：九张卡都带 off（变灰）',
  w2.children.every((c) => c.className.includes('off')));
state.selected = new Set(ORDER);
renderCards(state.cards);
check('全选：九张卡的勾选框全打上', boxes().every((b) => b === true), String(boxes()));
check('全选：没有卡还留灰', w2.children.every((c) => !c.className.includes('off')));
// 反选 = 在现状上取补集，勾选框也得跟着翻
state.selected = new Set([ORDER[0], ORDER[1]]);
renderCards(state.cards);
check('半选时就地同步：勾上的只头两张',
  boxes().map((b, i) => (b ? i : -1)).filter((i) => i >= 0).join(',') === '0,1',
  String(boxes()));
// 骨架阶段点全选不能把骨架清成空屏（state.cards 还是空的），但骨架卡上
// 那个 disabled 的勾选框也得跟着走，否则点了全选看着像没反应
renderSkeleton(ORDER);
state.selected = new Set(ORDER);
renderCards(state.cards);
const sBox = (c) => c.querySelector('input[type=checkbox]');
check('骨架阶段点全选：不清屏（还是九张骨架）', w2.children.length === ORDER.length,
  String(w2.children.length));
check('骨架阶段点全选：骨架卡勾选框也打上',
  w2.children.every((c) => sBox(c) && sBox(c).checked === true));
state.selected = new Set();
renderCards(state.cards);
check('骨架阶段点全不选：骨架卡勾选框也清空',
  w2.children.every((c) => sBox(c) && sBox(c).checked === false));

console.log('\n' + (fail ? '✗ ' + fail + ' 项不过' : '✓ 全过') + '（' + pass + ' 项）');
process.exit(fail ? 1 : 0);
