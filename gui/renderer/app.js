// Muse 工作台 · 渲染层逻辑 —— 2077 版：左菜单四视图 / 块状席位卡 / 部署⇄还原滑块 / 底部日志
const $ = (id) => document.getElementById(id);
const api = window.pojia;

const STATE_MAP = {
  current: { cls: 'on',     label: '已部署' },
  stale:   { cls: '',       label: '未部署' },
  none:    { cls: '',       label: '未部署' },
  broken:  { cls: 'err',    label: '未安装 / 异常' },
  foreign: { cls: '',       label: '外部配置' },
  warn:    { cls: '',       label: '部分部署' },
  orphan:  { cls: '',       label: 'CLI 未检测到' },
};
// 滑块态：卡上的部署状态 → 开关位置 + 文案
const SLIDER_STATE = {
  current: ['s-current', '已部署'],
  stale:   ['s-missing', '未部署'],
  none:    ['s-missing', '未部署'],
  broken:  ['s-missing', '未安装'],
  foreign: ['s-unknown', '外部'],
  warn:    ['s-stale',   '部分'],
  orphan:  ['s-unknown', '未检'],
};
const LEVEL_CLS = { ok: 'lv-ok', warn: 'lv-warn', err: 'lv-err', info: 'lv-info', dim: 'lv-dim', head: 'lv-info', step: 'lv-info' };
const ORDER = ['codex', 'dsh', 'dsh-desktop', 'wb', 'zcode', 'claude', 'hermes'];
const LABELS = {
  codex: 'ChatGPT', dsh: 'DSH (Web/CLI)', 'dsh-desktop': 'DSH 桌面版',
  wb: 'WorkBuddy', zcode: 'ZCode (Z.ai)',
  claude: 'Claude Code', hermes: 'Hermes Agent',
};
// 桌面版卡执行时折回 dsh（bridge 的 CARD_ONLY），tx 快照的 seat 也是 dsh
const REAL_SEAT = { 'dsh-desktop': 'dsh' };
const seatOf = (k) => REAL_SEAT[k] || k;

const OPT_KEY = 'pojia.opts';
const VIEW_TITLES = { deploy: '部署目标', skills: '技能管理', prompts: '指令提示词', settings: '设置', about: '关于' };

// DSH 只有一条提示词通道（~/.dsh/AGENTS.md）。这个下拉决定「这次动哪些」：
//   both    = 写 AGENTS.md + 投放 skills（默认）
//   prompt  = 只写 AGENTS.md
//   skills  = 只投放 skills
//   none    = 这次跳过 DSH
function optMode(o) {
  if (o.prompt && o.skills) return 'both';
  if (o.prompt) return 'prompt';
  if (o.skills) return 'skills';
  return 'none';
}
const MODE_TEXT = {
  both: '提示词 + skills', prompt: '只写提示词',
  skills: '只投放 skills', none: '都不做',
};
function flagsForOpts() {
  const { prompt, skills } = state.opts;
  if (prompt && !skills) return ['no-skills'];
  if (!prompt && skills) return ['no-agents'];
  return [];
}

const state = {
  meta: null,
  view: 'deploy',
  cards: [],
  selected: new Set(ORDER),
  busy: false,
  logCount: 0,
  opts: { prompt: true, skills: true },
  _lastRefreshAt: 0,
  _refreshTimer: 0,
  _queuedKeys: null,
  _pendingRefresh: null,
  _cardCache: new Map(),
};

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
    if (typeof d.skills === 'boolean') state.opts.skills = d.skills;
    if (typeof d.prompt === 'boolean') state.opts.prompt = d.prompt;
  } catch (_) { /* 没存过就用默认 */ }
}
function saveOpts() {
  try { localStorage.setItem(OPT_KEY, JSON.stringify(state.opts)); } catch (_) { /* 隐私模式 */ }
}

function targets() { return [...state.selected]; }
function hasDsh(tg) { return tg.includes('dsh') || tg.includes('dsh-desktop'); }

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
  document.querySelectorAll('.toolbar .btn, .btn.primary, .btn.danger').forEach((b) => { b.disabled = busy; });
  const dot = $('sbDot');
  dot.className = 'dot ' + (busy ? 'busy' : '');
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

// ────────────────────────── 日志（沉底通栏） ──────────────────────────

// 日志行：照 syslog / journalctl 的样子加时间戳 + 级别徽标 + 可着色文本。
// 颜色只标级别，正文保持正文色；error 整行淡红底，warn 淡黄底。
const LEVEL_BADGE = {
  ok:    ['OK  ', 'lbadge lb-ok'],
  info:  ['INFO', 'lbadge lb-info'],
  head:  ['====', 'lbadge lb-head'],
  step:  ['STEP', 'lbadge lb-step'],
  warn:  ['WARN', 'lbadge lb-warn'],
  err:   ['ERR ', 'lbadge lb-err'],
  dim:   ['····', 'lbadge lb-dim'],
};
function logStamp() {
  const d = new Date();
  const p = (n, w) => String(n).padStart(w || 2, '0');
  return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds())
    + '.' + p(d.getMilliseconds(), 3);
}
function logLine(level, tag, text) {
  const body = $('logBody');
  if (state.logCount === 0) body.innerHTML = '';
  const row = document.createElement('div');
  const lv = LEVEL_CLS[level] ? level : 'info';
  // 类名写成完整字面量（接口契约自检会静态扫 class 名）
  const LV_ROW = {
    ok: 'lrow lv-ok', warn: 'lrow lv-warn', err: 'lrow lv-err',
    info: 'lrow lv-info', dim: 'lrow lv-dim',
    head: 'lrow lv-head lv-info', step: 'lrow lv-step lv-info',
  };
  row.className = LV_ROW[lv] || 'lrow lv-info';
  const [badge, bcls] = LEVEL_BADGE[lv] || LEVEL_BADGE.info;
  const ts = document.createElement('span');
  ts.className = 'lts';
  ts.textContent = logStamp();
  const b = document.createElement('span');
  b.className = bcls;
  b.textContent = badge;
  const t = document.createElement('span');
  t.className = 'ltag';
  t.textContent = tag || '';
  const x = document.createElement('span');
  x.className = 'ltext';
  x.textContent = text;
  row.appendChild(ts);
  row.appendChild(b);
  row.appendChild(t);
  row.appendChild(x);
  body.appendChild(row);
  state.logCount += 1;
  $('logCount').textContent = state.logCount;
  while (body.children.length > 2600) body.removeChild(body.firstChild);
  if ($('autoScroll').checked) body.scrollTop = body.scrollHeight;
}
function logHead(text) { logLine('head', '—', text); }

// ────────────────────────── 视图切换 ──────────────────────────

function switchView(v) {
  state.view = v;
  document.querySelectorAll('.snav').forEach((b) => b.classList.toggle('on', b.dataset.view === v));
  document.querySelectorAll('.view').forEach((s) => s.classList.toggle('on', s.id === 'view-' + v));
  $('viewTitle').textContent = VIEW_TITLES[v] || v;
  $('viewTitle').classList.add('gtitle');
  if (v === 'skills' && !$('skBody').dataset.loaded) loadSkills();
  if (v === 'prompts') {
    if (!PROMPTS_INITED) initPrompts();
    // 每次进来都重扫一遍：部署 / 还原刚改过磁盘，缓存的列表会显示旧开关。
    // （初始化那次自己会扫，别重复调）
    else if (PM_DOCS.length) refreshDocs();
  }
}

// ────────────────────────── 席位卡（块状 + 滑块） ──────────────────────────

function renderCards(cards) {
  state.cards = cards.slice().sort((a, b) => ORDER.indexOf(a.key) - ORDER.indexOf(b.key));
  const wrap = $('cards');
  if (!state.cards.length) return;   // 骨架阶段没数据就别动
  const live = new Map([...wrap.querySelectorAll('.card')].map((n) => [n.dataset.key, n]));
  if (state.cards.every((c) => live.has(c.key) && !live.get(c.key).classList.contains('skeleton'))) {
    state.cards.forEach((c) => updateCard(live.get(c.key), c));
    return;
  }
  wrap.innerHTML = '';
  state.cards.forEach((c) => wrap.appendChild(buildCard(c)));
}

function renderSkeleton(keys) {
  const wrap = $('cards');
  wrap.innerHTML = '';
  state.cards = [];
  keys.forEach((k) => {
    const el = document.createElement('article');
    el.className = 'card skeleton';
    el.dataset.key = k;
    const ac = (BRAND[k] || {}).accent;
    if (ac && el.style.setProperty) el.style.setProperty('--seat-accent', ac);
    const top = document.createElement('div');
    top.className = 'ctop';
    const idx = document.createElement('span');
    idx.className = 'cindex';
    idx.textContent = String(ORDER.indexOf(k) + 1).padStart(2, '0') + ' / SEAT';
    const st = document.createElement('span');
    st.className = 'cstatus';
    st.textContent = 'SCAN';
    top.appendChild(idx);
    top.appendChild(st);
    const head = document.createElement('div');
    head.className = 'chead';
    const img = document.createElement('img');
    img.className = 'clogo';
    img.src = brandIcon(k);
    img.width = 26; img.height = 26;
    const nm = document.createElement('span');
    nm.className = 'cname';
    nm.textContent = LABELS[k] || k;
    head.appendChild(img);
    head.appendChild(nm);
    el.appendChild(top);
    el.appendChild(head);
    const home = document.createElement('div');
    home.className = 'cmeta';
    home.textContent = '正在探测安装位置与部署文件……';
    el.appendChild(head);
    el.appendChild(home);
    wrap.appendChild(el);
  });
}

function upsertCard(c) {
  const wrap = $('cards');
  const old = wrap.querySelector('.card[data-key="' + c.key + '"]');
  if (old && !old.classList.contains('skeleton')) {
    updateCard(old, c);
  } else {
    const el = buildCard(c);
    if (old) wrap.replaceChild(el, old);
    else {
      const idx = ORDER.indexOf(c.key);
      const after = [...wrap.querySelectorAll('.card')]
        .find((n) => ORDER.indexOf(n.dataset.key) > idx);
      if (after) wrap.insertBefore(el, after); else wrap.appendChild(el);
    }
  }
  const i = state.cards.findIndex((x) => x.key === c.key);
  if (i >= 0) state.cards[i] = c; else state.cards.push(c);
}

// 就地更新：整个 body 重画但节点不换（保滚动 / hover）
function updateCard(el, c) {
  const map = STATE_MAP[c.state] || STATE_MAP.none;
  const on = state.selected.has(c.key);
  el.className = 'card ' + map.cls + (on ? '' : ' dimmed');
  const ac = (BRAND[c.key] || {}).accent;
  if (ac && el.style.setProperty) el.style.setProperty('--seat-accent', ac);
  const body = buildCardBody(c);
  el.replaceChildren(body);
}

function buildCard(c) {
  const map = STATE_MAP[c.state] || STATE_MAP.none;
  const el = document.createElement('article');
  el.className = 'card ' + map.cls;
  el.dataset.key = c.key;
  const ac = (BRAND[c.key] || {}).accent;
  if (ac && el.style.setProperty) el.style.setProperty('--seat-accent', ac);
  el.appendChild(buildCardBody(c));
  return el;
}

// 各席位用真实客户端图标（与指令页同一套，存在 renderer/icons/）。
// 只留强调色，卡片左上角那条色带还在用。
const BRAND = {
  codex:          { accent: '#10a37f', icon: 'codex.svg' },   // ChatGPT / OpenAI
  dsh:            { accent: '#4f7dff', icon: 'dsh.png' },     // DeepSeek Harness
  'dsh-desktop':  { accent: '#4f7dff', icon: 'dsh.png' },
  wb:             { accent: '#a56bff', icon: 'workbuddy.png' }, // WorkBuddy
  zcode:          { accent: '#3dffa0', icon: 'zcode.png' },   // ZCode
  claude:         { accent: '#ff8a3d', icon: 'claude.svg' },  // Claude Code
  hermes:         { accent: '#ff4fd8', icon: 'hermes.png' },  // Hermes
};
function brandIcon(key) {
  const b = BRAND[key];
  return b && b.icon ? 'icons/' + b.icon : '';
}

function buildCardBody(c) {
  const map = STATE_MAP[c.state] || STATE_MAP.none;
  const box = document.createElement('div');
  box.className = 'cbody';

  // 角标行：席位编号 / SEAT + 勾选 + 状态灯（席位卡顶部）
  const top = document.createElement('div');
  top.className = 'ctop';
  const idx = document.createElement('span');
  idx.className = 'cindex';
  idx.textContent = String(ORDER.indexOf(c.key) + 1).padStart(2, '0') + ' / SEAT';
  const chk = document.createElement('input');
  chk.type = 'checkbox';
  chk.checked = state.selected.has(c.key);
  chk.title = '纳入批量操作';
  chk.addEventListener('change', () => {
    if (chk.checked) state.selected.add(c.key); else state.selected.delete(c.key);
  });
  const st = document.createElement('span');
  st.className = 'cstatus';
  st.textContent = map.label;
  top.appendChild(idx);
  top.appendChild(chk);
  top.appendChild(st);

  // 标题行：logo + 名字 + 总览按钮
  const head = document.createElement('div');
  head.className = 'chead';
  const img = document.createElement('img');
  img.className = 'clogo';
  img.src = brandIcon(c.key);
  img.alt = c.key;
  img.width = 26; img.height = 26;
  const name = document.createElement('span');
  name.className = 'cname';
  name.textContent = c.label || LABELS[c.key] || c.key;
  const patchBtn = document.createElement('button');
  patchBtn.className = 'minibtn';
  patchBtn.textContent = '总览';
  patchBtn.title = '补丁总览：改了哪些文件、位置在哪、内容是什么、状态如何';
  patchBtn.addEventListener('click', () => openPatch(c.key));
  head.appendChild(img);
  head.appendChild(name);
  head.appendChild(patchBtn);

  // 简介：落点路径（卡片一句话说明 —— 一句话说明）
  const home = document.createElement('div');
  home.className = 'cmeta';
  home.textContent = c.home || '(未探测到路径)';
  home.title = c.home || '';
  home.style.cursor = c.home ? 'pointer' : '';
  home.addEventListener('click', () => { if (c.home) api.openPath(c.home); });

  box.appendChild(top);
  box.appendChild(head);
  box.appendChild(home);

  if (c.state === 'broken') {
    (c.rows || []).forEach((r) => {
      const m = document.createElement('div');
      m.className = 'cmissing';
      m.textContent = r.note || r.path || '';
      box.appendChild(m);
    });
  }

  // 指标
  const list = c.metrics || [];
  if (list.length) {
    const stats = document.createElement('div');
    stats.className = 'cstats';
    list.forEach((m) => {
      const cell = document.createElement('div');
      cell.className = 'cstat';
      const n = document.createElement('span');
      n.className = 'n ' + (m.ok ? 'ok' : 'bad');
      n.textContent = m.value;
      const t = document.createElement('span');
      t.className = 't';
      t.textContent = m.name;
      cell.appendChild(n);
      cell.appendChild(t);
      stats.appendChild(cell);
    });
    box.appendChild(stats);
  }

  // 明细行（部署文件 + 体检行）—— 默认收起，点「明细」展开
  const detail = document.createElement('div');
  detail.className = 'cdetail';
  detail.hidden = true;
  let nDet = 0;
  (c.files || []).forEach((f) => {
    if (!f.path) return;
    const base = (f.path || '').split(/[\\/]/).filter(Boolean).pop();
    const row = document.createElement('div');
    row.className = 'cmeta';
    row.textContent = (f.exists ? '▸ ' : '✗ ') + (f.role ? f.role + ' · ' : '') + base;
    row.title = f.path || '';
    row.addEventListener('click', () => { if (f.path) api.openPath(f.path); });
    detail.appendChild(row);
    nDet++;
  });
  (c.rows || []).forEach((r) => {
    const row = document.createElement('div');
    row.className = 'cmeta';
    row.textContent = (r.level === 'err' ? '✗ ' : r.level === 'warn' ? '△ ' : '· ') + (r.note || r.path || '');
    if (r.path) {
      row.title = r.path;
      row.addEventListener('click', () => api.openPath(r.path));
    }
    detail.appendChild(row);
    nDet++;
  });
  if (nDet) box.appendChild(detail);

  // DSH 动作下拉（两张 DSH 卡共享 state.opts）——放进动作排，和按钮同一行；
  // 不再单独占一行，否则 DSH 卡与其它卡的动作区/滑块对不齐。
  let optrow = null;
  if (c.key === 'dsh' || c.key === 'dsh-desktop') {
    optrow = document.createElement('div');
    optrow.className = 'copt';
    const lbl = document.createElement('span');
    lbl.className = 'sliderlbl';
    lbl.textContent = '动作';
    const sel = document.createElement('select');
    sel.className = 'skselect';
    sel.title = '本次动作：只写提示词 / 只投放 skills / 两者都做';
    Object.entries(MODE_TEXT).forEach(([v, t]) => {
      const o = document.createElement('option');
      o.value = v;
      o.textContent = t;
      if (optMode(state.opts) === v) o.selected = true;
      sel.appendChild(o);
    });
    sel.addEventListener('change', () => {
      state.opts.prompt = sel.value === 'both' || sel.value === 'prompt';
      state.opts.skills = sel.value === 'both' || sel.value === 'skills';
      saveOpts();
      logLine('dim', 'sys', 'DSH 本次动作 → ' + MODE_TEXT[sel.value]);
    });
    optrow.appendChild(lbl);
    optrow.appendChild(sel);
  }

  // ── 动作排：明细 · 路径 · 日志（各一行点击动作） ──
  const acts = document.createElement('div');
  acts.className = 'cactions';
  if (optrow) acts.appendChild(optrow);
  const mkAct = (label, fn, hot) => {
    const b = document.createElement('button');
    b.textContent = label;
    if (hot) b.className = 'hot';
    b.addEventListener('click', fn);
    return b;
  };
  const nFiles = (c.files || []).filter((f) => f.path).length;
  acts.appendChild(mkAct('明细 ' + (nDet || nFiles), () => {
    const d = box.querySelector('.cdetail');
    if (d) d.hidden = !d.hidden;
  }));
  acts.appendChild(mkAct('路径', () => { if (c.home) api.openPath(c.home); }));
  acts.appendChild(mkAct('总览', () => openPatch(c.key), true));
  box.appendChild(acts);

  // ── 滑块：部署 ⇄ 还原（还原走事务快照）──
  box.appendChild(buildSlider(c));
  return box;
}

function buildSlider(c) {
  const wrap = document.createElement('div');
  wrap.className = 'sliderwrap';

  const lblL = document.createElement('span');
  lblL.className = 'sliderlbl';
  lblL.textContent = '还原';

  const slider = document.createElement('div');
  slider.className = 'slider';
  slider.setAttribute('role', 'switch');
  slider.title = '拨到右边 = 部署破甲；拨回左边 = 从事务快照还原（无快照则退回常规还原）';
  const knob = document.createElement('span');
  knob.className = 'knob';
  knob.textContent = '⇄';
  slider.appendChild(knob);

  const st = document.createElement('span');
  const [scls, stxt] = SLIDER_STATE[c.state] || SLIDER_STATE.none;
  st.className = 'sliderstate ' + scls;
  st.textContent = stxt;

  const lblR = document.createElement('span');
  lblR.className = 'sliderlbl rgt';
  lblR.textContent = '部署';

  const isOn = c.state === 'current';
  slider.classList.toggle('on', isOn);
  slider.dataset.seat = c.key;

  // 视觉先行：点下去立刻翻滑块，别等 Python 进程 —— 和指令页的开关一致。
  // 真实状态以后端刷新为准；中途失败 refreshStatus 会把卡片改回去。
  const paint = (on, pending) => {
    slider.classList.toggle('on', on);
    slider.classList.toggle('busy', !!pending);
    const [cls, txt] = pending
      ? ['s-stale', on ? '部署中…' : '还原中…']
      : (SLIDER_STATE[on ? 'current' : 'none'] || SLIDER_STATE.none);
    st.className = 'sliderstate ' + cls;
    st.textContent = pending ? txt : (txt || '');
  };

  slider.addEventListener('click', () => {
    if (state.busy || slider.classList.contains('busy')) {
      toast('有活儿在跑，稍等', 'warn');
      return;
    }
    const on = slider.classList.contains('on');
    paint(!on, true);
    if (on) doSliderRevert(c); else doRun('apply', [c.key]);
  });

  wrap.appendChild(lblL);
  wrap.appendChild(slider);
  wrap.appendChild(st);
  wrap.appendChild(lblR);
  return wrap;
}

// ────────────────────────── 数据加载 ──────────────────────────

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

const REFRESH_GAP_MS = 5000;
const CARD_TTL_MS = 20000;

async function runRefresh(quiet, keys, force) {
  if (state.busy) { state._pendingRefresh = { keys: keys || null }; return; }
  const want = keys ? ORDER.filter((k) => keys.has(k)) : ORDER.slice();
  if (!want.length) { overlay(false); return; }

  state._lastRefreshAt = Date.now();
  setBusy(true, '正在检测状态……');

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
    if (!state.cards.length) renderSkeleton(want);
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
                  : (want.length + ' 个目标均已部署'), bad ? 'warn' : 'ok');
  } catch (e) {
    toast('检测出错：' + (e.message || e), 'err');
  } finally {
    setBusy(false);
    overlay(false);
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
    $('aboutVer').textContent = 'v' + ((info.meta && info.meta.version) || '?');
    $('aboutVer').textContent = 'v' + ((info.meta && info.meta.version) || '?');
    // 启动后静默查一次：版本号旁边标出新版本，不需要等人点开才知道
    checkUpdate(false).catch(() => {});
    $('setPy').textContent = info.python ? (info.python.split('\\').pop() + ' · ' + info.pythonVersion) : 'python 未找到';
    $('setPy').title = info.python || '未找到';
    $('setRoot').textContent = info.root || '';
    $('setTx').textContent = '状态\\tx\\（每次部署自动留档）';
    $('sbRoot2').textContent = info.root || '';
    $('sbRoot2').title = info.root || '';
    state.meta = info.meta || null;
    if (!info.python) {
      toast('没找到可用的 Python，去「设置」里指定解释器', 'err');
      logLine('err', 'sys', '未找到 Python 解释器 —— 到「设置」页手动指定。');
    } else {
      logHead('Muse v' + ((info.meta && info.meta.version) || '?') + ' · 解释器 ' + info.python);
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

  // 后台预热指令页六个 Agent 的列表缓存：首屏画完后跑，切换 Agent 时直接读缓存
  if (api.docsWarm) {
    setTimeout(() => { api.docsWarm().catch(() => {}); }, 400);
  }

  // 不再在窗口重新获得焦点时自动全量检测：检测只在部署/还原/切换指令/
  // 回退快照这些“文件真的变了”的动作之后跑。外部手改文件用「刷新」或 F5。
}

// ────────────────────────── 执行动作 ──────────────────────────

async function doRun(action, forcedTargets, silent) {
  if (state.busy) return;
  let tg = forcedTargets || targets();
  if (!tg.length) { toast('先在卡片上勾选目标', 'warn'); return; }

  const dshOff = !state.opts.prompt && !state.opts.skills;
  if (dshOff && hasDsh(tg) && !forcedTargets) {
    tg = tg.filter((k) => k !== 'dsh' && k !== 'dsh-desktop');
    logLine('dim', 'sys', 'DSH 选了「都不做」—— 本次跳过 DSH');
  }
  if (!tg.length) {
    toast('没有可执行的目标', 'warn');
    return;
  }

  if (action === 'revert' && !silent) {
    const ask = revertAsk(tg);
    const okToGo = await askConfirm({
      title: '批量还原',
      sub: tg.join(' / '),
      text: ask,
      items: ['逐文件从快照回拷，可再部署回来',
              '目标程序需要重启才会重新读取文件'],
      ok: '还原', danger: true,
    });
    if (!okToGo) return;
  }

  const names = { apply: '部署', revert: '还原', dry: '预演' };
  const flags = flagsForOpts();
  const hint = (hasDsh(tg) && flags.length)
    ? (flags[0] === 'no-skills' ? '（DSH 只写提示词，skills 不动）'
                                : '（DSH 只投放 skills，AGENTS.md 不碰）')
    : '';
  setBusy(true, names[action] + '中……');
  logHead('▶ ' + names[action] + ' → ' + tg.join(' / ') + hint);

  try {
    const r = await api.run(action, tg, flags);
    if (r && r.ok) toast(names[action] + '搞定，漂亮', 'ok');
    else toast(names[action] + '跑完了，但退出码 ' + ((r && r.code) != null ? r.code : '?') + '，日志里瞅瞅', 'warn');
  } catch (e) {
    toast(names[action] + '翻车了：' + (e.message || e), 'err');
  } finally {
    setBusy(false);
    await refreshStatus(true, new Set(tg), true);
  }
}

function revertAsk(tg) {
  let s = '这是【撤除】，不是部署 —— 会把 [' + tg.join(' / ') + '] 还原成官方原版。\n'
        + '（想重新打上，请点「部署」或把滑块拨到右边）\n\n'
        + '每个目标优先用最近一次部署的事务快照逐文件还原；没有快照时退回备份还原。';
  if (!hasDsh(tg)) return s;
  const mode = optMode(state.opts);
  if (mode === 'both') s += '\n\nDSH：AGENTS.md 标记块会摘掉，投放的 skills 也会一并撤下。';
  else if (mode === 'prompt') s += '\n\nDSH 只摘 AGENTS.md 标记块，skills 留着。';
  else if (mode === 'skills') s += '\n\nDSH 只撤投放的 skills，AGENTS.md 保持不动。';
  return s;
}

// 滑块拨回左边：直接跑该席位的常规还原（.bak 备份 / 标记块剥离）。
//
// 这里**不能**优先走 tx 快照回退：
//   · tx 只管"这次部署写过的文件"，对客户端自己后来改写的文件（WorkBuddy 的
//     app-config.json、Codex 的 config.toml）同样会判冲突并整体中止；
//   · 快照只是某一时刻的切片，拿它回拷等于把中间所有改动一起丢掉。
// 常规 revert 会剥离标记块、逐字节还原原稿，客户端自己改过的地方不动。
//
// silent=true：滑块已经自己做了视觉反馈，不再弹确认框；滑块拨动本身就是授权。
async function doSliderRevert(c) {
  if (state.busy) return;
  // ChatGPT 走 四落点（指令文件 / AGENTS.md / config.toml / SKILL.md），
  // 事务快照管的是项目内文件，对 ~/.codex 一无所知 —— 这一席直接走完整还原：
  // 备份回拷 + 摘指针 + 撤 skill，并把四路开关与指令页文档全部置为停用。
  if (c.key === 'codex') {
    logLine('dim', 'tx', 'ChatGPT 走四路还原：回拷备份、撤下投放的四路落点');
  } else {
    logLine('dim', 'tx', seatOf(c.key) + ' 走常规还原：剥离标记块 / 回拷备份，客户端自有改动不动');
  }
  return doRun('revert', [c.key], true);
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
    await refreshStatus(true);
  }
}

// ────────────────────────── 归一预览（界面已移除）──────────────────────────

async function doRoute() {
  // 侧栏底部的「归一预览」入口已删除；函数保留给将来的命令面板复用。
  return null;
}
// ────────────────────────── 技能管理视图 ──────────────────────────

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

async function loadSkills() {
  const seat = $('skSeat').value || 'dsh';
  const body = $('skBody');
  body.innerHTML = '<div class="pempty">正在读取 ' + esc(seat) + ' 的技能目录……</div>';
  $('skHint').textContent = '';
  let d = null;
  try { d = await api.patches(seat); } catch (e) { d = { ok: false, error: String(e.message || e) }; }
  if (!d || d.ok === false) {
    body.innerHTML = '<div class="pempty err">读取失败：' + esc((d && d.error) || '未知错误') + '</div>';
    return;
  }
  $('skBody').dataset.loaded = '1';
  const groups = d.groups || [];
  const skill = groups.find((g) => g.id === 'skill');
  const all = groups.flatMap((g) => g.rows || []);
  const nSkill = skill ? (skill.rows || []).length : 0;
  $('skHint').textContent = seat + ' · ' + all.length + ' 个登记文件 · 技能 ' + nSkill + ' 个';

  body.innerHTML = '';
  if (!all.length) {
    body.innerHTML = '<div class="pempty">这个席位没有登记任何部署文件（未安装或未部署）。</div>';
    return;
  }
  groups.forEach((g) => {
    const rows = g.rows || [];
    const card = document.createElement('div');
    card.className = 'skcard';
    const head = document.createElement('div');
    head.className = 'skname';
    head.textContent = g.name + '（' + rows.length + '）';
    card.appendChild(head);
    const tags = document.createElement('div');
    tags.className = 'sktags';
    rows.slice(0, 40).forEach((r) => {
      const pill = PILL[r.state] || PILL.missing;
      const t = document.createElement('span');
      t.className = 'sktag ' + (r.state === 'current' ? 'on' : '');
      const fn = (r.path || '').split(/[\\/]/).filter(Boolean).pop() || r.path;
      t.textContent = pill[1] + ' · ' + fn;
      t.title = r.path + '（' + r.role + '）— 点行可到「总览」里编辑';
      t.style.cursor = 'pointer';
      t.addEventListener('click', () => openPatch(seat));
      tags.appendChild(t);
    });
    if (rows.length > 40) {
      const more = document.createElement('span');
      more.className = 'sktag';
      more.textContent = '… 还有 ' + (rows.length - 40) + ' 个';
      tags.appendChild(more);
    }
    card.appendChild(tags);
    body.appendChild(card);
  });
}

/* ── 指令提示词管理视图── */

let PROMPTS_INITED = false;
let PM_AGENT = 'codex';    // 当前 Agent
let PM_DOCS = [];          // 当前 Agent 的全部 md
let PM_CATS = [];          // 分类（含计数）
let PM_CAT = '';           // 当前分类（默认取该 Agent 的第一个分类）
let PM_CURRENT = '';       // 当前编辑中的 id（相对路径）
let PM_ORIG = '';          // 读入时的原文
let PM_META = null;        // 当前编辑项的元信息

// 每个 Agent 的真实客户端图标（从本机安装包提取，见 renderer/icons/）
const PM_AGENTS = [
  ['codex', 'ChatGPT', 'codex.svg'],
  ['claude', 'Claude Code', 'claude.svg'],
  ['dsh', 'DSH', 'dsh.png'],
  ['workbuddy', 'WorkBuddy', 'workbuddy.png'],
  ['zcode', 'ZCode', 'zcode.png'],
  ['hermes', 'Hermes', 'hermes.png'],
];

function pmById(id) { return PM_DOCS.find((it) => it.id === id) || null; }

function paintPmAgents() {
  const box = $('pmAgents');
  box.innerHTML = '';
  PM_AGENTS.forEach(([id, label, icon]) => {
    const el = document.createElement('button');
    el.type = 'button';
    el.className = 'pmagent' + (id === PM_AGENT ? ' on' : '');
    el.dataset.agent = id;
    el.setAttribute('role', 'tab');
    el.setAttribute('aria-selected', id === PM_AGENT ? 'true' : 'false');
    const glyph = document.createElement('span');
    glyph.className = 'pmagent-glyph';
    const img = document.createElement('img');
    img.src = 'icons/' + icon;
    img.alt = '';
    img.width = 22;
    img.height = 22;
    img.draggable = false;
    glyph.appendChild(img);
    const nm = document.createElement('span');
    nm.className = 'pmagent-name';
    nm.textContent = label;
    el.appendChild(glyph);
    el.appendChild(nm);
    el.addEventListener('click', () => pickAgent(id));
    box.appendChild(el);
  });
}

function pickAgent(id) {
  if (PM_AGENT === id) return;
  PM_AGENT = id;
  PM_CAT = '';
  PM_CURRENT = '';
  paintPmAgents();
  refreshDocs();
}

function paintPmCats() {
  const box = $('pmCats');
  box.innerHTML = '';
  // 首个按钮固定为「全部」：按类别分组展示，避免所有条目堆成一整块
  const total = (PM_CATS || []).reduce((n, c) => n + (c.count || 0), 0);
  const allEl = document.createElement('button');
  allEl.type = 'button';
  allEl.className = 'pmcat' + (!PM_CAT || PM_CAT === 'all' ? ' on' : '');
  allEl.dataset.cat = 'all';
  const allNm = document.createElement('span');
  allNm.textContent = '全部';
  const allCt = document.createElement('small');
  allCt.textContent = String(total);
  allEl.appendChild(allNm);
  allEl.appendChild(allCt);
  allEl.addEventListener('click', () => { PM_CAT = 'all'; paintPmCats(); paintPmGrid(); });
  box.appendChild(allEl);

  PM_CATS.forEach((cat) => {
    const el = document.createElement('button');
    el.type = 'button';
    el.className = 'pmcat' + (cat.id === PM_CAT ? ' on' : '');
    el.dataset.cat = cat.id;
    el.setAttribute('aria-pressed', cat.id === PM_CAT ? 'true' : 'false');
    const nm = document.createElement('span');
    nm.textContent = cat.name;
    const ct = document.createElement('small');
    ct.textContent = String(cat.count || 0);
    el.appendChild(nm);
    el.appendChild(ct);
    el.addEventListener('click', () => { PM_CAT = cat.id; paintPmCats(); paintPmGrid(); });
    box.appendChild(el);
  });
  // 固定落点（AGENTS.md / config.toml / SKILL.md…）没有“新建”语义
  const nb = $('btnPmNew');
  if (nb) {
    const can = !PM_CAT || PM_CAT === 'instruction' || PM_CAT === 'custom';
    nb.disabled = !can;
    nb.title = can ? '在当前 Agent 自己的指令目录中新建'
                   : '当前分类是固定落点，不能新建；请切到「指令文件」';
  }
}

function pmCatName(id) {
  const hit = PM_CATS.find((c) => c.id === id);
  return hit ? hit.name : '其它';
}

// 调试入口（截图自检 / 排错用）
if (typeof window !== 'undefined') {
  window.__pmDebug = () => ({
    cat: PM_CAT, cats: (PM_CATS || []).map((c) => c.id),
    docs: (PM_DOCS || []).length,
    docCats: [...new Set((PM_DOCS || []).map((d) => d.cat))],
    agent: PM_AGENT,
  });
}

function paintPmGrid() {
  const box = $('pmGrid');
  box.innerHTML = '';
  // 默认「全部」时按类别分组显示，不再把所有条目堆成一整块
  const all = PM_CAT === 'all' || !PM_CAT;
  const list = all ? PM_DOCS.slice()
                   : PM_DOCS.filter((it) => it.cat === PM_CAT);
  const offN = list.filter((x) => !x.enabled).length;
  const bar = document.createElement('div');
  bar.className = 'pmbulk';
  const barTxt = document.createElement('span');
  barTxt.textContent = (all ? '全部' : (pmCatName(PM_CAT) || '当前分类')) + ' · '
    + list.length + ' 份' + (offN ? ' · 已停用 ' + offN : '');
  const onBtn = document.createElement('button');
  onBtn.className = 'minibtn';
  onBtn.textContent = '本类启用';
  onBtn.disabled = !list.some((x) => !x.enabled && !x.deployable);
  onBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleMany(list.filter((x) => !x.enabled && !x.deployable), true);
  });
  const offBtn = document.createElement('button');
  offBtn.className = 'minibtn';
  offBtn.textContent = '本类停用';
  offBtn.disabled = !list.some((x) => x.enabled && !x.deployable);
  offBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleMany(list.filter((x) => x.enabled && !x.deployable), false);
  });
  bar.appendChild(barTxt);
  const sp = document.createElement('div');
  sp.className = 'pmbulk-sp';
  bar.appendChild(sp);
  bar.appendChild(onBtn);
  bar.appendChild(offBtn);
  box.appendChild(bar);
  if (!list.length) {
    const e = document.createElement('div');
    e.className = 'pmempty';
    const tx = document.createElement('div');
    tx.textContent = '这个分类下还没有 Markdown 提示词';
    e.appendChild(tx);
    const imp = document.createElement('button');
    imp.type = 'button';
    imp.className = 'btn';
    imp.textContent = '导入 .md 指令';
    imp.addEventListener('click', importDoc);
    e.appendChild(imp);
    box.appendChild(e);
    return;
  }
  if (!all) {
    list.forEach((it) => box.appendChild(buildDocRow(it)));
    return;
  }
  // 「全部」视图：按分类分组，每组带标题与计数
  const order = (PM_CATS || []).map((c) => c.id);
  const groups = new Map();
  list.forEach((it) => {
    if (!groups.has(it.cat)) groups.set(it.cat, []);
    groups.get(it.cat).push(it);
  });
  const keys = [...groups.keys()].sort((a, b) => {
    const ia = order.indexOf(a), ib = order.indexOf(b);
    return (ia < 0 ? 999 : ia) - (ib < 0 ? 999 : ib);
  });
  keys.forEach((cat) => {
    const items = groups.get(cat);
    const h = document.createElement('div');
    h.className = 'pmgroup-head';
    const hn = document.createElement('span');
    hn.className = 'pmgroup-name';
    hn.textContent = pmCatName(cat);
    const hc = document.createElement('small');
    hc.textContent = String(items.length);
    h.appendChild(hn);
    h.appendChild(hc);
    box.appendChild(h);
    items.forEach((it) => box.appendChild(buildDocRow(it)));
  });
}

// 一张卡 = 一份文档，
//   头部 = 图标 + 标题 + 右侧开关
//   正文 = 说明（备注优先）
//   底部 = 状态徽标 / 路径体积 + 图标操作
// 点卡片只选中，编辑走铅笔图标 —— 拨开关不会把人带进编辑页。
function buildDocRow(it) {
  const row = document.createElement('article');
  row.className = 'pmrow' + (it.enabled ? '' : ' off-doc')
    + (it.id === PM_CURRENT ? ' on' : '');
  row.dataset.id = it.id;

  // 头部
  const head = document.createElement('div');
  head.className = 'pmrow-head';
  const heading = document.createElement('div');
  heading.className = 'pmrow-heading';
  const icon = document.createElement('span');
  icon.className = 'pmcicon';
  icon.textContent = 'MD';
  const title = document.createElement('div');
  title.className = 'pmcname';
  // 标题与文件名相同时只显示一次（别出现「自定义指令 · 自定义指令 · x.md」这类叠字）
  const tTxt = it.title ? it.title + ' · ' + it.name : it.name;
  const dupName = it.title && (it.title === it.name || it.title.endsWith('· ' + it.name));
  title.textContent = dupName ? it.title : tTxt;
  title.title = it.path;
  heading.appendChild(icon);
  heading.appendChild(title);
  const swWrap = document.createElement('div');
  swWrap.className = 'pmrow-switch';
  swWrap.appendChild(buildDocToggle(it));
  head.appendChild(heading);
  head.appendChild(swWrap);
  row.appendChild(head);

  // 正文
  const desc = document.createElement('div');
  desc.className = 'pmcdesc' + (it.memo ? ' has-memo' : '');
  desc.textContent = it.memo || rowDesc(it);
  desc.title = it.memo ? '备注：' + it.memo : '';
  row.appendChild(desc);

  // 底部
  const foot = document.createElement('div');
  foot.className = 'pmrow-foot';
  const meta = document.createElement('div');
  meta.className = 'pmrow-meta';
  // 类别标签（所有文件都有）：每张卡固定带上自己的类型与当前状态
  (it.labels || []).forEach((lb) => {
    if (!lb || !lb.tag) return;
    const b = document.createElement('span');
    b.className = PM_TAG_CLS[lb.tone] || PM_TAG_CLS[''];
    b.textContent = lb.tag;
    meta.appendChild(b);
  });
  if (it.generated) {
    const b = document.createElement('span');
    b.className = 'pmcbadge gen';
    b.textContent = '生成';
    meta.appendChild(b);
  }
  // 状态只由 bridge 给的 labels 表达一次 —— 这里不再补「注入中」徽标，
  // 否则指令源卡片会同时出现两个「注入中」（标签 + 徽标）。
  const path = document.createElement('span');
  path.className = 'pmcpath';
  // 分类名与标题/文件名重复时不再写一遍（config.toml · config.toml、AGENTS.md · AGENTS.md）
  const catName = pmCatName(it.cat);
  const dup = catName === it.name || catName === it.title
    || catName === (it.title ? it.title + ' · ' + it.name : it.name);
  if (!dup) {
    path.textContent = catName;
    path.title = it.id;
    meta.appendChild(path);
  }

  const btns = document.createElement('div');
  btns.className = 'pmcbtns';
  btns.appendChild(iconBtn('memo', '备注 · ' + it.name, () => editMemo(it), 'iconbtn'));
  btns.appendChild(iconBtn('pencil',
    (it.live ? '查看 / 编辑本机文件 · ' : '编辑 · ') + it.name,
    () => selectDoc(it.id), 'iconbtn'));
  // 指令候选：一键切到这一份（后端 irtoggle 会立刻写落点，不需要再点部署）
  if (it.deployable && !it.active) {
    const use = document.createElement('button');
    use.type = 'button';
    use.className = 'minibtn';
    use.textContent = '设为当前';
    use.title = '立即切到这份指令并写入当前 Agent 的落点';
    use.addEventListener('click', (e) => {
      e.stopPropagation();
      applyInstr(it.name, it);
    });
    btns.appendChild(use);
  }
  if (it.id.indexOf('seats/custom_') >= 0 || it.userDoc) {
    btns.appendChild(iconBtn('trash', '删除这份自定义提示词', () => deleteDoc(it), 'iconbtn danger'));
  }
  foot.appendChild(meta);
  foot.appendChild(btns);
  row.appendChild(foot);
  return row;
}

function rowDesc(it) {
  if (it.deployable) {
    // 状态已经由底部标签说过了，正文只讲"这份是什么"，不再重复「注入中」
    return it.enabled ? '这份会整份写进客户端，成为当前指令文件'
                      : '这份是备选指令源，拨右才部署';
  }
  if (!it.enabled) {
    return it.cat === 'skill' ? '已停用 · 下次部署会撤下这份 SKILL.md'
                              : '已停用 · 这一路不写盘';
  }
  return it.note || pmCatName(it.cat);
}

// 只刷新某一行，不动整张列表 —— 拨开关时列表不跳、不重建
function updateDocRow(it) {
  const row = $('pmGrid').querySelector('.pmrow[data-id="' + CSS.escape(it.id) + '"]');
  if (!row) return;
  row.classList.toggle('off-doc', !it.enabled);
  const input = row.querySelector('.pmswitch input');
  if (input) input.checked = !!it.enabled;
  const desc = row.querySelector('.pmcdesc');
  if (desc && !it.memo) {
    desc.textContent = rowDesc(it);
  }
  // 状态标签跟着翻：按标签文字改，**不动语气 class**。
  // 之前固定标 ok，拨左后「未部署」还是绿的 —— 状态和颜色对不上。
  row.querySelectorAll('.pmtag').forEach((el) => {
    const txt = el.textContent;
    let next = null;
    if (txt === '注入中' || txt === '未部署') next = it.enabled ? '注入中' : '未部署';
    else if (txt === '已投放' || txt === '未投放') next = it.enabled ? '已投放' : '未投放';
    else if (txt === '已启用' || txt === '已关闭') next = it.enabled ? '已启用' : '已关闭';
    if (next) el.textContent = next;
  });
}

// 标签语气 → class（写成完整字面量，接口契约自检能对得上）
const PM_TAG_CLS = {
  '': 'pmtag',
  info: 'pmtag pmtag-info',
  ok: 'pmtag pmtag-ok',
  warn: 'pmtag pmtag-warn',
  muted: 'pmtag pmtag-muted',
};

// ── 图标（lucide 路径，描边 1.9，统一描边）──
const ICONS = {
  pencil: '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
  trash: '<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M10 11v6"/><path d="M14 11v6"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/>',
  plus: '<path d="M12 5v14"/><path d="M5 12h14"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  undo: '<path d="M3 7v6h6"/><path d="M3.5 13a9 9 0 1 0 2.1-5.3L3 10"/>',
  save: '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z"/><path d="M17 21v-8H7v8"/><path d="M7 3v5h8"/>',
  back: '<path d="M19 12H5"/><path d="M12 19l-7-7 7-7"/>',
  spin: '<path d="M21 12a9 9 0 1 1-6.22-8.56"/>',
  memo: '<path d="M4 6h16"/><path d="M4 12h10"/><path d="M4 18h7"/>',
};

function svgIcon(name) {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" '
    + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (ICONS[name] || '') + '</svg>';
}

function iconBtn(name, label, fn, cls) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = cls || 'iconbtn';
  b.title = label;
  b.setAttribute('aria-label', label);
  b.innerHTML = svgIcon(name);
  b.addEventListener('click', (e) => { e.stopPropagation(); fn(); });
  return b;
}

// 每份文档的注入开关：拨右 = 参与注入 / 部署这一份，拨左 = 关掉。
// 直接在行内拨，不弹窗、不跳页；状态写盘后只更新本行。
function buildDocToggle(it) {
    const deployMode = !!it.deployable;
    const checked = deployMode ? !!it.active : !!it.enabled;
    const wrap = document.createElement('label');
    wrap.className = 'pmswitch';
    wrap.title = deployMode
      ? (checked ? '注入中：拨左关闭注入（删部署文件并摘掉 config 指针）'
                 : '拨右把它部署为客户端指令文件')
      : (it.enabled ? '已启用：重新部署后注入。拨左停用（只摘出清单，内容不动）'
                    : '已停用：不参与合成。拨右重新启用');
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = checked;
    input.setAttribute('role', 'switch');
    input.setAttribute('aria-label', (checked ? '停用 ' : '启用 ') + it.name);
    const track = document.createElement('span');
    track.className = 'pmswitch-track';
    wrap.appendChild(input);
    wrap.appendChild(track);
  input.addEventListener('click', (e) => e.stopPropagation());
  input.addEventListener('change', (e) => {
    e.stopPropagation();
    if (deployMode) {
      if (input.checked) {
        // 换源：另一份正在注入 → 日志里说明这次是替换
        const prev = PM_DOCS.find((x) => x.deployable && x.active && x.name !== it.name);
        if (prev) logLine('dim', 'prompt', '正在用 ' + it.name + ' 替换 ' + prev.name);
        applyInstr(it.name, it);
      } else {
        disableInstr(it.name);
      }
    } else {
      toggleDoc(it, input.checked);
    }
  });
  return wrap;
}

// 页面内输入弹窗：Electron 渲染进程不支持 window.prompt，所以自己画一个居中弹窗。
// Esc / 点遮罩 / 取消都能关；回车即保存。
let PM_INPUT_RESOLVE = null;

function askText(opts) {
  const o = opts || {};
  return new Promise((resolve) => {
    PM_INPUT_RESOLVE = resolve;
    $('pmInputTitle').textContent = o.title || '输入';
    $('pmInputSub').textContent = o.sub || '';
    $('pmInputHint').textContent = o.hint || '';
    const tagBox = $('pmInputTags');
    tagBox.innerHTML = '';
    (o.tags || []).forEach((t, i) => {
      const el = document.createElement('span');
      el.className = i === 0 ? 'pmtag pmtag-info' : 'pmtag';
      el.textContent = t;
      tagBox.appendChild(el);
    });
    tagBox.hidden = !(o.tags || []).length;
    $('pmInputLabel').textContent = o.label || '内容';
    const inp = $('pmInputText');
    // 已有备注要真的填进去 —— 清空 value 后再赋值，避免浏览器保留旧值
    inp.value = '';
    inp.value = o.value || '';
    inp.maxLength = o.max || 200;
    inp.placeholder = o.placeholder || '';
    // "当前备注" 行已移除：直接看输入框
    $('pmInputCount').textContent = inp.value.length + ' / ' + inp.maxLength;
    $('pmInputModal').classList.add('on');
    // 进入时只聚焦、不选中全文：光标落在末尾，接着往后补字即可
    setTimeout(() => {
      inp.focus();
      const n = inp.value.length;
      try { inp.setSelectionRange(n, n); } catch (_) { /* 非文本框时忽略 */ }
    }, 30);
  });
}

function closeAsk(value) {
  $('pmInputModal').classList.remove('on');
  const r = PM_INPUT_RESOLVE;
  PM_INPUT_RESOLVE = null;
  if (r) r(value);
}

// 页面内确认（同样不用系统弹窗）：items 是可选的要点列表。
let PM_CFM_RESOLVE = null;

function askConfirm(opts) {
  const o = opts || {};
  // 自检桩：check_boot.mjs 没有真人点按钮，用这个开关让确认直接通过
  if (typeof window !== 'undefined' && window.__pojiaAutoConfirm) return Promise.resolve(true);
  return new Promise((resolve) => {
    if (PM_CFM_RESOLVE) { const prev = PM_CFM_RESOLVE; PM_CFM_RESOLVE = null; prev(false); }
    PM_CFM_RESOLVE = resolve;
    $('cfmTitle').textContent = o.title || '确认';
    $('cfmSub').textContent = o.sub || '';
    $('cfmText').textContent = o.text || '';
    const list = $('cfmList');
    list.innerHTML = '';
    (o.items || []).forEach((t) => {
      const li = document.createElement('li');
      li.textContent = t;
      list.appendChild(li);
    });
    const ok = $('btnCfmOk');
    ok.textContent = o.ok || '确定';
    ok.className = 'btn ' + (o.danger ? 'danger' : 'primary');
    $('cfmSheet').classList.add('on');
  });
}

function closeConfirm(value) {
  $('cfmSheet').classList.remove('on');
  const r = PM_CFM_RESOLVE;
  PM_CFM_RESOLVE = null;
  if (r) r(!!value);
}

async function editMemo(it) {
  // 用列表里的最新对象：卡片节点可能持着旧快照
  it = PM_DOCS.find((x) => x.id === it.id) || it;
  const builtin = '';
  const next = await askText({
    title: '备注 · ' + (it.title ? it.title + '（' + it.name + '）' : it.name),
    sub: it.id,
    tags: (it.labels || []).map((l) => l.tag).filter(Boolean),
    hint: '留空即清除。备注只存在本地，不会写进源件、也不会被引擎读进注入内容。',
    label: '备注',
    value: it.memo || '',
    max: 200,
    placeholder: '例如：改之前先备份 / 这份是试验版',
  });
  if (next === null) return;
  let r = null;
  try { r = await api.docMemo(it.id, next, PM_AGENT); }
  catch (e) { r = { ok: false, error: String(e.message || e) }; }
  if (!r || r.ok === false) { toast('备注保存失败：' + ((r && r.error) || ''), 'err'); return; }
  toast(r.memo ? '备注已保存' : '备注已清除', 'ok');
  const cur = PM_DOCS.find((x) => x.id === it.id);
  if (cur) {
    cur.memo = r.memo || '';
    const row = $('pmGrid').querySelector('.pmrow[data-id="' + CSS.escape(it.id) + '"]');
    const desc = row && row.querySelector('.pmcdesc');
    if (desc) {
      desc.textContent = cur.memo || rowDesc(cur);
      desc.classList.toggle('has-memo', !!cur.memo);
      desc.title = cur.memo ? '备注：' + cur.memo : '';
    }
  }
}

async function toggleDoc(it, want) {
  if (want === !!it.enabled) return;
  // 就地翻状态：先让 UI 跟手，再落盘；失败才回滚
  it.enabled = want;
  updateDocRow(it);
  let r = null;
  try { r = await api.docToggle(it.id, PM_AGENT); }
  catch (e) { r = { ok: false, error: String(e.message || e) }; }
  if (!r || r.ok === false) {
    it.enabled = !want;
    updateDocRow(it);
    toast((want ? '启用' : '停用') + '失败：' + ((r && r.error) || ''), 'err');
  } else {
    toast(it.name + (want ? ' 已启用' : ' 已停用') + ' —— 重新部署后生效', 'ok');
    await refreshAgentStatus(PM_AGENT);
  }
  syncPmStatus();
}

async function toggleMany(list, want) {
  if (!list.length) return;
  const ids = list.map((x) => x.id);
  // 先整批就地翻，再一趟过桥写盘
  list.forEach((it) => { it.enabled = want; updateDocRow(it); });
  let r = null;
  try {
    r = await api.docToggleMany(list.map((it) => ({ id: it.id, on: want })), PM_AGENT);
  } catch (e) { r = { ok: false, error: String(e.message || e) }; }
  const failed = (r && r.failed) || [];
  if (!r || r.ok === false) {
    // 整批失败：全部回滚
    list.forEach((it) => { it.enabled = !want; updateDocRow(it); });
    toast((want ? '启用' : '停用') + '失败：' + ((r && r.error) || ''), 'err');
    syncPmStatus();
    return;
  }
  failed.forEach((f) => {
    const it = PM_DOCS.find((x) => x.id === f.id);
    if (it) { it.enabled = !want; updateDocRow(it); }
  });
  toast((want ? '已启用 ' : '已停用 ') + (ids.length - failed.length) + ' 份'
    + (failed.length ? '，' + failed.length + ' 份失败' : '') + ' —— 重新部署后生效',
    failed.length ? 'warn' : 'ok');
  syncPmStatus();
  paintPmBulk();
  if (r && r.ok !== false) await refreshAgentStatus(PM_AGENT);
}

// 只重算状态面板与批量条上的计数，不重建列表
function syncPmStatus() {
  const on = PM_DOCS.filter((x) => x.enabled).length;
  const off = PM_DOCS.length - on;
  // 可停用的文档：指令源（deployable）由自己的滑块管，其余都计入总开关
  const sw = PM_DOCS.filter((x) => !x.deployable);
  const swOn = sw.filter((x) => x.enabled).length;
  $('pmDeployInput').checked = sw.length > 0 && swOn === sw.length;
  $('pmDot').classList.toggle('on', on > 0);
  $('pmAgentHint').textContent = '';
  // 总开关的提示跟着数字走：WorkBuddy 页有 90 份，不再是「已启用 89 份」那种陈旧提示
  $('pmDeploySwitch').title = on === 0
    ? '全部文档已关闭：部署等于回官方原版（拨右全部启用）'
    : '总开关：拨左停用全部可停用的文档，拨右全部启用（' + on + ' 启用 / ' + off + ' 停用）';
}

function paintPmBulk() {
  const bar = $('pmGrid').querySelector('.pmbulk');
  if (!bar) return;
  const list = PM_DOCS.filter((it) => it.cat === PM_CAT);
  const offN = list.filter((x) => !x.enabled).length;
  const t = bar.querySelector('span');
  if (t) t.textContent = (pmCatName(PM_CAT) || '当前分类') + ' · ' + list.length + ' 份'
    + (offN ? ' · 已停用 ' + offN : '');
  const btns = bar.querySelectorAll('.minibtn');
  if (btns[0]) btns[0].disabled = !list.some((x) => !x.enabled && !x.deployable);
  if (btns[1]) btns[1].disabled = !list.some((x) => x.enabled && !x.deployable);
}

// 指令页 Agent → 部署卡 key（切换指令后只刷新这一张卡）
const AGENT_CARD = {
  codex: 'codex', workbuddy: 'wb', dsh: 'dsh',
  claude: 'claude', hermes: 'hermes', zcode: 'zcode',
};

async function refreshAgentStatus(agent) {
  const key = AGENT_CARD[agent];
  if (!key) return;
  try { await refreshStatus(true, new Set([key]), true); } catch (_) { /* 忽略 */ }
}

// 破甲候选的开关：拨右 = 部署这一份（写 ~/.codex + 改 config 指针），
// 拨左 = 关闭注入。两种状态互斥，换一份时旧的那份自动落回停用。
async function applyInstr(name, it) {
  const cur = it || PM_DOCS.find((x) => x.name === name) || null;
  setBusy(true, '正在部署指令：' + name);
  let r = null;
  try { r = await api.instrToggle(PM_AGENT, name, true); }
  catch (e) { r = { ok: false, error: String(e.message || e) }; }
  setBusy(false);
  if (!r || r.ok === false) {
    toast('部署失败：' + ((r && r.error) || ''), 'err');
    logLine('err', 'prompt', '切换指令源失败：' + ((r && r.error) || ''));
  } else {
    logHead('▶ 切换指令源 → ' + name);
    logLine('ok', 'prompt', '指令源已切到 ' + name
      + (r.written && r.written.length ? '（写入 ' + r.written.length + ' 个落点）' : '')
      + (r.removed && r.removed.length ? '，撤下旧文件 ' + r.removed.length + ' 份' : ''));
    // 先把开关就地翻过去，列表不闪烁；落盘后以桥的返回为准
    PM_DOCS.filter((x) => x.deployable).forEach((x) => {
      x.enabled = x.name === name;
      x.active = x.enabled;
      x.labels = (x.labels || []).map((lb) => (lb.tag === '注入中' || lb.tag === '未部署')
        ? { tag: x.enabled ? '注入中' : '未部署', tone: x.enabled ? 'ok' : 'muted' } : lb);
      updateDocRow(x);
    });
    toast(`已切换 ${name} 并写入落点，不需要再点部署` + (r.removed && r.removed.length
      ? `（清理旧文件：${r.removed.join('、')}）` : '')
      + ' · 客户端下一轮/新会话读取', 'ok');
  }
  await refreshDocs();
  if (r && r.ok !== false) await refreshAgentStatus(PM_AGENT);
}

async function disableInstr(name) {
  setBusy(true, '正在关闭指令注入……');
  let r = null;
  try { r = await api.instrToggle(PM_AGENT, name, false); }
  catch (e) { r = { ok: false, error: String(e.message || e) }; }
  setBusy(false);
  if (!r || r.ok === false) {
    toast('关闭失败：' + ((r && r.error) || ''), 'err');
    logLine('err', 'prompt', '关闭指令注入失败：' + ((r && r.error) || ''));
  } else {
    logHead('▶ 关闭指令注入');
    logLine('ok', 'prompt', '已撤下注入：部署文件、AGENTS 标记块、config 指针一并清掉；'
      + 'AGENTS.md / SKILL.md 两路开关保持原样');
    PM_DOCS.filter((x) => x.deployable).forEach((x) => {
      x.enabled = false;
      x.active = false;
      x.labels = (x.labels || []).map((lb) => (lb.tag === '注入中' || lb.tag === '未部署')
        ? { tag: '未部署', tone: 'muted' } : lb);
      updateDocRow(x);
    });
    toast('已关闭指令注入并撤下落点，不需要再点还原 · 客户端下一轮/新会话读取', 'ok');
  }
  await refreshDocs();
  if (r && r.ok !== false) await refreshAgentStatus(PM_AGENT);
}

async function deleteDoc(it) {
  const okDel = await askConfirm({
    title: '删除自定义提示词',
    sub: it.id,
    text: '删除后无法从界面恢复（源件备份仍在 状态/editor-backup）。',
    ok: '删除', danger: true,
  });
  if (!okDel) return;
  let r = null;
  try { r = await api.docDelete(it.id); }
  catch (e) { r = { ok: false, error: String(e.message || e) }; }
  if (!r || r.ok === false) { toast('删除失败：' + ((r && r.error) || ''), 'err'); return; }
  toast('已删除 ' + it.name, 'ok');
  await refreshDocs();
}

function paintPmDirty() {
  const area = $('pmArea');
  const changed = area.value !== PM_ORIG;
  const saved = PM_SAVE_STATE === 'saved';
  area.classList.toggle('p-dirty', changed);
  $('btnPmUndo').disabled = !changed;
  $('pmHint').textContent = !PM_CURRENT ? '' : (changed
    ? (PM_SAVE_STATE === 'saving' ? '正在保存…' : '● 有改动 · 正在自动保存')
    : (saved ? '已保存' + (PM_SAVED_AT ? ' ' + PM_SAVED_AT : '')
             : '已是最新'));
}

function showPmList() {
  $('pmFormWrap').classList.add('off');
  $('pmListWrap').classList.remove('off');
  $('pmGrid').querySelectorAll('.pmrow.on').forEach((n) => n.classList.remove('on'));
  PM_CURRENT = '';
}

// 编辑中的内容按 id 记在内存里：切到别的文档再回来，草稿还在
const PM_DRAFTS = new Map();
let PM_SAVE_TIMER = 0;
let PM_SAVE_STATE = '';
let PM_SAVED_AT = '';

function schedulePmSave() {
  if (!PM_CURRENT) return;
  PM_DRAFTS.set(PM_CURRENT, $('pmArea').value);
  clearTimeout(PM_SAVE_TIMER);
  PM_SAVE_STATE = '';
  paintPmDirty();
  PM_SAVE_TIMER = setTimeout(() => { saveDoc(true); }, 900);
}

async function selectDoc(id) {
  const it = pmById(id);
  if (!it) return;
  // 切走前先把上一份的草稿留住（还没落盘的，立刻落盘）
  if (PM_CURRENT && PM_CURRENT !== id) {
    PM_DRAFTS.set(PM_CURRENT, $('pmArea').value);
    if (PM_ORIG !== $('pmArea').value) {
      clearTimeout(PM_SAVE_TIMER);
      await saveDoc(true);
    }
  }
  PM_CURRENT = id;
  PM_META = it;
  $('pmGrid').querySelectorAll('.pmrow').forEach((n) => {
    n.classList.toggle('on', n.dataset.id === id);
  });
  $('pmListWrap').classList.add('off');
  $('pmFormWrap').classList.remove('off');
  // 中文名和文件名相同时不要重复（config.toml · config.toml）
  const headLabel = it.title && it.title !== it.name
    ? it.title + '（' + it.name + '）' : it.name;
  $('pmFormTitle').textContent = '编辑 · ' + headLabel;
  $('pmFormTitle').title = it.path || '';
  $('pmPath').textContent = it.path || '';
  // 面板头：只在能补充信息时显示；与页头标题/文件名相同的就退成通用文案，
  // 否则同一屏会连着出现两个 config.toml。
  const catName = pmCatName(it.cat);
  let headText;
  if (it.title && it.title !== it.name) headText = it.title;
  else if (catName && catName !== it.name) headText = catName;
  else headText = '文件内容';
  $('pmFileHead').textContent = headText;
  // 只读字段：文件名跟随源件，分类跟随当前页
  $('pmFieldName').value = it.name || '';
  $('pmFieldCat').value = catName || '';
  $('pmGenHint').hidden = !it.generated;

  const area = $('pmArea');
  area.value = '';
  area.disabled = true;
  PM_ORIG = '';
  PM_SAVE_STATE = '';
  paintPmDirty();
  let d = null;
  try { d = await api.docRead(id, PM_AGENT); }
  catch (e) { d = { ok: false, error: String(e.message || e) }; }
  if (!d || d.ok === false) {
    $('pmHint').textContent = (d && d.error) || '读取失败';
    return;
  }
  PM_ORIG = d.content || '';
  if (d.hint) $('pmHint').textContent = d.hint;
  // 有未保存的草稿就接着草稿，否则用盘上的内容
  area.value = PM_DRAFTS.has(id) ? PM_DRAFTS.get(id) : PM_ORIG;
  area.disabled = !d.editable;
  paintPmDirty();
}

async function refreshDocs() {
  let d = null;
  try { d = await api.docs(PM_AGENT); }
  catch (e) { d = { ok: false, error: String(e.message || e) }; }
  if (!d || d.ok === false) {
    $('pmHint').textContent = (d && d.error) || '清单读取失败';
    toast('读取提示词库失败：' + ((d && d.error) || ''), 'err');
    return;
  }
  PM_DOCS = d.items || [];
  PM_CATS = d.categories || [];
  const on = PM_DOCS.filter((x) => x.enabled).length;
  const off = PM_DOCS.length - on;
  $('pmAgentName').textContent = d.label || PM_AGENT;
  $('pmAgentHint').textContent = '';
  $('pmCount').textContent = String(PM_DOCS.length);
  $('pmCustom').textContent = String(PM_DOCS.filter((x) => x.cat === 'custom').length);
  const sw = PM_DOCS.filter((x) => !x.deployable);
  const swOn = sw.filter((x) => x.enabled).length;
  $('pmDeployInput').checked = sw.length > 0 && swOn === sw.length;
  $('pmDeploySwitch').title = on === 0
    ? '全部文档已关闭：部署等于回官方原版（拨右全部启用）'
    : '总开关：拨左停用全部可停用的文档，拨右全部启用（' + on + ' 启用 / ' + off + ' 停用）';
  $('pmDot').classList.toggle('on', on > 0);
  if (!PM_CATS.some((c) => c.id === PM_CAT)) PM_CAT = 'all';
  paintPmCats();
  paintPmGrid();
}

async function saveDoc(auto) {
  if (!PM_CURRENT) return;
  const id = PM_CURRENT;
  const text = $('pmArea').value;
  PM_SAVE_STATE = 'saving';
  paintPmDirty();
  const b64 = btoa(unescape(encodeURIComponent(text)));
  let r = null;
  try { r = await api.docWrite(id, b64); }
  catch (e) { r = { ok: false, error: String(e.message || e) }; }
  if (PM_CURRENT !== id) return;       // 已经切走了，别把结果画到别的文档上
  if (!r || r.ok === false) {
    PM_SAVE_STATE = '';
    paintPmDirty();
    if (!auto) toast('写回失败：' + ((r && r.error) || ''), 'err');
    else logLine('warn', 'pm', '自动保存失败：' + ((r && r.error) || ''));
    return;
  }
  PM_ORIG = text;
  PM_DRAFTS.delete(id);
  PM_SAVE_STATE = 'saved';
  PM_SAVED_AT = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  paintPmDirty();
  if (!auto) toast('已写回源件 · 备份在 状态/editor-backup —— 改完要重新部署才生效', 'ok');
}

async function newDoc() {
  const cat = PM_CAT || 'custom';
  if (cat !== 'instruction' && cat !== 'custom') {
    toast('「' + pmCatName(cat) + '」是固定落点，不能新建；请切到「指令文件」', 'warn');
    return;
  }
  const name = await askText({
    title: '新建指令',
    sub: pmCatName(cat) + ' · ' + (PM_AGENT || 'codex'),
    hint: '名称只允许字母 / 数字 / _ / -；会创建在 targets/<当前 Agent>/instructions/ 下，只属于这个 Agent。',
    label: '名称',
    value: '',
    max: 40,
    placeholder: '例如：trial-v2',
  });
  if (name === null || !name.trim()) return;
  let r = null;
  try { r = await api.docNew(PM_AGENT, cat, name.trim()); }
  catch (e) { r = { ok: false, error: String(e.message || e) }; }
  if (!r || r.ok === false) { toast('新建失败：' + ((r && r.error) || ''), 'err'); return; }
  await refreshDocs();
  toast('已创建：' + (r.id || r.path), 'ok');
  await selectDoc(r.id);
}

async function importDoc() {
  if (!api.docImport) { toast('当前版本不支持导入', 'warn'); return; }
  setBusy(true, '正在导入指令……');
  let r = null;
  try { r = await api.docImport(PM_AGENT); }
  catch (e) { r = { ok: false, error: String(e.message || e) }; }
  setBusy(false);
  if (r && r.canceled) return;
  if (!r || r.ok === false) {
    toast('导入失败：' + ((r && r.error) || ''), 'err');
    return;
  }
  PM_CAT = 'instruction';
  await refreshDocs();
  toast('已导入 ' + (r.name || r.id) + ' 到当前 Agent', 'ok');
  if (r.id) await selectDoc(r.id);
}

async function initPrompts() {
  PROMPTS_INITED = true;
  paintPmAgents();
  $('pmArea').addEventListener('input', schedulePmSave);
  $('pmArea').addEventListener('blur', () => {
    if (PM_CURRENT && PM_ORIG !== $('pmArea').value) { clearTimeout(PM_SAVE_TIMER); saveDoc(true); }
  });
  $('btnPmUndo').addEventListener('click', () => {
    $('pmArea').value = PM_ORIG;
    if (PM_CURRENT) PM_DRAFTS.delete(PM_CURRENT);
    clearTimeout(PM_SAVE_TIMER);
    PM_SAVE_STATE = '';
    paintPmDirty();
  });
  $('btnPmSave').addEventListener('click', () => {
    clearTimeout(PM_SAVE_TIMER);
    saveDoc(false);
  });
  $('btnPmClose').addEventListener('click', () => {
    if (PM_CURRENT && PM_ORIG !== $('pmArea').value) {
      PM_DRAFTS.set(PM_CURRENT, $('pmArea').value);
      clearTimeout(PM_SAVE_TIMER);
      saveDoc(true);           // 返回时先把改动落盘，不再拦一道确认
    }
    showPmList();
  });
  $('btnPmRefresh').addEventListener('click', refreshDocs);
  $('btnPmNew').addEventListener('click', newDoc);
  if ($('btnPmImport')) $('btnPmImport').addEventListener('click', importDoc);
  // 页面内输入弹窗
  $('btnPmInputOk').addEventListener('click', () => closeAsk($('pmInputText').value));
  $('btnPmInputCancel').addEventListener('click', () => closeAsk(null));
  $('btnPmInputClose').addEventListener('click', () => closeAsk(null));
  $('pmInputModal').addEventListener('click', (e) => {
    if (e.target.id === 'pmInputModal') closeAsk(null);
  });
  $('pmInputText').addEventListener('input', () => {
    $('pmInputCount').textContent = $('pmInputText').value.length + ' / ' + $('pmInputText').maxLength;
  });
  $('pmInputText').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); closeAsk($('pmInputText').value); }
    if (e.key === 'Escape') { e.preventDefault(); closeAsk(null); }
  });
  // 页面内确认
  $('btnCfmOk').addEventListener('click', () => closeConfirm(true));
  $('btnCfmCancel').addEventListener('click', () => closeConfirm(false));
  $('btnCfmClose').addEventListener('click', () => closeConfirm(false));
  $('cfmSheet').addEventListener('click', (e) => {
    if (e.target.id === 'cfmSheet') closeConfirm(false);
  });
  $('pmDeployInput').addEventListener('change', () => {
    const on = PM_DOCS.filter((x) => x.enabled && !x.deployable);
    const off = PM_DOCS.filter((x) => !x.enabled && !x.deployable);
    if ($('pmDeployInput').checked) {
      if (!off.length) { $('pmDeployInput').checked = on.length > 0; toast('没有停用的文档可启用', 'warn'); return; }
      toggleMany(off, true);
    } else {
      if (!on.length) { $('pmDeployInput').checked = false; toast('没有启用的文档可停用', 'warn'); return; }
      toggleMany(on, false);
    }
  });
  $('btnPmCopy').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText($('pmArea').value);
      toast('已复制全文', 'ok');
    } catch (e) {
      toast('复制失败：' + (e.message || e), 'err');
    }
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !$('pmFormWrap').classList.contains('off')) $('btnPmClose').click();
  });
  await refreshDocs();
}

/* ── 补丁总览（指标卡 → 进度条 → 分组表 + 状态 pill）── */

const PILL = {
  current:  ['pill-cur', '已部署'],
  stale:    ['pill-miss', '未部署'],
  missing:  ['pill-miss', '缺失'],
  external: ['pill-ext', '外来'],
};

let PATCH_FLAT = [];
let PATCH_TARGET = '';
let PATCH_OPEN_IDX = -1;
const origRef = new Map();

async function openPatch(target) {
  PATCH_TARGET = target || (targets()[0] || '');
  if (!PATCH_TARGET) { toast('先在卡片上勾选目标', 'warn'); return; }
  $('patchModal').classList.add('on');
  await loadPatch();
}

function closePatch() { $('patchModal').classList.remove('on'); }

async function loadPatch() {
  const body = $('patchBody');
  body.innerHTML = '<div class="pempty">正在检测……</div>';
  let d = null;
  try { d = await api.patches(PATCH_TARGET); } catch (e) { d = { ok: false, error: String(e.message || e) }; }
  if (!d || d.ok === false) {
    body.innerHTML = '<div class="pempty err">读取失败：' + esc((d && d.error) || '未知错误') + '</div>';
    return;
  }
  PATCH_FLAT = [];
  const groups = d.groups || [];
  const allRows = groups.flatMap((g) => g.rows || []);
  allRows.forEach((r) => PATCH_FLAT.push(r));
  $('patchTarget').textContent = (d.label || PATCH_TARGET) + ' · ' + (d.state || '');

  body.innerHTML = '';
  if (!groups.length) {
    body.innerHTML = '<div class="pempty">这个席位没有登记任何部署文件（未安装或未部署）。</div>';
    return;
  }

  const cur = allRows.filter((r) => r.state === 'current').length;
  const stale = allRows.filter((r) => r.state === 'stale').length;
  const miss = allRows.filter((r) => r.state === 'missing').length;
  const ext = allRows.filter((r) => r.state === 'external').length;
  const tracked = cur + stale + miss;
  const pct = tracked ? Math.round(cur / tracked * 100) : 0;
  const stateTone = miss || stale ? 'p-warn' : 'p-ok';
  const stateTxt = miss ? (miss + ' 个文件缺失') : (stale ? (stale + ' 个文件未部署') : '全部已部署');
  const dep = d.deploy || {};
  const extNote = ext ? ' · 另有外来 ' + ext : '';
  const mets = document.createElement('div');
  mets.className = 'pmetrics';

  // codex：先把「部署了什么」写成四行人话（注入了哪份、改了哪两个、投放了多少技能）
  if (dep.instruction || dep.agents || dep.config || dep.skills) {
    const ins = dep.instruction || {};
    const ag = dep.agents || {};
    const cfg = dep.config || {};
    const sk = dep.skills || {};
    const short = (p) => (p || '').split(/[\\/]/).filter(Boolean).pop() || '—';
    const dirOf = (p) => (p || '').replace(/[\\/][^\\/]*$/, '');
    const onBadge = (on) => on
      ? '<span class="pill pill-cur">已写入</span>'
      : '<span class="pill pill-miss">未写入</span>';
    const skillNames = (sk.ids || []).slice(0, 10).join('、');
    const more = (sk.ids || []).length > 10 ? ' 等 ' + (sk.ids || []).length + ' 份' : '';
    mets.innerHTML =
      '<div class="pmetric p-ok"><b>' + esc(dep.pack || '—') + '</b>' +
        '<span>当前注入的指令源</span></div>' +
      '<div class="pmetric"><b style="font-size:13px">' + onBadge(ins.on) +
        ' <span class="mono">' + esc(short(ins.src)) + ' → ' + esc(ins.dst || '') + '</span></b>' +
        '<span>指令文件 · 复制进 prompts/（' + (ins.bytes || 0) + ' 字节）</span></div>' +
      '<div class="pmetric"><b style="font-size:13px">' + onBadge(ag.on) +
        ' <span class="mono">' + esc(ag.path || '') + '</span></b>' +
        '<span>AGENTS.md · 插入 POJIA-CODEX 标记块（' + (ag.bytes || 0) + ' 字节）</span></div>' +
      '<div class="pmetric"><b style="font-size:13px">' + onBadge(cfg.on) +
        ' <span class="mono">' + esc(short(cfg.path)) + '</span></b>' +
        '<span>config.toml · ' + esc(cfg.value || '') + '</span></div>' +
      '<div class="pmetric"><b style="font-size:13px">' + onBadge(sk.on) +
        ' <span class="mono">' + (sk.ok || 0) + ' / ' + (sk.total || 0) + '</span></b>' +
        '<span>SKILL.md · ' + esc(dirOf(sk.dir) + '/' + short(sk.dir)) + '</span></div>' +
      ((sk.ids || []).length
        ? '<div class="pmetric p-note"><b style="font-size:12px">' + esc(skillNames + more) + '</b>' +
          '<span>已投放的路由技能（父类目 + 叶子）</span></div>'
        : '');
  } else {
    mets.innerHTML =
      '<div class="pmetric ' + stateTone + '"><b>' + cur + ' / ' + tracked + '</b>' +
      '<span>部署文件 · ' + stateTxt + extNote + '</span></div>' +
      '<div class="pmetric"><b style="font-size:13px">' +
        '<span class="pill ' + (miss ? 'pill-miss' : 'pill-cur') + '">缺失 ' + miss + '</span> ' +
        '<span class="pill ' + (stale ? 'pill-miss' : 'pill-cur') + '">未部署 ' + stale + '</span> ' +
        '<span class="pill ' + (ext ? 'pill-ext' : 'pill-cur') + '">外来 ' + ext + '</span> ' +
        '<span class="pill pill-cur">已部署 ' + cur + '</span></b>' +
      '<span>' + esc(dep.text || '部署状态：' + (d.state || '—')) + '</span></div>' +
      '<div class="pmetric"><b>' + pct + '%</b><span>关键段命中率</span></div>';
  }
  body.appendChild(mets);
  const bar = document.createElement('div');
  bar.className = 'pbar';
  bar.innerHTML = '<i style="width:' + pct + '%"></i>';
  body.appendChild(bar);

  // DSH：分层 / 每轮重锁 / 源码补丁 三块明细
  if (dep.layers || dep.shield || dep.purge) {
    const short = (p) => (p || '').split(/[\\/]/).filter(Boolean).pop() || '—';
    const kb = (n) => (n ? (n / 1024).toFixed(n > 10240 ? 0 : 1) + ' KB' : '—');
    const line = (title, path, note) =>
      '<div class="pmetric"><b style="font-size:13px">' + esc(title) +
      ' <span class="mono">' + esc(short(path)) + '</span></b>' +
      '<span>' + esc(note) + '</span>' +
      '<span class="mono" style="font-size:11px;opacity:.6;word-break:break-all">' +
      esc(path) + '</span></div>';

    let html = '';
    (dep.layers || []).forEach((L) => {
      html += line('分层 · ' + L.name, L.path, kb(L.bytes) + ' · DSH 按宽→窄拼装');
    });
    if (dep.shield) {
      html += line('每轮重锁', dep.shield.dir,
        dep.shield.files.join('、') + ' · 已挂载 ' + dep.shield.profiles + ' 个 profile');
    }
    if (dep.purge && dep.purge.length) {
      const okN = dep.purge.filter((x) => x.applied).length;
      html += '<div class="pmetric p-ok"><b>' + okN + ' / ' + dep.purge.length +
        '</b><span>源码补丁 · 已清除</span></div>';
      dep.purge.forEach((x) => {
        html += '<div class="pmetric p-note"><b style="font-size:12px">#' + x.id + ' ' +
          esc(x.desc) + '</b><span>' + esc(x.layer) + ' · ' +
          (x.applied ? '已清除' : '待清除') + '</span>' +
          '<span class="mono" style="font-size:11px;opacity:.6;word-break:break-all">' +
          esc(x.path) + '</span></div>';
      });
    }
    if (html) mets.insertAdjacentHTML('beforeend', html);
  }

  const GROUP_NOTE = {
    inject: '注入到客户端读取的指令 / 角色文件',
    patch:  '官方安装包里被打过补丁的文件',
    output: '输出风格通道（output styles）',
    skill:  '投放进技能目录的路由技能',
    other:  '其它落盘文件',
    rows:   '体检与检测的逐项注记（含未知 / 异常标记）',
  };
  groups.forEach((g) => {
    const rows = g.rows || [];
    const grp = document.createElement('div');
    grp.className = 'pgroup';
    const gcur = rows.filter((r) => r.state === 'current').length;
    const gext = rows.filter((r) => r.state === 'external').length;
    const gbad = rows.some((r) => r.state === 'missing');
    const gwarn = rows.some((r) => r.state === 'stale');
    grp.classList.add(gbad ? 'g-bad' : (gwarn ? 'g-warn' : 'g-ok'));
    const head = document.createElement('button');
    head.type = 'button';
    head.className = 'ghead';
    head.innerHTML =
      '<span class="gcaret">&#x25B8;</span>' + esc(g.name) +
      '<span class="gnote">' + esc(GROUP_NOTE[g.id] || '') +
      ' · <b>' + gcur + '</b>/' + rows.length + (gext ? ' + 外来 ' + gext : '') + '</span>';
    const tbl = document.createElement('table');
    tbl.className = 'ptable';
    if (rows.length > 12) { tbl.style.display = 'none'; }
    tbl.innerHTML =
      '<thead><tr><th class="num">#</th><th>文件 / 落点</th><th>角色</th>' +
      '<th>大小</th><th>状态</th></tr></thead><tbody></tbody>';
    const tb = tbl.querySelector('tbody');
    rows.forEach((r, i) => {
      const idx = PATCH_FLAT.length - rows.length + i;
      const pill = PILL[r.state] || PILL.missing;
      const fn = (r.path || '').split(/[\\/]/).filter(Boolean).pop() || r.path;
      const dir = (r.path || '').slice(0, Math.max(0, (r.path || '').length - fn.length));
      const tr = document.createElement('tr');
      tr.className = 'prow';
      tr.dataset.idx = String(idx);
      tr.innerHTML =
        '<td class="num">' + (idx + 1) + '</td>' +
        '<td><span class="pfile" title="' + esc(r.path) + '">' +
          '<span class="fdir">' + esc(dir) + '</span>' +
          '<span class="fname">' + esc(fn) + '</span></span></td>' +
        '<td>' + esc(r.role) + '</td>' +
        '<td class="szcol">' + (r.isdir ? '目录' : (typeof r.size === 'number' ? fmtBytes(r.size) : '—')) + '</td>' +
        '<td><span class="pill ' + pill[0] + '">' + pill[1] + '</span></td>';
      tr.addEventListener('click', () => showCat(idx));
      tb.appendChild(tr);
    });
    head.addEventListener('click', () => {
      const hide = tbl.style.display === 'none';
      tbl.style.display = hide ? '' : 'none';
      grp.classList.toggle('g-open', hide);
    });
    grp.appendChild(head);
    grp.appendChild(tbl);
    body.appendChild(grp);
  });

  if (PATCH_OPEN_IDX >= 0 && PATCH_OPEN_IDX < PATCH_FLAT.length) showCat(PATCH_OPEN_IDX, true);
}

function fmtBytes(n) {
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  return (n / 1024 / 1024).toFixed(2) + ' MB';
}

async function showCat(idx, silent) {
  const row = PATCH_FLAT[idx];
  if (!row) return;
  const body = $('patchBody');
  const old = body.querySelector('.pcat');
  if (old) old.remove();
  if (!silent && PATCH_OPEN_IDX === idx) {
    PATCH_OPEN_IDX = -1;
    return;
  }
  PATCH_OPEN_IDX = idx;

  const box = document.createElement('div');
  box.className = 'pcat';
  box.innerHTML = '<div class="pops"><span>' + esc(row.path) + '</span>' +
    '<span class="pdirty">' + esc(row.role) + ' · 读取中……</span></div>';
  const tr = body.querySelector('.ptable tr[data-idx="' + idx + '"]');
  const grp = tr && tr.closest('.pgroup');
  if (grp) grp.after(box); else body.appendChild(box);
  if (!silent) box.scrollIntoView({ block: 'nearest' });

  let d = null;
  try { d = await api.read(['' + PATCH_TARGET, '' + idx]); }
  catch (e) { d = { ok: false, error: String(e.message || e) }; }
  const note = box.querySelector('.pdirty');
  if (!d || d.ok === false) {
    note.textContent = (d && d.error) || '读取失败';
    return;
  }
  if (d.isdir) { note.textContent = '目录 —— 点路径用资源管理器打开'; return; }

  if (!d.editable) {
    if (!d.content) { note.textContent = '空文件'; return; }
    note.textContent = d.role + ' · 只读';
    const pre = document.createElement('pre');
    pre.className = 'parea';
    pre.style.userSelect = 'text';
    pre.textContent = d.content.length > 2400 ? d.content.slice(0, 2400) + '\n\n…… 只读预览截断 ……' : d.content;
    box.appendChild(pre);
    return;
  }

  const orig = d.content;
  const shaTxt = (d.sha || '—').slice(0, 10);
  note.textContent = 'SHA ' + shaTxt + ' · ' + fmtBytes(d.size || orig.length);

  const area = document.createElement('textarea');
  area.className = 'parea';
  area.spellcheck = false;
  area.value = orig;
  box.appendChild(area);

  const ops = document.createElement('div');
  ops.className = 'pops';
  const dirty = document.createElement('span');
  dirty.className = 'pdirty';
  const saveBtn = document.createElement('button');
  saveBtn.className = 'minibtn';
  saveBtn.textContent = '保存写回';
  const undoBtn = document.createElement('button');
  undoBtn.className = 'minibtn';
  undoBtn.textContent = '还原';
  const openBtn = document.createElement('button');
  openBtn.className = 'minibtn';
  openBtn.textContent = '外部打开';
  ops.appendChild(saveBtn);
  ops.appendChild(undoBtn);
  ops.appendChild(openBtn);
  ops.appendChild(dirty);
  box.appendChild(ops);

  const paintDirty = () => {
    const changed = area.value !== origRef.get(idx);
    area.classList.toggle('p-dirty', changed);
    dirty.textContent = changed ? '● 有未保存的修改' : '';
    saveBtn.disabled = !changed;
    undoBtn.disabled = !changed;
  };
  origRef.set(idx, orig);
  area.addEventListener('input', paintDirty);
  paintDirty();

  saveBtn.addEventListener('click', async () => {
    saveBtn.disabled = true;
    const b64 = btoa(unescape(encodeURIComponent(area.value)));
    let r = null;
    try { r = await api.write(['' + PATCH_TARGET, '' + idx, b64]); }
    catch (e) { r = { ok: false, error: String(e.message || e) }; }
    saveBtn.disabled = false;
    if (!r || r.ok === false) {
      dirty.textContent = '✗ 写回失败：' + ((r && r.error) || '');
      return;
    }
    if (!r.written) { dirty.textContent = '内容没变，没写盘'; return; }
    dirty.textContent = '✓ 已写回 · 备份在 状态/editor-backup';
    origRef.set(idx, area.value);
    paintDirty();
  });
  undoBtn.addEventListener('click', () => {
    area.value = origRef.get(idx) || orig;
    paintDirty();
  });
  openBtn.addEventListener('click', () => { if (row.path) api.openPath(row.path); });
}

/* ── 写入预览 ── */
const DIFF_KINDS = new Set(['codex', 'zcode', 'claude', 'hermes']);

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
  $('diffTarget').textContent = tg.join(' / ');
  body.innerHTML = '<div class="pempty">正在对比……</div>';
  body.innerHTML = '';
  for (const k of tg) {
    let d = null;
    try { d = await api.diff([k]); } catch (e) { d = { error: String(e.message || e) }; }
    const item = document.createElement('div');
    item.className = 'skcard';
    if (!d || d.error) {
      item.innerHTML = '<div class="skname">' + esc(k) + '</div><div class="skdesc">读取失败：' +
        esc((d && d.error) || '未知错误') + '</div>';
      body.appendChild(item);
      continue;
    }
    if (d.support !== 'full') {
      item.innerHTML = '<div class="skname">' + esc(d.label || k) + '</div><div class="skdesc">' +
        esc(d.note || '该目标不支持全文预览') + '</div>';
      body.appendChild(item);
      continue;
    }
    item.innerHTML = '<div class="skname">' + esc(d.label) + '</div>';
    body.appendChild(item);
    (d.items || []).forEach((it) => {
      const box = document.createElement('div');
      let html = '<div class="skname mono" style="font-style:normal">' + esc(it.path) + '</div>' +
        '<div class="skdesc">' + esc(it.note || '') + '</div>';
      if (it.unchanged) {
        html += '<div class="pempty">内容一模一样，一个字都不用动</div>';
      } else {
        html += '<div class="diffwrap"><div class="diffcol"><div class="diffhead">落盘现状</div>' +
          '<div class="diffbody">' + esc((it.rows || []).map((r) => r.l).join('\n')) + '</div></div>' +
          '<div class="diffcol"><div class="diffhead">部署写入</div>' +
          '<div class="diffbody">' + esc((it.rows || []).map((r) => r.r).join('\n')) + '</div></div></div>';
      }
      box.innerHTML = html;
      item.appendChild(box);
    });
  }
}
function closeDiff() { $('diffModal').classList.remove('on'); }

/* ── 部署历史（事务）── */
const TX_STATE = {
  applied: ['applied', '已应用'], restored: ['reverted', '已回退'],
  partial: ['partial', '部分完成'], 'rolled-back': ['partial', '已回滚'],
  preparing: ['reverted', '进行中'],
};

async function openTx() {
  $('txModal').classList.add('on');
  await loadTx();
}
function closeTx() { $('txModal').classList.remove('on'); }

async function loadTx() {
  const body = $('txBody');
  body.innerHTML = '<div class="pempty">正在读取……</div>';
  let d = null;
  try { d = await api.tx(['list']); } catch (e) { d = { ok: false, error: String(e.message || e) }; }
  if (!d || d.ok === false) {
    body.innerHTML = '<div class="pempty err">读取失败：' + esc((d && d.error) || '未知错误') + '</div>';
    return;
  }
  $('txRoot').textContent = d.root || '';
  const items = d.items || [];
  if (!items.length) {
    body.innerHTML = '<div class="pempty">还没有部署记录 —— 跑一次「部署」就会出现。</div>';
    return;
  }
  body.innerHTML = '';
  items.forEach((r) => {
    const pair = TX_STATE[r.status] || ['', r.status];
    const row = document.createElement('div');
    row.className = 'txrow';
    row.dataset.id = r.short;
    row.innerHTML =
      '<span class="txid">' + esc(r.short) + '</span>' +
      '<span class="txseat">' + esc(r.seat) + '</span>' +
      '<span class="txat">' + esc(String(r.at || '').slice(5, 16)) + '</span>' +
      '<span class="txfn">' + r.changed + ' / ' + r.files + '</span>' +
      '<span class="txstat ' + pair[0] + '">' + esc(pair[1]) + '</span>' +
      '<div class="txops">' +
      '<button class="minibtn" data-txact="verify">校验</button>' +
      '<button class="minibtn danger" data-txact="restore">回退</button></div>';
    body.appendChild(row);
  });
}

async function onTxRowClick(e) {
  const btn = e.target.closest('button[data-txact]');
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
    const okGo = await askConfirm({
      title: '回退快照',
      sub: id,
      text: '回到这次部署之前的状态。',
      items: ['逐文件回拷快照备份，不是二进制基线',
              '文件被别的操作改过时会中止，不硬覆盖',
              '目标程序需要重启才会重新读取文件'],
      ok: '回退', danger: true,
    });
    if (!okGo) return;
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
    refreshStatus(true, null, true);
  }
}

async function doTxPurge() {
  const okPurge = await askConfirm({
    title: '清理旧快照',
    text: '只保留最近 12 次部署快照，更早的会被删除。',
    ok: '清理', danger: true,
  });
  if (!okPurge) return;
  const d = await api.tx(['purge']);
  if (d && d.ok) { toast('已清理 ' + d.dropped + ' 个旧快照', ''); await loadTx(); }
  else toast('清理失败：' + ((d && d.error) || ''), 'err');
}

// ────────────────────────── 更新检查 ──────────────────────────

let UP_CACHE = null;

function mdToHtml(md) {
  const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const out = [];
  let inCode = false, codeBuf = [];
  for (const raw of String(md || '').split(/\r?\n/)) {
    if (raw.trim().startsWith('```')) {
      if (inCode) { out.push('<pre><code>' + esc(codeBuf.join('\n')) + '</code></pre>'); codeBuf = []; }
      inCode = !inCode; continue;
    }
    if (inCode) { codeBuf.push(raw); continue; }
    const line = esc(raw);
    if (/^#{1,3}\s/.test(line)) { out.push('<h3>' + line.replace(/^#{1,3}\s*/, '') + '</h3>'); continue; }
    if (/^[-*]\s/.test(line)) {
      if (!out.length || !out[out.length - 1].endsWith('</ul>')) out.push('<ul>');
      out.push('<li>' + line.replace(/^[-*]\s*/, '') + '</li>');
      continue;
    }
    if (out.length && out[out.length - 1].endsWith('</li>') && !out[out.length - 1].endsWith('</ul>')) out.push('</ul>');
    if (line.trim()) out.push('<p>' + line + '</p>');
  }
  if (codeBuf.length) out.push('<pre><code>' + esc(codeBuf.join('\n')) + '</code></pre>');
  return out.join('\n')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, '<a href="#" class="uplink" data-url="$2">$1</a>');
}

function paintUpdate(d) {
  const meta = $('upMeta'), hint = $('upHint'), body = $('upBody'), foot = $('upFoot');
  if (!d || !d.ok) {
    const detail = (d && d.error) ? String(d.error) : '';
    const limited = /限流|403|429|rate limit/i.test(detail);
    meta.textContent = '—';
    hint.textContent = limited
      ? 'GitHub 匿名接口限流了（每小时 60 次），过一会儿再试。'
      : '检查失败：' + (detail || '网络不通');
    body.innerHTML = '<div class="pempty">' + (limited
      ? 'GitHub 对未登录请求有频率限制，等一小时或稍后重试。<br>'
      : '拿不到版本信息。<br>') +
      '<a href="#" class="uplink" data-url="' + ((d && (d.releasesUrl || d.url)) || 'https://github.com/Wei894348/pojia_desk/releases') + '">直接在浏览器打开 GitHub 历史版本 →</a></div>';
    if (detail) body.innerHTML += '<div class="pempty" style="font-size:11px;opacity:.65;word-break:break-all">' + esc(detail) + '</div>';
    foot.innerHTML = '<button class="btn ghost" id="btnUpReleases">历史版本</button>';
    bindUpFoot(d);
    return;
  }
  const when = d.published ? d.published.slice(0, 10) : '';
  meta.textContent = '最新 v' + d.latest + (when ? ' · ' + when : '') + ' · 本地 v' + d.local;
  hint.textContent = d.hasUpdate
    ? '有新版本 v' + d.latest + '（本地 v' + d.local + '）。'
    : '已是最新版本 v' + d.local + '。';

  // 版本列表：当前版本高亮，点一行展开该版本的更新说明
  const list = (d.releases || []).map((r) => {
    const cur = r.tag === d.local;
    const dt = r.published ? r.published.slice(0, 10) : '';
    return '<div class="uprow' + (cur ? ' cur' : '') + '" data-tag="' + esc(r.tag) + '">' +
      '<span class="tag">v' + esc(r.tag) + '</span>' +
      (cur ? '<span class="pill">当前</span>' : '') +
      (r.prerelease ? '<span class="pill pre">预览</span>' : '') +
      '<span class="when">' + esc(dt) + '</span>' +
      (r.tag === d.latest && !cur ? '<span class="pill new">最新</span>' : '') +
      '</div>';
  }).join('');
  const rail = list
    ? '<div class="uprail"><div class="uprail-h">历史版本</div>' + list + '</div>'
    : '';
  const notes = '<div class="upnotes">' +
    '<div class="upnotes-h">v' + esc(d.latest) + ' 更新说明</div>' +
    (mdToHtml(d.body) || '<p>（无描述）</p>') + '</div>';
  body.innerHTML = '<div class="upgrid">' + rail + notes + '</div>';

  foot.innerHTML = (d.hasUpdate ? '<button class="btn primary" id="btnUpGo">去更新</button>' : '') +
    '<button class="btn ghost" id="btnUpReleases">历史版本</button>';
  bindUpFoot(d);

  body.querySelectorAll('.uprow').forEach((row) => row.addEventListener('click', () => {
    const r = (d.releases || []).find((x) => x.tag === row.dataset.tag);
    if (!r) return;
    body.querySelectorAll('.uprow').forEach((x) => x.classList.toggle('on', x === row));
    const nh = body.querySelector('.upnotes-h'), nb = body.querySelector('.upnotes');
    if (nh) nh.textContent = 'v' + r.tag + (r.published ? ' · ' + r.published.slice(0, 10) : '') + ' 更新说明';
    if (nb) nb.innerHTML = '<div class="upnotes-h">v' + esc(r.tag) +
      (r.published ? ' · ' + esc(r.published.slice(0, 10)) : '') + ' 更新说明</div>' +
      (mdToHtml(r.body) || '<p>（无描述）</p>');
    bindUpLinks();
  }));
  bindUpLinks();
}

function bindUpLinks() {
  document.querySelectorAll('#upBody .uplink').forEach((a) => {
    if (a.dataset.bound) return;
    a.dataset.bound = '1';
    a.addEventListener('click', (e) => {
      e.preventDefault();
      if (a.dataset.url) window.open(a.dataset.url, '_blank');
    });
  });
}

function bindUpFoot(d) {
  const go = $('btnUpGo');
  if (go) go.addEventListener('click', () => { if (d && d.url) window.open(d.url, '_blank'); });
  const rel = $('btnUpReleases');
  if (rel) rel.addEventListener('click', () => {
    window.open((d && d.releasesUrl) || 'https://github.com/Wei894348/pojia_desk/releases', '_blank');
  });
}

async function checkUpdate(manual) {
  if (manual) {
    $('upMeta').textContent = '—';
    $('upHint').textContent = '正在检查最新版本……';
    $('upBody').innerHTML = '<div class="pempty">请求 GitHub 中……</div>';
    $('upFoot').innerHTML = '';
  }
  try { UP_CACHE = await api.checkUpdate(); }
  catch (e) { UP_CACHE = { ok: false, error: String(e.message || e) }; }
  paintUpdate(UP_CACHE);
  // 版本号上的小红点：有新版本时提示，否则不打扰
  const v = $('aboutVer');
  if (v) {
    v.classList.toggle('has-new', !!(UP_CACHE && UP_CACHE.ok && UP_CACHE.hasUpdate));
    v.title = (UP_CACHE && UP_CACHE.ok)
      ? ('最新 v' + UP_CACHE.latest + ' · 本地 v' + UP_CACHE.local + '（点击查看更新日志）')
      : '点击查看更新日志 / 检查新版本（暂时连不上 GitHub）';
  }
  return UP_CACHE;
}

async function openUpdate() {
  $('upModal').classList.add('on');
  // 每次打开都重新拉一次：GitHub 上刚发的版本要能立刻看到，
  // 不能因为启动时查过一次就永远显示旧结果。
  await checkUpdate(true);
}
function closeUpdate() { $('upModal').classList.remove('on'); }

// ────────────────────────── 事件绑定 ──────────────────────────

function bind() {
  // 左侧菜单
  document.querySelectorAll('.snav').forEach((b) => {
    b.addEventListener('click', () => switchView(b.dataset.view));
  });

  // 工具栏
  $('btnDeploy').addEventListener('click', () => doRun('apply'));
  $('btnDry').addEventListener('click', () => doRun('dry'));
  $('btnDiff').addEventListener('click', doDiff);
  $('btnTest').addEventListener('click', doTest);
  $('btnRevert').addEventListener('click', () => doRun('revert'));
  $('btnRefresh').addEventListener('click', () => refreshStatus(false, null, true));
  $('btnTx').addEventListener('click', openTx);
  $('btnPatchAll').addEventListener('click', () => openPatch());
  $('btnConsole').addEventListener('click', async () => {
    const r = await api.openConsole();
    if (!r.ok) toast('打不开控制台：' + (r.error || ''), 'err');
  });

  document.querySelectorAll('.chipbtn[data-sel]').forEach((b) => {
    b.addEventListener('click', () => {
      const m = b.dataset.sel;
      const all = state.cards.map((c) => c.key).filter((k) => ORDER.includes(k));
      if (m === 'all') state.selected = new Set(all);
      else if (m === 'none') state.selected = new Set();
      else state.selected = new Set(all.filter((k) => !state.selected.has(k)));
      renderCards(state.cards);
    });
  });

  document.querySelectorAll('.wbtn[data-win]').forEach((b) => {
    b.addEventListener('click', () => api.win(b.dataset.win));
  });

  // 日志：隐藏 / 恢复 + 拖拽调高
  const logPanel = $('logPanel');
  const toggleLog = () => {
    logPanel.classList.toggle('hidden');
  };
  $('btnCollapse').addEventListener('click', toggleLog);

  // 手柄拖拽：上拖加高、下拖减高（min 60 / max 视口-卡片区最低 160）
  const grip = $('logGrip');
  grip.addEventListener('mousedown', (e) => {
    e.preventDefault();
    logPanel.classList.add('dragging');
    const startY = e.clientY;
    const startH = logPanel.getBoundingClientRect().height;
    const move = (ev) => {
      const h = Math.min(Math.max(startH + (startY - ev.clientY), 60), window.innerHeight - 220);
      logPanel.style.flex = '0 0 ' + h + 'px';
    };
    const up = () => {
      logPanel.classList.remove('dragging');
      document.removeEventListener('mousemove', move);
      document.removeEventListener('mouseup', up);
    };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  });
  $('btnLogClear').addEventListener('click', () => {
    $('logBody').innerHTML = '';
    state.logCount = 0;
    $('logCount').textContent = '0';
  });
  $('btnLogFile').addEventListener('click', async () => {
    const r = await api.readLog();
    if (!r.ok) { toast('读不到日志：' + (r.error || ''), 'err'); return; }
    logHead('状态/破甲日志.txt（末尾 ' + r.lines.length + ' 行）');
    r.lines.forEach((ln) => { if (ln.trim()) logLine('dim', 'file', ln); });
  });

  // 归一
  const aboutVer = $('aboutVer');
  if (aboutVer) aboutVer.addEventListener('click', openUpdate);

  // 技能管理视图
  ORDER.forEach((k) => {
    if (k === 'dsh-desktop') return;   // 与 dsh 同一份目录，不重复列
    const o = document.createElement('option');
    o.value = k;
    o.textContent = LABELS[k];
    $('skSeat').appendChild(o);
  });
  $('btnSkLoad').addEventListener('click', () => { $('skBody').dataset.loaded = ''; loadSkills(); });

  // 设置视图
  const changePy = async () => {
    const r = await api.pickPython();
    if (r.canceled) return;
    if (!r.ok) { toast(r.error || '设置失败', 'err'); return; }
    toast('已切换解释器：' + r.python, 'ok');
    $('setPy').textContent = r.python.split('\\').pop() + ' · ' + r.version;
    await refreshStatus(true, null, true);
  };
  $('btnSetPy').addEventListener('click', changePy);
  $('btnSetRoot').addEventListener('click', () => {
    if (state.meta && state.meta.root) api.openPath(state.meta.root);
  });
  $('btnSetConsole').addEventListener('click', async () => {
    const r = await api.openConsole();
    if (!r.ok) toast('打不开控制台：' + (r.error || ''), 'err');
  });
  $('btnSetTx').addEventListener('click', openTx);

  // 关于视图
  $('btnAboutUp').addEventListener('click', openUpdate);
  $('btnAboutRepo').addEventListener('click', () => {
    window.open('https://github.com/Wei894348/pojia_desk', '_blank');
  });

  // 模态
  $('btnTxClose').addEventListener('click', closeTx);
  $('btnTxReload').addEventListener('click', loadTx);
  $('btnTxPurge').addEventListener('click', doTxPurge);
  $('txModal').addEventListener('click', (e) => { if (e.target.id === 'txModal') closeTx(); });
  $('txBody').addEventListener('click', onTxRowClick);
  $('btnDiffClose').addEventListener('click', closeDiff);
  $('diffModal').addEventListener('click', (e) => { if (e.target.id === 'diffModal') closeDiff(); });
  $('btnPatchClose').addEventListener('click', closePatch);
  $('btnPatchReload').addEventListener('click', loadPatch);
  $('patchModal').addEventListener('click', (e) => { if (e.target.id === 'patchModal') closePatch(); });

  // 版本 / 更新
  const aboutVerBtn = $('aboutVer');
  if (aboutVerBtn) aboutVerBtn.addEventListener('click', openUpdate);
  $('btnUpClose').addEventListener('click', closeUpdate);
  $('btnUpReload').addEventListener('click', () => checkUpdate(true));
  $('upModal').addEventListener('click', (e) => { if (e.target.id === 'upModal') closeUpdate(); });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'F5') { e.preventDefault(); refreshStatus(false, null, true); }
    if (e.key === 'F9') { e.preventDefault(); doTest(); }
  });

  api.onLog((evt) => {
    if (!evt) return;
    if (evt.type === 'line') logLine(evt.level, evt.target, evt.text);
    else if (evt.type === 'step') logLine('step', evt.target, '── ' + evt.action + ' ──');
    else if (evt.type === 'done') logLine(evt.code ? 'err' : 'ok', evt.target, evt.code ? ('翻车了 · 退出码 ' + evt.code) : '收工，漂亮');
    else if (evt.type === 'start') logHead('开工：' + evt.action + ' → ' + (evt.targets || []).join(' / '));
    else if (evt.type === 'exit' && evt.code) logLine('err', 'sys', '整体退出码 ' + evt.code + '（有目标没干利索，往上翻翻）');
  });
}

loadOpts();
bind();
boot();