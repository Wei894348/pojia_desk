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
const ORDER = ['codex', 'dsh', 'wb', 'zcode', 'claude', 'grok', 'gemini', 'hermes'];
// 目标就这八个。DSH 的「破甲模式」不是第九个目标 —— 它是 DSH 卡片里的一个选项
// （卡内下拉的两个勾选框），状态并进 DSH 卡片自己的体检项与明细。
const LABELS = {
  codex: 'Codex CLI', dsh: 'DSH (DeepSeek Harness)', wb: 'WorkBuddy', zcode: 'ZCode (Z.ai)',
  claude: 'Claude Code', grok: 'Grok CLI', gemini: 'Gemini CLI', hermes: 'Hermes Agent',
};

const OPT_KEY = 'pojia.opts';

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
};

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
  const a = scope.querySelector('#optPrompt') || $('optPrompt');
  const b = scope.querySelector('#optPreset') || $('optPreset');
  if (a) a.checked = !!state.opts.prompt;
  if (b) b.checked = !!state.opts.preset;
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

function paintDshOpts() {
  return paintDshNodes($('ddDshBtn'), $('ddDshText'), $('ddDshMenu'));
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
function buildPresetOpt(dep) {
  const bar = document.createElement('div');
  bar.className = 'card-opt';
  const lbl = document.createElement('span');
  lbl.className = 'optlbl';
  lbl.textContent = '破甲模式';

  const btn = document.createElement('button');
  btn.className = 'optbtn';
  btn.id = 'ddDshBtn';
  btn.title = '这个 DSH 目标要干什么：注入提示词（给它打补丁）/ 增加破甲模式（写入用户级 preset）—— 两项可各选，也可以都要';
  const led = document.createElement('span');
  led.className = 'led';
  const txt = document.createElement('span');
  txt.id = 'ddDshText';
  const caret = document.createElement('span');
  caret.className = 'optcaret';
  caret.textContent = '\u25BE';
  btn.appendChild(led);
  btn.appendChild(txt);
  btn.appendChild(caret);

  const menu = document.createElement('div');
  menu.className = 'optmenu';
  menu.id = 'ddDshMenu';

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
  menu.appendChild(mk('optPrompt', '注入提示词',
    '部署时给 DSH 打提示词补丁，恢复时还原成官方原版', 'prompt'));
  menu.appendChild(mk('optPreset', '增加破甲模式',
    '写入 / 移除用户级 agent preset —— 模式下拉里多一个「破甲模式」', 'preset'));

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    state.ddOpen = !state.ddOpen;
    paintDshNodes(btn, txt, menu);
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

function renderCards(cards) {
  // 按 ORDER 重排 —— state.cards 的入队顺序是"探测谁先回来"，
  // 不排的话勾选一次全选卡片就会跳位
  state.cards = cards.slice().sort((a, b) =>
    ORDER.indexOf(a.key) - ORDER.indexOf(b.key));
  const wrap = $('cards');
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
  const i = state.cards.findIndex((x) => x.key === c.key);
  if (i >= 0) state.cards[i] = c; else state.cards.push(c);
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
  const metrics = document.createElement('div');
  metrics.className = 'metrics';
  (c.metrics || []).forEach((m) => {
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

  // 明细
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

  chev.addEventListener('click', () => {
    const hide = detail.classList.toggle('hide');
    if (hide) state.expanded.delete(c.key); else state.expanded.add(c.key);
    chev.textContent = hide ? '\u25BE' : '\u25B4';
  });

  el.appendChild(head);
  el.appendChild(home);

  // DSH 卡片专属：破甲模式下拉（注入提示词 / 增加破甲模式）+ 当前部署状态行。
  // 放在 DSH 卡里而不是工具条 —— 它是 DSH 自己的选择，跟别的目标没关系。
  if (c.key === 'dsh') {
    const opt = buildPresetOpt(c.deploy);
    el.appendChild(opt.bar);
    el.appendChild(opt.state);
    el.appendChild(opt.menu);
    paintDshNodes(opt.btn, opt.txt, opt.menu);   // 节点这还没进 DOM，只能按引用画
  }

  if (metrics.children.length) el.appendChild(metrics);
  el.appendChild(detail);
  return el;
}

function updateToolbarHint() {
  const n = state.selected.size;
  $('btnDeploy').title = n ? ('对选中的 ' + n + ' 个目标部署') : '先在卡片上勾选目标';
}

// ────────────────────────── 数据加载 ──────────────────────────

async function refreshStatus(quiet) {
  if (state.busy) return;
  setBusy(true, '正在检测状态……');
  const want = ORDER.slice();
  if (!quiet) overlay(true, '正在检测 ' + want.length + ' 个目标……');
  try {
    renderSkeleton(want);
    let done = 0;
    let bad = 0;
    await Promise.all(want.map(async (k) => {
      let d = null;
      try { d = await api.status([k]); }
      catch (e) { d = { error: String(e.message || e) }; }
      done += 1;
      if (d && d.cards && d.cards[0]) {
        upsertCard(d.cards[0]);
        if (d.cards[0].state !== 'current') bad += 1;
        if (d.when) $('sbWhen').textContent = d.when;
      } else {
        bad += 1;
        logLine('err', k, '检测失败：' + ((d && d.error) || '无返回'));
      }
      setStatus('已检测 ' + done + ' / ' + want.length + ' 个目标');
    }));
    setStatus(bad ? (bad + ' / ' + want.length + ' 个目标需要处理')
                  : (want.length + ' 个目标均为本版状态'), bad ? 'warn' : 'ok');
  } catch (e) {
    toast('检测出错：' + (e.message || e), 'err');
  } finally {
    setBusy(false);
    overlay(false);
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
}

// ────────────────────────── 执行动作 ──────────────────────────

async function doRun(action) {
  if (state.busy) return;
  let tg = targets();
  if (!tg.length) { toast('先在卡片上勾选目标', 'warn'); return; }

  // DSH 下拉里两项都没勾 = 这个目标这次不参与（下拉写的是"都不做"，不是"跳过按钮"）
  const dshOff = !state.opts.prompt && !state.opts.preset;
  if (dshOff && tg.includes('dsh')) {
    tg = tg.filter((k) => k !== 'dsh');
    logLine('dim', 'sys', 'DSH 下拉是「都不做」—— 本次跳过 DSH');
  }
  if (!tg.length) {
    toast(dshOff ? 'DSH 下拉是「都不做」，也没有别的目标可跑' : '先在卡片上勾选目标', 'warn');
    return;
  }

  if (action === 'revert') {
    if (!window.confirm(revertAsk(tg))) return;
  }

  const names = { apply: '部署', revert: '恢复', dry: '预演' };
  const flags = flagsForOpts();
  const hint = (tg.includes('dsh') && flags.length)
    ? (flags[0] === 'no-preset' ? '（DSH 只还原本体，破甲模式留着）'
                                : '（DSH 只动破甲模式，本体不碰）')
    : '';
  setBusy(true, names[action] + '中……');
  logHead('▶ ' + names[action] + ' → ' + tg.join(' / ') + hint);

  try {
    const r = await api.run(action, tg, flags);
    if (r && r.ok) toast(names[action] + '搞定，漂亮 ✔', 'ok');
    else toast(names[action] + '跑完了，但甩了个非零退出码 ' + ((r && r.code) != null ? r.code : '?') + '，日志里瞅瞅', 'warn');
  } catch (e) {
    toast(names[action] + '翻车了：' + (e.message || e), 'err');
  } finally {
    setBusy(false);
    await refreshStatus(true);
  }
}

// 恢复前的二次确认 —— 按 DSH 下拉的档位说清会动到哪部分
function revertAsk(tg) {
  let s = '确定把 [' + tg.join(' / ') + '] 还原成官方原版？\n\n每个目标会从各自的备份还原，部署产物会被移除。';
  if (!tg.includes('dsh')) return s;
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
    toast(d.ok ? '体检全过 ✔' : '体检查出问题（FAIL/WARN），日志里瞅瞅', d.ok ? 'ok' : 'warn');
    setStatus(d.ok ? '体检通过' : '体检没过', d.ok ? 'ok' : 'warn');
  } catch (e) {
    toast('体检出错：' + (e.message || e), 'err');
  } finally {
    setBusy(false);
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
// 对应冷咖啡 workbench 的「版本对比」页：看得到改动、查得到完整性、回得去。
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
        html += '<div class="diffunchanged">✔ 内容一模一样，一个字都不用动</div>';
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
    refreshStatus(true);
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
  $('btnRefresh').addEventListener('click', () => refreshStatus(false));

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
    logHead('破甲日志.txt（末尾 ' + r.lines.length + ' 行）');
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
    await refreshStatus(true);
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
    if (e.key === 'F5') { e.preventDefault(); refreshStatus(false); }
    if (e.key === 'F9') { e.preventDefault(); doTest(); }
  });

  const GO_WORDS = ['撸起袖子开工 → ', '学姐上手了 → ', '轮到它了 → ', '这就动手 → ', '安排上 → '];
  const goWord = () => GO_WORDS[Math.floor(Math.random() * GO_WORDS.length)];
  const DONE_OK = ['收工，漂亮 ✔', '干完了，干净利落 ✔', '搞定，没留尾巴 ✔', '活儿齐了 ✔'];
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
