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
const LABELS = {
  codex: 'Codex CLI', dsh: 'DSH (DeepSeek Harness)', wb: 'WorkBuddy', zcode: 'ZCode (Z.ai)',
  claude: 'Claude Code', grok: 'Grok CLI', gemini: 'Gemini CLI', hermes: 'Hermes Agent',
};

const state = {
  meta: null,
  cards: [],
  selected: new Set(['codex', 'dsh', 'wb', 'zcode', 'claude', 'grok', 'gemini', 'hermes']),
  expanded: new Set(),
  busy: false,
  logCount: 0,
};

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
  state.cards = cards.slice();
  const wrap = $('cards');
  wrap.innerHTML = '';
  cards.forEach((c) => wrap.appendChild(buildCard(c)));
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

// 单个目标的结果回来就替换它自己那张卡 —— 四个目标并行探测，谁快谁先显示
function upsertCard(c) {
  const wrap = $('cards');
  const old = wrap.querySelector('.card[data-key="' + c.key + '"]');
  const el = buildCard(c);
  if (old) wrap.replaceChild(el, old); else wrap.appendChild(el);
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

  head.appendChild(chkLabel);
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
  if (!quiet) overlay(true, '正在检测 ' + ORDER.length + ' 个目标……');
  try {
    renderSkeleton(ORDER);
    let done = 0;
    let bad = 0;
    await Promise.all(ORDER.map(async (k) => {
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
      setStatus('已检测 ' + done + ' / ' + ORDER.length + ' 个目标');
    }));
    setStatus(bad ? (bad + ' / ' + ORDER.length + ' 个目标需要处理')
                  : (ORDER.length + ' 个目标均为本版状态'), bad ? 'warn' : 'ok');
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

function targets() {
  const t = [...state.selected];
  return t.length ? t : [];
}

async function doRun(action) {
  if (state.busy) return;
  const tg = targets();
  if (!tg.length) { toast('先在卡片上勾选目标', 'warn'); return; }

  if (action === 'revert') {
    const ok = window.confirm('确定把 [' + tg.join(' / ') + '] 还原成官方原版？\n\n每个目标会从各自的备份还原，部署产物会被移除。');
    if (!ok) return;
  }

  const names = { apply: '部署', revert: '恢复', dry: '预演' };
  setBusy(true, names[action] + '中……');
  logHead('▶ ' + names[action] + ' → ' + tg.join(' / '));

  try {
    const r = await api.run(action, tg);
    if (r && r.ok) toast(names[action] + '搞定，漂亮 ✔', 'ok');
    else toast(names[action] + '跑完了，但甩了个非零退出码 ' + ((r && r.code) != null ? r.code : '?') + '，日志里瞅瞅', 'warn');
  } catch (e) {
    toast(names[action] + '翻车了：' + (e.message || e), 'err');
  } finally {
    setBusy(false);
    await refreshStatus(true);
  }
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
      const all = state.cards.map((c) => c.key);
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
}

bind();
boot();
