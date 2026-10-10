// 破甲工作台 · 渲染层逻辑
const $ = (id) => document.getElementById(id);
const api = window.pojia;

const STATE_MAP = {
  current: { cls: 's-current', badge: 'badge-ok', label: '已部署 · 本版' },
  stale:   { cls: 's-stale',   badge: 'badge-warn', label: '已部署 · 旧版' },
  none:    { cls: 's-none',    badge: 'badge-muted', label: '未部署' },
  broken:  { cls: 's-broken',  badge: 'badge-err', label: '未安装 / 异常' },
  foreign: { cls: 's-foreign', badge: 'badge-info', label: '外部配置' },
  warn:    { cls: 's-warn',    badge: 'badge-warn', label: '部分命中' },
  orphan:  { cls: 's-orphan',  badge: 'badge-warn', label: 'CLI 未检测到' },
};
const LEVEL_CLS = { ok: 'lv-ok', warn: 'lv-warn', err: 'lv-err', info: 'lv-info', dim: 'lv-dim' };
const ORDER = ['codex', 'dsh', 'dsh-desktop', 'wb', 'zcode', 'claude', 'grok', 'gemini', 'hermes'];
// 九张卡，但只有**八个命令行目标**：DSH 桌面版和 npm 版共用同一套命令
// （preset 一次就写 web + desktop 两份 profile），所以它界面单独成卡、
// 执行时折回 dsh（bridge 的 CARD_ONLY）。DSH 的「破甲模式」仍不是卡 ——
// 它是 DSH 卡片里的一个下拉选项（两个勾选框），状态并进那张卡的体检项与明细。
const LABELS = {
  codex: 'Codex CLI', dsh: 'DSH (Web/CLI)', 'dsh-desktop': 'DSH 桌面版',
  wb: 'WorkBuddy', zcode: 'ZCode (Z.ai)',
  claude: 'Claude Code', grok: 'Grok CLI', gemini: 'Gemini CLI', hermes: 'Hermes Agent',
};

const OPT_KEY = 'pojia.opts';

// ── 刷新策略 ───────────────────────────────────────────────
// 状态检测一次要跑 8 个目标（dsh 单卡就 3.5s），代价很高。
// 所以：① 两次真检测之间留冷却窗口，窗口内的刷新请求合并成一次；
//      ② 单张卡的结果缓存 20 秒，冷却窗口外的自动刷新（切窗口回来之类）
//         直接吃缓存，不重探；③ 只有手动刷新与刚跑完动作的目标才真探。
const REFRESH_GAP_MS = 5000;    // 两次真检测的最小间隔
const CARD_TTL_MS = 20000;      // 单卡结果缓存有效期

// DSH 卡片里那个下拉 = 两个独立勾选框，四种组合都合法：
//   两个都勾（默认）  注入提示词 + 增加破甲模式
//   只勾「注入提示词」 部署 / 恢复时打或还原 DSH 本体补丁，不动 preset
//   只勾「增加破甲模式」只用 preset（写入 / 移除），DSH 本体一个字节都不碰
//   都不勾            这次跳过 DSH
function optMode(o) {
  if (o.prompt && o.preset) return 'both';
  if (o.prompt) return 'prompt';
  if (o.preset) return 'preset';
  return 'none';
}

const MODE_TEXT = {
  both: '提示词 + 破甲模式', prompt: '只注入提示词',
  preset: '只加破甲模式', none: '都不做',
};

// 下拉按钮长什么样：两项都勾 = 绿，只勾一项 = 蓝，都不勾 = 灰
function presetButtonFace(o) {
  const mode = optMode(o);
  const cls = mode === 'none' ? 'idle' : (mode === 'both' ? 'on' : 'half');
  return { mode, text: MODE_TEXT[mode], cls };
}

// 下拉里两项 → 命令行开关。两个开关互斥，且只有 DSH 受它们影响
// （bridge 侧 preset-only 判的是 k === 'dsh'；no-preset 对别的目标是空转）。
function flagsForOpts() {
  const { prompt, preset } = state.opts;
  if (prompt && !preset) return ['no-preset'];    // 只注入提示词
  if (!prompt && preset) return ['preset-only'];  // 只动破甲模式
  return [];
}

const state = {
  meta: null,
  cards: [],
  selected: new Set(ORDER),
  expanded: new Set(),
  busy: false,
  logCount: 0,
  // DSH 卡片里那个下拉的两项：
  //   prompt —— 部署 / 恢复 DSH 时，写 / 还原提示词补丁（作用于 DSH 本体）
  //   preset —— 部署 / 恢复 DSH 时，写入 / 移除破甲模式（用户级 agent preset）
  opts: { prompt: true, preset: true },
  ddOpen: false,
  // 刷新节流的三块状态（详见下面 refreshStatus / runRefresh）：
  _lastRefreshAt: 0,        // 上次真检测的时刻
  _refreshTimer: 0,         // 冷却窗口的合并定时器
  _queuedKeys: null,        // 窗口内攒下的目标（Set；null = 全部）
  _pendingRefresh: null,    // busy 期间攒下的刷新请求 { keys }
  _cardCache: new Map(),    // key -> { card, at } —— 单卡结果缓存
};

// 冷却窗口内的目标取并集；任一侧是 null（= 全部）就是全部
function mergeRefreshKeys(a, b) {
  if (!a || !b) return null;
  const out = new Set(a);
  b.forEach((k) => out.add(k));
  return out;
}

function cacheCard(c) { state._cardCache.set(c.key, { card: c, at: Date.now() }); }

function loadOpts() {
  try {
    const d = JSON.parse(localStorage.getItem(OPT_KEY) || '{}');
    // preset 这个键两版同义（"连带动破甲模式"），直接沿用；
    // 旧版的 presetCard 是"面板上要不要那张独立卡片"—— 卡片已并进 DSH，丢弃。
    if (typeof d.preset === 'boolean') state.opts.preset = d.preset;
    if (typeof d.prompt === 'boolean') state.opts.prompt = d.prompt;
  } catch (_) { /* 没存过就用默认 */ }
}

function saveOpts() {
  try { localStorage.setItem(OPT_KEY, JSON.stringify(state.opts)); } catch (_) { /* 隐私模式 */ }
}

// 勾选框跟着 state.opts 走。菜单每次重建都要重画，所以单独拎出来。
function syncOptBoxes(root) {
  const scope = root || document;
  // 两台下拉各有自己的勾选框（id 带后缀），所以按 data-opt 找，别写死 id ——
  // 写死就会只同步 DSH 那台，桌面版那台勾了之后回显不动。
  scope.querySelectorAll('label.optitem[data-opt]').forEach((lab) => {
    const k = lab.dataset.opt;
    if (!(k in state.opts)) return;
    const box = lab.querySelector('input');
    if (box) box.checked = !!state.opts[k];
  });
}

// 把 state.opts 画回下拉：按钮摘要 + 配色 + 展开与否。
// 卡片每次 upsert 都会重建，所以拆成两份：一份按节点引用画（建卡时节点还没进 DOM），
// 一份按 id 找（勾选之后重画用）。全都判空，谁不在都不炸。
function paintDshNodes(btn, txtEl, menu) {
  const face = presetButtonFace(state.opts);
  if (btn) {
    btn.classList.remove('on', 'half', 'idle');
    btn.classList.add(face.cls);
  }
  if (txtEl) txtEl.textContent = face.text;
  if (menu) {
    menu.classList.toggle('on', !!state.ddOpen);
    syncOptBoxes(menu);
  }
  return face;
}

// 两台下拉共享 state.opts（两张 DSH 卡各一台），所以每次重画都过一遍两组节点。
function paintDshOpts() {
  const sets = [['ddDshBtn', 'ddDshText', 'ddDshMenu'],
                ['ddDshBtn2', 'ddDshText2', 'ddDshMenu2']];
  let first = null;
  sets.forEach(([b, t, m]) => {
    const face = paintDshNodes($(b), $(t), $(m));
    if (!first) first = face;
  });
  return first;
}

// DSH 卡片里那行「当前：已部署 … / 什么都没部署」。
// 报的是磁盘上的实际状态，跟卡内两个勾选框无关 —— 勾选说的是"这次要做什么"，
// 这行说的是"现在盘上是什么"。bridge 每次探测都会带上 deploy 字段。
const DEPLOY_CLS = { on: 'st-on', half: 'st-half', idle: 'st-idle' };

function buildDeployState(dep) {
  const d = dep || {};
  const box = document.createElement('div');
  box.className = 'deploystate ' + (DEPLOY_CLS[d.level] || 'st-idle');
  const led = document.createElement('span');
  led.className = 'dled';
  const txt = document.createElement('span');
  txt.className = 'dtxt';
  txt.textContent = d.text || '当前：还没探测到状态';
  txt.title = d.chip ? (txt.textContent + '（' + d.chip + '）') : txt.textContent;
  box.appendChild(led);
  box.appendChild(txt);
  return box;
}

// DSH 卡片里的「破甲模式」下拉。菜单内联展开 —— 卡片是 overflow:hidden，
// 绝对定位的浮层会被裁掉，所以选项直接挤在卡片流里。
//
// DSH 现在有两张卡（Web/CLI 版 + 桌面版），各挂一台下拉：它们打的是**同一份包树**
// （桌面版通过 profiles/node_modules 的 junction 指向 npm 缓存那套），所以两项
// 对两台卡含义完全一样、共享同一个 state.opts。用 sfx 给 id 加后缀区分节点。
function buildPresetOpt(dep, sfx) {
  sfx = sfx || '';
  const bar = document.createElement('div');
  bar.className = 'card-opt';
  const lbl = document.createElement('span');
  lbl.className = 'optlbl';
  lbl.textContent = '破甲模式';

  const btn = document.createElement('button');
  btn.className = 'optbtn';
  btn.id = 'ddDshBtn' + sfx;
  btn.title = '这个 DSH 目标要干什么：注入提示词（给它打补丁）/ 增加破甲模式（写入用户级 preset）—— 两项可各选，也可以都要';
  const led = document.createElement('span');
  led.className = 'led';
  const txt = document.createElement('span');
  txt.id = 'ddDshText' + sfx;
  const caret = document.createElement('span');
  caret.className = 'optcaret';
  caret.textContent = '\u25BE';
  btn.appendChild(led);
  btn.appendChild(txt);
  btn.appendChild(caret);

  const menu = document.createElement('div');
  menu.className = 'optmenu';
  menu.id = 'ddDshMenu' + sfx;

  const mk = (id, name, sub, key) => {
    const lab = document.createElement('label');
    lab.className = 'optitem';
    lab.dataset.opt = key;
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.id = id;
    box.checked = !!state.opts[key];
    box.addEventListener('change', () => pickOpt(key, box.checked));
    const box2 = document.createElement('span');
    box2.className = 'otext';
    const nm = document.createElement('span');
    nm.className = 'oname';
    nm.textContent = name;
    const sb = document.createElement('span');
    sb.className = 'osub';
    sb.textContent = sub;
    box2.appendChild(nm);
    box2.appendChild(sb);
    lab.appendChild(box);
    lab.appendChild(box2);
    // 点标签（含文字）交给 checkbox 自己处理，别冒泡到按钮把菜单关了
    lab.addEventListener('click', (e) => e.stopPropagation());
    return lab;
  };
  menu.appendChild(mk('optPrompt' + sfx, '注入提示词',
    '部署时给 DSH 打提示词补丁，恢复时还原成官方原版', 'prompt'));
  menu.appendChild(mk('optPreset' + sfx, '增加破甲模式',
    '写入 / 移除用户级 agent preset —— 模式下拉里多一个「破甲模式」', 'preset'));

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    state.ddOpen = !state.ddOpen;
    paintDshOpts();          // 两台下拉（两张 DSH 卡）开合状态保持一致
  });

  bar.appendChild(lbl);
  bar.appendChild(btn);
  return { bar, menu, btn, txt, state: buildDeployState(dep) };
}

// 勾 / 取消某一项：写盘 + 更新回显。卡片不再增删，所以不用重新探测
// （省一次几秒的安装基点扫描）；状态栏那句提示靠下一次刷新带上。
function pickOpt(key, on) {
  state.opts[key] = !!on;
  saveOpts();
  const face = paintDshOpts();
  logLine('dim', 'sys', 'DSH 破甲 → ' + face.text);
  return face;
}

// 当前勾选的目标 —— 全部都是真目标，DSH 那个下拉只决定 DSH 自己怎么跑
function targets() {
  return [...state.selected];
}

// 桌面版那张卡执行时会折回 dsh（bridge 的 CARD_ONLY），所以"这一批里有没有 DSH"
// 得两张卡一起看 —— 只勾桌面版时漏判会让破甲模式的下拉开关和恢复确认都不出现。
function hasDsh(tg) {
  return tg.includes('dsh') || tg.includes('dsh-desktop');
}

// ────────────────────────── 通用 UI ──────────────────────────

function toast(msg, kind) {
  const el = document.createElement('div');
  el.className = 'toast ' + (kind || '');
  el.textContent = msg;
  $('toasts').appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .3s'; }, 4200);
  setTimeout(() => el.remove(), 4700);
}

function setBusy(busy, msg) {
  state.busy = busy;
  document.querySelectorAll('.toolbar .btn').forEach((b) => { b.disabled = busy; });
  const dot = $('sbDot');
  dot.className = 'dot ' + (busy ? 'run' : 'idle');
  if (msg) $('sbMsg').textContent = msg;
}

function overlay(on, text) {
  $('overlay').classList.toggle('on', !!on);
  if (text) $('overlayText').textContent = text;
}

function setStatus(msg, kind) {
  $('sbMsg').textContent = msg;
  if (kind) $('sbDot').className = 'dot ' + kind;
}

// ────────────────────────── 日志 ──────────────────────────

function logLine(level, tag, text) {
  const body = $('logBody');
  // 首屏占位行清掉
  if (state.logCount === 0) body.innerHTML = '';
  const row = document.createElement('div');
  row.className = 'lrow ' + (LEVEL_CLS[level] || 'lv-dim');
  const t = document.createElement('span');
  t.className = 'ltag';
  t.textContent = tag || '';
  const x = document.createElement('span');
  x.className = 'ltext';
  x.textContent = text;
  row.appendChild(t);
  row.appendChild(x);
  body.appendChild(row);
  state.logCount += 1;
  $('logCount').textContent = state.logCount;
  while (body.children.length > 2600) body.removeChild(body.firstChild);
  if ($('autoScroll').checked) body.scrollTop = body.scrollHeight;
}

function logHead(text) { logLine('head', '—', text); }

// ────────────────────────── 卡片渲染 ──────────────────────────

// 卡头那个勾选框。'off' 类与它的 checked 必须同源 ——
// 只切类不切 checked，就是"点了全选、左边勾号一个没动"。
function cardBox(node) {
  const head = node ? node.querySelector('.card-head') : null;
  return head ? head.querySelector('input[type=checkbox]') : null;
}

function renderCards(cards) {
  // 按 ORDER 重排 —— state.cards 的入队顺序是"探测谁先回来"，
  // 不排的话勾选一次全选卡片就会跳位
  state.cards = cards.slice().sort((a, b) =>
    ORDER.indexOf(a.key) - ORDER.indexOf(b.key));
  const wrap = $('cards');
  // 首屏骨架阶段 state.cards 还是空的：不能往重建分支走（那会把骨架清成空屏），
  // 但骨架卡头上那个 disabled 的勾选框也得跟着走 —— 否则点了「全选」看着像没反应。
  if (!state.cards.length) {
    wrap.querySelectorAll('.card').forEach((n) => {
      const box = cardBox(n);
      if (box) box.checked = state.selected.has(n.dataset.key);
    });
    return;
  }
  // 全选 / 反选只是换勾选态，节点本来就在 —— 就地同步 'off' 类 + 勾选框，
  // 别整屏重建（那是"点一下全选界面闪一片"的来源）
  const live = new Map([...wrap.querySelectorAll('.card')].map((n) => [n.dataset.key, n]));
  if (state.cards.every((c) => live.has(c.key))) {
    state.cards.forEach((c) => {
      const node = live.get(c.key);
      const on = state.selected.has(c.key);
      node.classList.toggle('off', !on);
      const box = cardBox(node);
      if (box && box.checked !== on) box.checked = on;
    });
    return;
  }
  wrap.innerHTML = '';
  state.cards.forEach((c) => wrap.appendChild(buildCard(c)));
}

// 骨架卡：状态还没回来时先把四个席位占上，别让首屏空着
function renderSkeleton(keys) {
  const wrap = $('cards');
  wrap.innerHTML = '';
  state.cards = [];
  keys.forEach((k) => {
    const el = document.createElement('article');
    el.className = 'card skeleton';
    el.dataset.key = k;
    const head = document.createElement('div');
    head.className = 'card-head';
    const chk = document.createElement('input');
    chk.type = 'checkbox';
    chk.checked = state.selected.has(k);
    chk.disabled = true;
    const name = document.createElement('span');
    name.className = 'card-name';
    name.textContent = LABELS[k] || k;
    const key = document.createElement('span');
    key.className = 'card-key';
    key.textContent = k;
    const spacer = document.createElement('div');
    spacer.className = 'card-spacer';
    const badge = document.createElement('span');
    badge.className = 'badge badge-muted';
    badge.textContent = '检测中…';
    head.appendChild(chk);
    head.appendChild(name);
    head.appendChild(key);
    head.appendChild(spacer);
    head.appendChild(badge);
    const home = document.createElement('div');
    home.className = 'card-home';
    home.textContent = '正在探测安装位置与部署文件……';
    el.appendChild(head);
    el.appendChild(home);
    wrap.appendChild(el);
  });
}

// 单个目标的结果回来就替换它自己那张卡 —— 八个目标并行探测，谁快谁先显示
function upsertCard(c) {
  const wrap = $('cards');
  const old = wrap.querySelector('.card[data-key="' + c.key + '"]');
  // 已经是真卡片就地更新；只有骨架卡（或压根没有）才新建节点。
  // 重建节点会丢滚动位置、hover 与展开动画 —— "界面老在闪"的来源之一。
  if (old && !old.classList.contains('skeleton')) {
    updateCard(old, c);
  } else {
    const el = buildCard(c);
    if (old) {
      wrap.replaceChild(el, old);
    } else {
      // 插到位置上，不无脑追加 —— 并行探测谁先回来是不定的
      const idx = ORDER.indexOf(c.key);
      const after = [...wrap.querySelectorAll('.card')]
        .find((n) => ORDER.indexOf(n.dataset.key) > idx);
      if (after) wrap.insertBefore(el, after); else wrap.appendChild(el);
    }
  }
  const i = state.cards.findIndex((x) => x.key === c.key);
  if (i >= 0) state.cards[i] = c; else state.cards.push(c);
}

// 同一张卡的新结果回来了 —— 就地改内容，不换节点。
// 只重画会变的几块：状态类名、徽标、落点、DSH 状态行、指标块、明细块。
function updateCard(el, c) {
  const map = STATE_MAP[c.state] || STATE_MAP.none;
  const on = state.selected.has(c.key);
  el.className = 'card ' + map.cls + (on ? '' : ' off');
  // 探测结果回来也是走这条就地更新 —— 期间若点过全选，勾选框要跟着对齐
  const box = cardBox(el);
  if (box && box.checked !== on) box.checked = on;

  const kids = Array.from(el.children);
  const byCls = (name) => kids.find((x) => (x.className || '').split(' ').includes(name));
  const open = state.expanded.has(c.key);

  const head = byCls('card-head');
  if (head) {
    const badge = head.querySelector('.badge');
    if (badge) { badge.className = 'badge ' + map.badge; badge.textContent = map.label; }
    const chev = head.querySelector('.chev');
    if (chev) chev.textContent = open ? '\u25B4' : '\u25BE';
  }

  const home = byCls('card-home');
  if (home) {
    home.textContent = c.home || '(未探测到路径)';
    home.title = c.home || '';
  }

  // DSH 卡那行「当前：已部署 …」—— 探测结果带了 deploy 才换
  const dsLive = byCls('deploystate');
  if (dsLive) el.replaceChild(buildDeployState(c.deploy), dsLive);

  const mLive = byCls('metrics');
  const dLive = byCls('detail');
  const mNew = buildMetrics(c);
  if (mLive && mNew) el.replaceChild(mNew, mLive);
  else if (mLive) el.removeChild(mLive);
  else if (mNew) { if (dLive) el.insertBefore(mNew, dLive); else el.appendChild(mNew); }

  const dNew = buildDetail(c, open);
  if (dLive) el.replaceChild(dNew, dLive); else el.appendChild(dNew);
}

function buildCard(c) {
  const map = STATE_MAP[c.state] || STATE_MAP.none;
  const el = document.createElement('article');
  el.className = 'card ' + map.cls;
  el.dataset.key = c.key;
  if (!state.selected.has(c.key)) el.classList.add('off');

  // 头部
  const head = document.createElement('div');
  head.className = 'card-head';

  const chkLabel = document.createElement('label');
  chkLabel.className = 'chk';
  const chk = document.createElement('input');
  chk.type = 'checkbox';
  chk.checked = state.selected.has(c.key);
  chk.addEventListener('change', () => {
    if (chk.checked) state.selected.add(c.key); else state.selected.delete(c.key);
    el.classList.toggle('off', !chk.checked);
    updateToolbarHint();
  });
  chkLabel.appendChild(chk);
  head.appendChild(chkLabel);

  const title = document.createElement('div');
  title.className = 'card-title';
  const name = document.createElement('span');
  name.className = 'card-name';
  name.textContent = c.label;
  const key = document.createElement('span');
  key.className = 'card-key';
  key.textContent = c.key;
  title.appendChild(name);
  title.appendChild(key);

  const spacer = document.createElement('div');
  spacer.className = 'card-spacer';

  const badge = document.createElement('span');
  badge.className = 'badge ' + map.badge;
  badge.textContent = map.label;

  const chev = document.createElement('button');
  chev.className = 'chev';
  const open = state.expanded.has(c.key);
  chev.textContent = open ? '\u25B4' : '\u25BE';

  head.appendChild(title);
  head.appendChild(spacer);
  head.appendChild(badge);
  head.appendChild(chev);

  // 落点路径
  const home = document.createElement('div');
  home.className = 'card-home';
  home.textContent = c.home || '(未探测到路径)';
  home.title = c.home || '';
  home.addEventListener('click', () => { if (c.home) api.openPath(c.home); });

  // 指标
  const metrics = buildMetrics(c);

  // 明细
  const detail = buildDetail(c, open);

  chev.addEventListener('click', () => {
    const hide = detail.classList.toggle('hide');
    if (hide) state.expanded.delete(c.key); else state.expanded.add(c.key);
    chev.textContent = hide ? '\u25BE' : '\u25B4';
  });

  el.appendChild(head);
  el.appendChild(home);

  // 两张 DSH 卡（Web/CLI 版与桌面版）各挂一台破甲模式下拉 + 当前部署状态行。
  // 它们打的是同一份包树（桌面版经 junction 指向 npm 缓存那套），两项的含义完全一样，
  // 所以下拉照给、共享 state.opts；执行时桥会把两张卡折成同一条 dsh 命令。
  if (c.key === 'dsh' || c.key === 'dsh-desktop') {
    const opt = buildPresetOpt(c.deploy, c.key === 'dsh' ? '' : '2');
    el.appendChild(opt.bar);
    el.appendChild(opt.state);
    el.appendChild(opt.menu);
    paintDshNodes(opt.btn, opt.txt, opt.menu);   // 节点这还没进 DOM，只能按引用画
  }

  if (metrics) el.appendChild(metrics);
  el.appendChild(detail);
  return el;
}

// 指标块 —— 单独拎出来，是为了让 updateCard 能只换这一块而不重建整张卡。
// 没有指标时返回 null（buildCard / updateCard 都得容忍）
function buildMetrics(c) {
  const list = c.metrics || [];
  if (!list.length) return null;
  const metrics = document.createElement('div');
  metrics.className = 'metrics';
  list.forEach((m) => {
    const box = document.createElement('div');
    box.className = 'metric ' + (m.ok ? 'ok' : 'bad');
    const n = document.createElement('span');
    n.className = 'mname';
    n.textContent = m.name;
    const v = document.createElement('span');
    v.className = 'mval';
    v.textContent = m.value;
    v.title = String(m.value);
    box.appendChild(n);
    box.appendChild(v);
    metrics.appendChild(box);
  });
  return metrics;
}

// 明细块 —— 同样是为了增量更新。没明细也是一块空壳（保持"最后的子节点"位置稳定）
function buildDetail(c, open) {
  const detail = document.createElement('div');
  detail.className = 'detail' + (open ? '' : ' hide');
  (c.rows || []).forEach((r) => {
    const row = document.createElement('div');
    row.className = 'row';
    const dot = document.createElement('span');
    dot.className = 'dot ' + (r.level || 'dim');
    const p = document.createElement('span');
    p.className = 'rpath';
    p.textContent = r.path || '';
    p.title = r.path || '';
    p.addEventListener('click', () => { if (r.path) api.openPath(r.path); });
    const note = document.createElement('span');
    note.className = 'rnote';
    note.textContent = r.note || '';
    row.appendChild(dot);
    row.appendChild(p);
    row.appendChild(note);
    detail.appendChild(row);
  });
  return detail;
}

function updateToolbarHint() {
  const n = state.selected.size;
  $('btnDeploy').title = n ? ('对选中的 ' + n + ' 个目标部署') : '先在卡片上勾选目标';
}

// ────────────────────────── 数据加载 ──────────────────────────

// 外壳：决定"这次请求要不要真的打一次检测"。
//   busy            → 记待办，本次结束后补跑（不能静默丢弃，否则卡片停在旧内容上）
//   冷却窗口内      → 合并成一个定时器，到点跑一次；期间来多少请求都只算一次
//   force           → 跳过冷却与缓存，立刻真检测（手动刷新、刚动过盘的目标走这条）
//     keys          → 只刷这些目标（Set）；null / 不传 = 全部
// 注意：定时器回调直接进 runRefresh，不再回到外壳 —— 否则 stub 里同步 setTimeout
//       会让"再排一次队"变成死循环。
async function refreshStatus(quiet, keys, force) {
  if (state.busy) { state._pendingRefresh = { keys: keys || null }; return; }

  const now = Date.now();
  if (!force && state._lastRefreshAt && now - state._lastRefreshAt < REFRESH_GAP_MS) {
    state._queuedKeys = mergeRefreshKeys(state._queuedKeys, keys || null);
    if (!state._refreshTimer) {
      const wait = REFRESH_GAP_MS - (now - state._lastRefreshAt);
      state._refreshTimer = setTimeout(() => {
        state._refreshTimer = 0;
        const q = state._queuedKeys;
        state._queuedKeys = null;
        runRefresh(true, q, false);
      }, wait);
    }
    return;
  }
  return runRefresh(quiet, keys || null, force);
}

// 内核：真去探一次。缓存命中的目标不重探，只把结果重画一遍。
async function runRefresh(quiet, keys, force) {
  if (state.busy) { state._pendingRefresh = { keys: keys || null }; return; }
  const want = keys ? ORDER.filter((k) => keys.has(k)) : ORDER.slice();
  if (!want.length) { overlay(false); return; }

  state._lastRefreshAt = Date.now();
  setBusy(true, '正在检测状态……');

  // 缓存命中判定：TTL 内刚探过就直接复用（force 时不看缓存）
  const now = Date.now();
  const cached = new Map();
  const ask = [];
  want.forEach((k) => {
    const hit = force ? null : state._cardCache.get(k);
    if (hit && now - hit.at < CARD_TTL_MS) cached.set(k, hit.card);
    else ask.push(k);
  });

  if (!quiet) overlay(true, '正在检测 ' + want.length + ' 个目标……');
  try {
    // 首屏才铺骨架。之后的刷新一律原地更新 —— 之前的 renderSkeleton 会把
    // 全部卡片清空重画，那是"界面老是在闪"的主因。
    if (!state.cards.length) renderSkeleton(want);
    // 一次请求拿全部要探的卡。
    //   原来是 9 个并发单卡请求、每个只取 cards[0] —— 任何一个失败或超时，
    //   那张卡就停在旧内容上（正是"某张卡不刷新"的来源）。
    //   bridge 侧 cmd_status 内部本来就是 ThreadPoolExecutor 批量并行，
    //   一次拿完更稳，也少 8 次往返。
    const byKey = new Map();
    let when = '';
    if (ask.length) {
      try {
        const d = await api.status(ask);
        ((d && d.cards) || []).forEach((c) => { if (c && c.key) byKey.set(c.key, c); });
        when = (d && d.when) || '';
      } catch (e) {
        logLine('err', 'status', '检测失败：' + ((e && e.message) || e));
      }
    }
    if (when) $('sbWhen').textContent = when;
    let bad = 0;
    want.forEach((k) => {
      const c = byKey.get(k) || cached.get(k);
      if (c) {
        cacheCard(c);
        upsertCard(c);
        if (c.state !== 'current') bad += 1;
      } else {
        // 没回来的卡片**必须显式标错**，不能让它留着上一次的内容冒充现状
        bad += 1;
        const broken = { key: k, label: LABELS[k] || k, home: '', state: 'broken',
                         rows: [{ level: 'err', path: '',
                                  note: '状态未返回（检测失败或超时）—— 点「刷新」或 F5 重试' }],
                         files: [], metrics: [] };
        cacheCard(broken);
        upsertCard(broken);
      }
    });
    setStatus(bad ? (bad + ' / ' + want.length + ' 个目标需要处理')
                  : (want.length + ' 个目标均为本版状态'), bad ? 'warn' : 'ok');
  } catch (e) {
    toast('检测出错：' + (e.message || e), 'err');
  } finally {
    setBusy(false);
    overlay(false);
    // 本次期间攒下的刷新请求，补跑一次（带上它要刷的目标）
    if (state._pendingRefresh) {
      const pend = state._pendingRefresh;
      state._pendingRefresh = null;
      const ks = pend.keys ? new Set(pend.keys) : null;
      refreshStatus(true, ks, false);
    }
  }
}

async function boot() {
  overlay(true, '正在初始化……');
  try {
    const info = await api.boot();
    $('tbVersion').textContent = 'v' + ((info.meta && info.meta.version) || '?');
    // 副标题跟着 ORDER 走 —— 加席位时不会再漏成「四目标」
    const CN = ['零', '一', '二', '三', '四', '五', '六', '七', '八', '九', '十'];
    $('tbSub').textContent = (CN[ORDER.length] || ORDER.length) + '目标统一控制台';
    $('sbPython').textContent = info.python ? (info.python.split('\\').pop() + ' · ' + info.pythonVersion) : 'python 未找到';
    $('sbPython').title = info.python || '未找到';
    $('sbRoot').textContent = '项目 ' + (info.root || '');
    $('sbRoot').title = info.root || '';
    state.meta = info.meta || null;
    if (!info.python) {
      toast('没找到可用的 Python，请点右下角「python」指定解释器', 'err');
      logLine('err', 'sys', '未找到 Python 解释器 —— 点右下角 python 手动指定。');
    } else {
      logHead('破甲工作台 v' + ((info.meta && info.meta.version) || '?') + ' · 解释器 ' + info.python);
      if (info.meta && info.meta.route) {
        logLine('info', 'rt', '任务路由层 ' + info.meta.route.size + ' B · 关键段 '
          + info.meta.route.hit.length + ' / ' + (info.meta.route.hit.length + info.meta.route.miss.length));
      }
      if (info.error) logLine('warn', 'sys', info.error);
    }
  } catch (e) {
    toast('初始化失败：' + (e.message || e), 'err');
  } finally {
    overlay(false);
  }
  await refreshStatus(true);

  // 窗口切回来时自动对齐一次状态。
  //   前端不做轮询（省资源），但盘可能被别处改过（脚本 / 另一个窗口 / 手滑点了「还原」），
  //   切回来还对着旧快照判断，正是容易再点错按钮的场合 —— 这一下把它拉回来。
  //   非 force：20 秒内探过的卡直接吃缓存，回来就有一版对齐，不必重跑 8 目标探测。
  //   用 document 而不是 window：两个 GUI 自检 stub 都 stub 了 document.addEventListener，
  //   而 check_render 里的 window 只有 { pojia }，绑 window.addEventListener 会直接抛。
  if (typeof document !== 'undefined' && document.addEventListener) {
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) refreshStatus(true);
    });
  }
}

// ────────────────────────── 执行动作 ──────────────────────────

async function doRun(action) {
  if (state.busy) return;
  let tg = targets();
  if (!tg.length) { toast('先在卡片上勾选目标', 'warn'); return; }

  // DSH 下拉里两项都没勾 = 这个目标这次不参与（下拉写的是"都不做"，不是"跳过按钮"）
  const dshOff = !state.opts.prompt && !state.opts.preset;
  if (dshOff && hasDsh(tg)) {
    tg = tg.filter((k) => k !== 'dsh' && k !== 'dsh-desktop');
    logLine('dim', 'sys', 'DSH 下拉是「都不做」—— 本次跳过 DSH');
  }
  if (!tg.length) {
    toast(dshOff ? 'DSH 下拉是「都不做」，也没有别的目标可跑' : '先在卡片上勾选目标', 'warn');
    return;
  }

  if (action === 'revert') {
    if (!window.confirm(revertAsk(tg))) return;
  }

  // 别把 revert 叫「恢复」—— 它的语义是**撤掉破甲、还原成官方原版**，
  //   「恢复」会被读成"恢复破甲"，验证把人坑过一次（以为在修，其实在拆）。
  const names = { apply: '部署', revert: '还原', dry: '预演' };
  const flags = flagsForOpts();
  const hint = (hasDsh(tg) && flags.length)
    ? (flags[0] === 'no-preset' ? '（DSH 只还原本体，破甲模式留着）'
                                : '（DSH 只动破甲模式，本体不碰）')
    : '';
  setBusy(true, names[action] + '中……');
  logHead('▶ ' + names[action] + ' → ' + tg.join(' / ') + hint);

  try {
    const r = await api.run(action, tg, flags);
    if (r && r.ok) toast(names[action] + '搞定，漂亮', 'ok');
    else toast(names[action] + '跑完了，但甩了个非零退出码 ' + ((r && r.code) != null ? r.code : '?') + '，日志里瞅瞅', 'warn');
  } catch (e) {
    toast(names[action] + '翻车了：' + (e.message || e), 'err');
  } finally {
    setBusy(false);
    // 只重探本次动过的目标，且强制绕过缓存 —— 别的目标沿用缓存，不陪跑一次全量
    await refreshStatus(true, new Set(tg), true);
  }
}

// 还原前的二次确认 —— 按 DSH 下拉的档位说清会动到哪部分
// 首行必须把「这是撤除、不是部署」说死：验证有人把 revert 当成
//   "恢复破甲"连点两次，每次都在拆。注意「破甲模式」四字只能在 hasDsh 分支里出现
//   （check_render 有断言 revertAsk(['wb']) 不含它）。
function revertAsk(tg) {
  let s = '这是【撤除】，不是部署 —— 会把 [' + tg.join(' / ') + '] 还原成官方原版。\n'
        + '（想重新打上，请点工具栏左边的「部署」）\n\n'
        + '每个目标会从各自的备份还原，部署产物会被移除。';
  if (!hasDsh(tg)) return s;
  const { prompt, preset } = state.opts;
  if (prompt && preset) {
    s += '\n\nDSH 两项都勾着：本体提示词会还原，破甲模式目录也会一并删掉。';
  } else if (prompt) {
    s += '\n\nDSH 只勾了「注入提示词」：只还原本体，破甲模式目录留着。';
  } else {
    s += '\n\nDSH 只勾了「增加破甲模式」：只移除破甲模式目录，本体的提示词补丁保持不动。';
  }
  return s;
}

async function doTest() {
  if (state.busy) return;
  const tg = targets();
  if (!tg.length) { toast('先在卡片上勾选目标', 'warn'); return; }
  setBusy(true, '体检中……');
  logHead('▶ 体检（验证） → ' + tg.join(' / '));
  try {
    const d = await api.test(tg);
    if (d.error) { toast('体检没做成：' + d.error, 'err'); return; }
    (d.items || []).forEach((it) => logLine(it.level, 'test', it.text));
    toast(d.ok ? '体检全过' : '体检查出问题（FAIL/WARN），日志里瞅瞅', d.ok ? 'ok' : 'warn');
    setStatus(d.ok ? '体检通过' : '体检没过', d.ok ? 'ok' : 'warn');
  } catch (e) {
    toast('体检出错：' + (e.message || e), 'err');
  } finally {
    setBusy(false);
    // 体检不改盘，走节流版即可（20 秒内的探测结果直接复用）
    await refreshStatus(true);
  }
}

// ────────────────────────── 破甲模式 ──────────────────────────
// 它**不是独立卡片、也不走独立按钮** —— 它是 DSH 卡片里那个下拉的两个勾选框之一
// （「增加破甲模式」）。勾着就用工具条上的 部署 / 恢复 连带处理：
//   部署 → python 破甲一键通.py --dsh-preset deploy --yes
//   恢复 → python 破甲一键通.py --dsh-preset remove --yes
// 想单独建 / 单独删：只勾「增加破甲模式」（此时桥会传 preset-only，本体一个字节不碰）。
// 命令行通道仍在：`python gui/bridge.py run preset-deploy|preset-remove|preset-status`。

// ────────────────────────── 归一预览 ──────────────────────────

async function doRoute() {
  const s = $('routeInput').value.trim();
  const out = $('routeOut');
  out.innerHTML = '';
  if (!s) return;
  const d = await api.route(s);
  if (d.error) {
    const c = document.createElement('span');
    c.className = 'chip warn';
    c.textContent = d.error;
    out.appendChild(c);
    return;
  }
  if (d.washed && d.washed.length) {
    const c = document.createElement('span');
    c.className = 'chip mute';
    c.textContent = '洗口：' + d.washed.join('、');
    out.appendChild(c);
  }
  if (d.miss) {
    const c = document.createElement('span');
    c.className = 'chip warn';
    c.textContent = '未命中归一表 —— 建议补一行这种写法';
    out.appendChild(c);
    return;
  }
  d.hits.forEach((h, i) => {
    const c = document.createElement('span');
    c.className = 'chip' + (i === 0 ? ' main' : '');
    c.textContent = '「' + h.frag + '」 → ' + h.term + ' → 【' + h.cat + '】';
    out.appendChild(c);
  });
}

// ────────────────────────── 事件绑定 ──────────────────────────

// ────────────────────────── 部署历史（事务）──────────────────────────
// 看得到改动、查得到完整性、回得去。
// 每次 apply / revert 留一份快照；回退前先确认现场没被别的操作动过 —— 动过就中止。

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

const TX_STATE = {
  applied: ['ok', '已应用'], restored: ['dim', '已回退'],
  partial: ['warn', '部分完成'], 'rolled-back': ['warn', '已回滚'],
  preparing: ['dim', '进行中'],
};

async function openTx() {
  $('txModal').classList.add('on');
  await loadTx();
}

function closeTx() { $('txModal').classList.remove('on'); }

async function loadTx() {
  const body = $('txBody');
  body.innerHTML = '<div class="txempty">正在读取……</div>';
  let d = null;
  try { d = await api.tx(['list']); } catch (e) { d = { ok: false, error: String(e.message || e) }; }
  if (!d || d.ok === false) {
    body.innerHTML = '<div class="txempty">读取失败：' + esc((d && d.error) || '未知错误') + '</div>';
    return;
  }
  $('txRoot').textContent = d.root || '';
  const items = d.items || [];
  if (!items.length) {
    body.innerHTML = '<div class="txempty">还没有部署记录 —— 跑一次「部署」或「恢复」就会出现。</div>';
    return;
  }
  body.innerHTML = '';
  items.forEach((r) => {
    const pair = TX_STATE[r.status] || ['dim', r.status];
    const row = document.createElement('div');
    row.className = 'txrow';
    row.dataset.id = r.short;
    row.innerHTML =
      '<span class="txid">' + esc(r.short) + '</span>' +
      '<span class="txseat">' + esc(r.seat) + '</span>' +
      '<span class="txat">' + esc(String(r.at || '').slice(5, 16)) + '</span>' +
      '<span class="txfiles">' + r.changed + ' / ' + r.files + '</span>' +
      '<span class="txstate st-' + pair[0] + '">' + esc(pair[1]) + '</span>' +
      '<div class="txspacer"></div>' +
      '<button class="minibtn" data-txact="verify">校验</button>' +
      '<button class="minibtn" data-txact="restore">回退</button>';
    body.appendChild(row);
  });
}

/* ── 写入预览（先预览再确认写入）── */
const DIFF_KINDS = new Set(['codex', 'zcode', 'claude', 'grok', 'gemini', 'hermes']);

async function doDiff() {
  if (state.busy) return;
  const tg = targets().filter((k) => DIFF_KINDS.has(k));
  const skipped = targets().filter((k) => !DIFF_KINDS.has(k));
  if (!tg.length) {
    toast(skipped.length ? '所选目标（' + skipped.join('/') + '）文件多，用「预演」看清单' : '先在卡片上勾选目标', 'warn');
    return;
  }
  if (skipped.length) logLine('dim', 'sys', '这几个（' + skipped.join('/') + '）文件太多，对照表装不下 —— 点「预演」看清单');
  $('diffModal').classList.add('on');
  const body = $('diffBody');
  body.innerHTML = '<div class="txempty">正在对比……</div>';
  $('diffTarget').textContent = tg.join(' / ');
  body.innerHTML = '';
  for (const k of tg) {
    let d = null;
    try { d = await api.diff([k]); } catch (e) { d = { error: String(e.message || e) }; }
    const item = document.createElement('div');
    item.className = 'diffitem';
    if (!d || d.error) {
      item.innerHTML = '<div class="diffpath">' + esc(k) + '</div><div class="diffunchanged">读取失败：' +
        esc((d && d.error) || '未知错误') + '</div>';
      body.appendChild(item);
      continue;
    }
    if (d.support !== 'full') {
      item.innerHTML = '<div class="diffpath">' + esc(d.label || k) + '</div><div class="diffunchanged">' +
        esc(d.note || '该目标不支持全文预览') + '</div>';
      body.appendChild(item);
      continue;
    }
    item.innerHTML = '<div class="diffpath">' + esc(d.label) + '</div>';
    body.appendChild(item);
    (d.items || []).forEach((it) => {
      const box = document.createElement('div');
      let html = '<div class="diffpath" style="color:var(--fg)">' + esc(it.path) + '</div>' +
        '<div class="diffnote">' + esc(it.note || '') + '</div>';
      if (it.unchanged) {
        html += '<div class="diffunchanged">内容一模一样，一个字都不用动</div>';
      } else {
        html += '<div class="diffhead"><span>落盘现状</span><span>部署写入</span></div>' +
          '<div class="diffgrid">' + (it.rows || []).map((r) =>
            '<div class="drow t-' + r.t + '"><span class="l">' + esc(r.l) + '</span>' +
            '<span class="r">' + esc(r.r) + '</span></div>').join('') + '</div>';
      }
      box.innerHTML = html;
      item.appendChild(box);
    });
  }
}

function closeDiff() { $('diffModal').classList.remove('on'); }

async function onTxRowClick(e) {  const btn = e.target.closest('button[data-txact]');
  if (!btn) return;
  const row = btn.closest('.txrow');
  const id = row && row.dataset.id;
  if (!id) return;
  const act = btn.dataset.txact;

  if (act === 'verify') {
    btn.disabled = true;
    const d = await api.tx(['verify', id]);
    btn.disabled = false;
    if (!d.ok) { toast('校验失败：' + (d.error || ''), 'err'); return; }
    const rep = d.report;
    logHead('快照校验 ' + id + ' · ' + rep.seat + ' · ' + rep.at);
    (rep.files || []).forEach((f) => {
      const snap = f.snapshot_ok === null ? '无底' : (f.snapshot_ok ? '完整' : '缺失');
      const cur = f.current_ok === null ? '—' : (f.current_ok ? '一致' : '已变');
      logLine(f.snapshot_ok === false || f.current_ok === false ? 'warn' : 'ok', 'tx',
        f.path + '   快照:' + snap + '  当前:' + cur);
    });
    toast(rep.ok ? '快照完整、落盘态一致' : '有不一致项，见日志', rep.ok ? '' : 'warn');
    return;
  }

  if (act === 'restore') {
    if (!confirm('回退到 ' + id + ' 这次部署之前？\n\n· 文件被别的操作改过时会中止，不硬覆盖\n· 目标程序需要重启才会重新读取文件')) return;
    btn.disabled = true;
    const d = await api.tx(['restore', id]);
    btn.disabled = false;
    if (!d.ok) {
      logLine('err', 'tx', '回退中止：' + (d.error || ''));
      toast('回退中止 —— 见日志', 'err');
      return;
    }
    logLine('ok', 'tx', '已回退 ' + id + '：还原 ' + d.result.restored + ' 个文件（状态 ' + d.result.status + '）');
    toast('已回退 ' + d.result.restored + ' 个文件', '');
    await loadTx();
    // 回退真改了盘，且改的范围不一定就是当前勾选 —— 全量强制重探一次
    refreshStatus(true, null, true);
  }
}

async function doTxPurge() {
  if (!confirm('清理旧快照，只保留最近 12 次？')) return;
  const d = await api.tx(['purge']);
  if (d && d.ok) { toast('已清理 ' + d.dropped + ' 个旧快照', ''); await loadTx(); }
  else toast('清理失败：' + ((d && d.error) || ''), 'err');
}

function bind() {
  $('btnDeploy').addEventListener('click', () => doRun('apply'));
  $('btnDry').addEventListener('click', () => doRun('dry'));
  $('btnRevert').addEventListener('click', () => doRun('revert'));
  $('btnTest').addEventListener('click', doTest);
  $('btnRefresh').addEventListener('click', () => refreshStatus(false, null, true));

  document.querySelectorAll('.chipbtn[data-sel]').forEach((b) => {
    b.addEventListener('click', () => {
      const m = b.dataset.sel;
      // 只认 ORDER 里的真目标 —— 任何非目标卡片都不该被"全选"卷进来
      const all = state.cards.map((c) => c.key).filter((k) => ORDER.includes(k));
      if (m === 'all') state.selected = new Set(all);
      else if (m === 'none') state.selected = new Set();
      else state.selected = new Set(all.filter((k) => !state.selected.has(k)));
      renderCards(state.cards);
      updateToolbarHint();
    });
  });

  document.querySelectorAll('.wbtn').forEach((b) => {
    b.addEventListener('click', () => api.win(b.dataset.win));
  });

  $('btnLogClear').addEventListener('click', () => {
    $('logBody').innerHTML = '';
    state.logCount = 0;
    $('logCount').textContent = '0';
  });

  $('btnCollapse').addEventListener('click', () => {
    const hid = document.querySelector('.workspace').classList.toggle('loghidden');
    $('logPanel').classList.toggle('collapsed', hid);
    $('btnCollapse').textContent = hid ? '\u21E4' : '\u21E5';
    $('btnCollapse').title = hid ? '展开日志' : '折叠日志';
  });

  $('btnLogFile').addEventListener('click', async () => {
    const r = await api.readLog();
    if (!r.ok) { toast('读不到日志：' + (r.error || ''), 'err'); return; }
    logHead('状态/破甲日志.txt（末尾 ' + r.lines.length + ' 行）');
    r.lines.forEach((ln) => { if (ln.trim()) logLine('dim', 'file', ln); });
  });

  $('btnConsole').addEventListener('click', async () => {
    const r = await api.openConsole();
    if (!r.ok) toast('打不开控制台：' + (r.error || ''), 'err');
  });

  const changePy = async () => {
    const r = await api.pickPython();
    if (r.canceled) return;
    if (!r.ok) { toast(r.error || '设置失败', 'err'); return; }
    toast('已切换解释器：' + r.python, 'ok');
    $('sbPython').textContent = r.python.split('\\').pop() + ' · ' + r.version;
    // 换了解释器，之前所有探测结果都不可信 —— 强制全量重探
    await refreshStatus(true, null, true);
  };
  $('btnPy').addEventListener('click', changePy);
  $('sbPython').addEventListener('click', changePy);

  $('sbRoot').addEventListener('click', () => {
    if (state.meta && state.meta.root) api.openPath(state.meta.root);
  });

  $('btnRoute').addEventListener('click', doRoute);

  $('btnTx').addEventListener('click', openTx);
  $('btnTxClose').addEventListener('click', closeTx);
  $('btnTxReload').addEventListener('click', loadTx);
  $('btnTxPurge').addEventListener('click', doTxPurge);
  $('txModal').addEventListener('click', (e) => { if (e.target.id === 'txModal') closeTx(); });
  $('txBody').addEventListener('click', onTxRowClick);
  $('btnDiff').addEventListener('click', doDiff);
  $('btnDiffClose').addEventListener('click', closeDiff);
  $('diffModal').addEventListener('click', (e) => { if (e.target.id === 'diffModal') closeDiff(); });
  $('routeInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') doRoute(); });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'F5') { e.preventDefault(); refreshStatus(false, null, true); }
    if (e.key === 'F9') { e.preventDefault(); doTest(); }
  });

  const GO_WORDS = ['撸起袖子开工 → ', '学姐上手了 → ', '轮到它了 → ', '这就动手 → ', '安排上 → '];
  const goWord = () => GO_WORDS[Math.floor(Math.random() * GO_WORDS.length)];
  const DONE_OK = ['收工，漂亮', '干完了，干净利落', '搞定，没留尾巴', '活儿齐了'];
  const doneOk = () => DONE_OK[Math.floor(Math.random() * DONE_OK.length)];

  api.onLog((evt) => {
    if (!evt) return;
    if (evt.type === 'line') logLine(evt.level, evt.target, evt.text);
    else if (evt.type === 'step') logLine('step', evt.target, '── ' + goWord() + evt.action + ' ──');
    else if (evt.type === 'done') logLine(evt.code ? 'err' : 'ok', evt.target, evt.code ? ('翻车了 · 退出码 ' + evt.code) : doneOk());
    else if (evt.type === 'start') logHead('开工咯：' + evt.action + ' → ' + (evt.targets || []).join(' / '));
    else if (evt.type === 'exit' && evt.code) logLine('err', 'sys', '整体退出码 ' + evt.code + '（有目标没干利索，往上翻翻）');
  });

  // ── DSH卡片里的「破甲模式」下拉 ──
  //   控件本体在 buildCard 里建、事件也在那儿绑（卡片会 upsert 重建）。
  //   这里只负责一件事：启动时把 state.opts 的回显画上去。
  paintDshOpts();
}

loadOpts();
bind();
boot();
