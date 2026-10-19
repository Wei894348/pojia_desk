// Muse 工作台 · 主进程
// 与渲染层之间只走 IPC；所有真实操作都转发给 gui/bridge.py（它再驱动 console.py）。
const { app, BrowserWindow, ipcMain, shell, dialog, screen } = require('electron');

app.setName('Muse');
const path = require('path');
const fs = require('fs');
const { spawn, spawnSync } = require('child_process');

const GUI_DIR = __dirname;
const ROOT = path.dirname(GUI_DIR);
const BRIDGE = path.join(GUI_DIR, 'bridge.py');
const CONSOLE_PY = path.join(ROOT, 'engine', 'console.py');
const PY_CACHE = path.join(GUI_DIR, '.python-path');

let mainWin = null;
let py = null;            // { exe, prefix, version }
let running = null;       // 当前执行中的子进程

// 开发/自检期禁用磁盘缓存，否则改了 renderer 里的 css/js 重启后仍读旧内容
if (!app.isPackaged) app.commandLine.appendSwitch('disable-http-cache');

// ───────────────────────────── 运行环境 ─────────────────────────────

// 沙箱会把删除钩子注入子进程（PYTHONPATH/NODE_OPTIONS 等），
// 那会让 python 的临时文件操作被拦。全部剥掉再用。
const STRIP_ENV = ['PYTHONPATH', 'BASH_ENV', 'NODE_OPTIONS',
  'CODEBUDDY_SAFE_DELETE_SANDBOX', 'CODEBUDDY_SAFE_DELETE_BULK_GUARD'];

function cleanEnv() {
  const env = Object.assign({}, process.env);
  for (const k of STRIP_ENV) delete env[k];
  env.PYTHONIOENCODING = 'utf-8';
  env.PYTHONUTF8 = '1';
  env.PYTHONNOUSERSITE = '1';
  if (env.PATH) {
    env.PATH = env.PATH.split(path.delimiter)
      .filter((p) => p && !p.replace(/\\/g, '/').includes('app.asar.unpacked'))
      .join(path.delimiter);
  }
  return env;
}

function pythonCandidates() {
  const out = [];
  const push = (exe, prefix) => {
    if (!exe) return;
    prefix = prefix || [];
    if (out.some((o) => o.exe === exe && o.prefix.join(' ') === prefix.join(' '))) return;
    out.push({ exe, prefix });
  };
  try {
    const cached = fs.readFileSync(PY_CACHE, 'utf8').trim();
    if (cached) push(cached);
  } catch (_) { /* 没缓存，正常 */ }
  push(process.env.POJIA_PYTHON);
  // 项目自己的解释器优先：gui/.python-path 指定的 > 项目 .venv > PATH 上的 python。
  // 找不到才退回 WorkBuddy 自带那份 —— 它只是保底，不是默认。
  push(path.join(ROOT, '.venv', 'Scripts', 'python.exe'));
  push(path.join(ROOT, '.venv', 'bin', 'python'));
  push('python');
  push('python3');
  // WorkBuddy 自带的 python（保底）
  const home = process.env.USERPROFILE || app.getPath('home');
  const base = path.join(home, '.workbuddy', 'binaries', 'python', 'versions');
  try {
    for (const v of fs.readdirSync(base).sort().reverse()) {
      push(path.join(base, v, 'python.exe'));
      push(path.join(base, v, 'bin', 'python'));
    }
  } catch (_) { /* 不存在就跳过 */ }
  return out;
}

function probePython(cand) {
  try {
    const r = spawnSync(cand.exe, cand.prefix.concat(['-c', 'import sys;print("%d.%d.%d"%sys.version_info[:3])']),
      { timeout: 10000, encoding: 'utf8', windowsHide: true, env: cleanEnv() });
    if (r.status === 0 && /\d+\.\d+\.\d+/.test(r.stdout || '')) return (r.stdout || '').trim();
  } catch (_) { /* 这个候选不行 */ }
  return '';
}

function resolvePython(force) {
  if (py && !force) return py;
  for (const c of pythonCandidates()) {
    const v = probePython(c);
    if (v) { py = { exe: c.exe, prefix: c.prefix, version: v }; return py; }
  }
  py = null;
  return null;
}

function setPython(exe) {
  const cand = { exe, prefix: [] };
  const v = probePython(cand);
  if (!v) return { ok: false, error: '这个路径跑不通 python：' + exe };
  fs.writeFileSync(PY_CACHE, exe, 'utf8');
  py = { exe, prefix: [], version: v };
  return { ok: true, python: exe, version: v };
}

// ───────────────────────────── 桥调用 ─────────────────────────────

function callBridge(args, timeout) {
  return new Promise((resolve, reject) => {
    const p = resolvePython();
    if (!p) return reject(new Error('没有找到可用的 Python。请用「设置 Python」指定解释器路径。'));
    const child = spawn(p.exe, p.prefix.concat([BRIDGE]).concat(args),
      { cwd: ROOT, env: cleanEnv(), windowsHide: true });
    let out = '';
    let err = '';
    const timer = setTimeout(() => { try { child.kill(); } catch (_) {} }, timeout || 180000);
    child.stdout.on('data', (d) => { out += d.toString('utf8'); });
    child.stderr.on('data', (d) => { err += d.toString('utf8'); });
    child.on('error', (e) => { clearTimeout(timer); reject(e); });
    child.on('close', () => {
      clearTimeout(timer);
      const lines = out.split('\n').map((s) => s.trim()).filter(Boolean);
      for (let i = lines.length - 1; i >= 0; i--) {
        try { return resolve(JSON.parse(lines[i])); } catch (_) { /* 继续往前找 */ }
      }
      reject(new Error((err || out || '桥没有返回可解析的 JSON').slice(0, 800)));
    });
  });
}

function runAction(action, targets, flags, sender) {
  return new Promise((resolve) => {
    const p = resolvePython();
    if (!p) return resolve({ ok: false, error: '未找到 Python 解释器' });
    // 位置参数：run <action> <targets> [flags]；flags 是逗号分隔的开关名
    const argv = [BRIDGE, 'run', action, (targets || []).join(',')];
    if (flags && flags.length) argv.push(flags.join(','));
    const child = spawn(p.exe, p.prefix.concat(argv),
      { cwd: ROOT, env: cleanEnv(), windowsHide: true });
    running = child;
    let buf = '';
    let code = null;
    const feed = (chunk) => {
      buf += chunk.toString('utf8');
      let i;
      while ((i = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, i).trim();
        buf = buf.slice(i + 1);
        if (!line) continue;
        let evt = null;
        try { evt = JSON.parse(line); } catch (_) { continue; }
        if (sender && !sender.isDestroyed()) sender.send('pojia:log', evt);
        if (evt.type === 'exit') code = evt.code;
      }
    };
    child.stdout.on('data', feed);
    child.stderr.on('data', (d) => {
      if (sender && !sender.isDestroyed()) {
        sender.send('pojia:log', { type: 'line', target: 'gui', level: 'err', text: d.toString('utf8').trim() });
      }
    });
    child.on('close', (c) => { running = null; resolve({ ok: (code || c) === 0, code: code === null ? c : code }); });
    child.on('error', (e) => { running = null; resolve({ ok: false, error: String(e) }); });
  });
}

// ───────────────────────────── 窗口 ─────────────────────────────

function createWindow() {
  // 尺寸按屏幕工作区算 —— 固定 1340 在小屏上会把右侧面板和底栏挤出去
  const wa = screen.getPrimaryDisplay().workAreaSize;
  const width = Math.min(1400, Math.max(880, wa.width - 80));
  const height = Math.min(880, Math.max(580, wa.height - 80));

  mainWin = new BrowserWindow({
    width, height, minWidth: Math.min(900, width), minHeight: Math.min(600, height),
    frame: false, backgroundColor: '#f3f4f6', show: false,
    title: 'Muse',
    icon: path.join(ROOT, 'assets', 'logo.png'),
    webPreferences: {
      preload: path.join(GUI_DIR, 'preload.js'),
      contextIsolation: true, nodeIntegration: false, sandbox: false,
      spellcheck: false,
    },
  });
  mainWin.loadFile(path.join(GUI_DIR, 'renderer', 'index.html'));
  // 截图模式下窗口必须真的显示并处于前台，否则 offscreen 合成长时间不刷新，
  // capturePage 会反复拿到同一张旧帧（切分类后画面仍旧）。
  mainWin.once('ready-to-show', () => {
    mainWin.show();
    if (process.env.POJIA_SHOT || process.env.POJIA_REVERTTEST) mainWin.focus();
  });
  if (process.argv.includes('--dev')) mainWin.webContents.openDevTools({ mode: 'detach' });

  // 界面截图：POJIA_SHOT=1 时依次切视图截图落盘后退出（无需人工点）
  if (process.env.POJIA_SHOT) {
    mainWin.webContents.once('did-finish-load', async () => {
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const shots = [];
      const save = async (name) => {
        // capturePage 在无 GPU 时会取到合成器里较早的一帧（切分类后画面滞后）。
        // invalidate() 强制整页重绘，再隔两帧取图。
        try { mainWin.webContents.invalidate(); } catch (_) {}
        await mainWin.webContents.capturePage();
        await sleep(260);
        try { mainWin.webContents.invalidate(); } catch (_) {}
        await sleep(200);
        const img = await mainWin.webContents.capturePage();
        // 诊断：打印当前视图与分类栏位置，避免再"看图猜坐标"
        try {
          const diag = await mainWin.webContents.executeJavaScript(`(() => {
            const v = document.querySelector('.view.on');
            const c = document.getElementById('pmCats');
            const r = c && c.getBoundingClientRect();
            return JSON.stringify({
              view: v && v.id,
              iw: window.innerWidth, ih: window.innerHeight, dpr: window.devicePixelRatio,
              cats: r ? [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] : null,
              tabs: [...document.querySelectorAll('#pmCats .pmcat')].map(b => b.textContent),
              title: document.getElementById('pmAgentName').textContent,
              firstCat: (() => {
                const el = document.querySelector('#pmCats .pmcat');
                if (!el) return null;
                const sp = el.querySelector('span');
                const cs = getComputedStyle(el), ss = sp && getComputedStyle(sp);
                const r = el.getBoundingClientRect(), sr = sp && sp.getBoundingClientRect();
                return {
                  text: el.textContent,
                  cat: [cs.color, cs.fontSize, cs.opacity, cs.visibility],
                  span: ss && [ss.color, ss.fontSize, ss.opacity, ss.visibility, ss.display],
                  rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
                  spanRect: sr && [Math.round(sr.x), Math.round(sr.y), Math.round(sr.width), Math.round(sr.height)],
                };
              })(),
            });
          })()`);
          shots.push('diag:' + diag);
        } catch (_) {}
        const p = path.join(ROOT, '状态', name);
        fs.writeFileSync(p, img.toPNG());
        shots.push(name);
      };
      try {
        await sleep(9000);
        await save('_shot_deploy.png');
        const cardIcons = await mainWin.webContents.executeJavaScript(`(() => {
          const imgs = [...document.querySelectorAll('#cards .clogo')];
          return JSON.stringify(imgs.map(i => ({
            key: i.closest('.card') ? i.closest('.card').dataset.key : '?',
            src: i.getAttribute('src'),
            ok: i.complete && i.naturalWidth > 0,
            w: i.naturalWidth,
          })));
        })()`);
        shots.push('card-icons:' + cardIcons);
        const cardLook = await mainWin.webContents.executeJavaScript(`(() => {
          const c = document.querySelector('#cards .card');
          if (!c) return JSON.stringify({ err: 'no card' });
          const g = (s) => { const e = c.querySelector(s); if (!e) return null;
            const cs = getComputedStyle(e); return { color: cs.color, size: cs.fontSize, bg: cs.backgroundColor }; };
          return JSON.stringify({
            cardBg: getComputedStyle(c).backgroundColor,
            name: g('.cname'), meta: g('.cmeta'), idx: g('.cindex'), status: g('.cstatus'),
            logo: (() => { const i = c.querySelector('.clogo'); if (!i) return null;
              const r = i.getBoundingClientRect();
              return { src: i.getAttribute('src'), w: Math.round(r.width), h: Math.round(r.height) }; })(),
          });
        })()`);
        shots.push('card-look:' + cardLook);
        const logoGeo = await mainWin.webContents.executeJavaScript(`(() => {
          const out = [];
          document.querySelectorAll('#cards .card').forEach(c => {
            const img = c.querySelector('.clogo');
            const nm = c.querySelector('.cname');
            if (!img || !nm) return;
            const ir = img.getBoundingClientRect(), nr = nm.getBoundingClientRect();
            const cs = getComputedStyle(img);
            out.push({
              key: c.dataset.key,
              img: [Math.round(ir.x), Math.round(ir.y), Math.round(ir.width), Math.round(ir.height)],
              name: [Math.round(nr.x), Math.round(nr.y)],
              op: cs.opacity, vis: cs.visibility, disp: cs.display, filter: cs.filter,
              complete: img.complete, nw: img.naturalWidth,
            });
          });
          return JSON.stringify(out);
        })()`);
        shots.push('logo-geo:' + logoGeo);
        await mainWin.webContents.executeJavaScript(
          "document.querySelector('.snav[data-view=prompts]').click()");
        await sleep(2500);
        // 等分类标签真的画出来再截 —— 之前截在首次 refreshDocs 返回前，图里是空的
        let gridState = '';
        for (let i = 0; i < 25; i++) {
          const st = await mainWin.webContents.executeJavaScript(
            "JSON.stringify([document.querySelectorAll('#pmCats .pmcat').length,"
            + "document.querySelectorAll('#pmGrid .pmrow').length,"
            + "(document.getElementById('pmGrid')||{}).innerHTML.length||0])");
          gridState = st;
          const [nc, nr] = JSON.parse(st);
          if (nc > 0 && nr > 0) break;
          await sleep(400);
        }
        shots.push('gridwait:' + gridState);
        shots.push('pmdebug:' + await mainWin.webContents.executeJavaScript(
          "JSON.stringify(window.__pmDebug && window.__pmDebug())"));
        await sleep(900);          // 等一帧合成，否则截到的是分类栏出现前的画面
        await save('_shot_prompts.png');
        // 总览弹窗：ChatGPT 的「部署了什么」四行人话
        await mainWin.webContents.executeJavaScript(
          "document.getElementById('btnPatchAll').click()");
        await sleep(4000);
        shots.push('patchsummary:' + await mainWin.webContents.executeJavaScript(`JSON.stringify({
          target: document.getElementById('patchTarget').textContent,
          metrics: [...document.querySelectorAll('#patchBody .pmetric')].map(m => ({
            b: (m.querySelector('b') || {}).textContent || '',
            s: (m.querySelector('span') || {}).textContent || '',
          })),
          groups: [...document.querySelectorAll('#patchBody .pgroup .ghead')].map(g => g.textContent),
        })`));
        await save('_shot_patch_summary.png');
        await mainWin.webContents.executeJavaScript(
          "document.getElementById('btnPatchClose').click()");
        await sleep(600);
        // 官方原版分类：两张只读卡（AGENTS.md / config.toml），开关必须禁用
        await mainWin.webContents.executeJavaScript(`(() => {
          const tabs = [...document.querySelectorAll('#pmCats .pmcat')];
          const t = tabs.find(b => b.textContent.indexOf('官方原版') >= 0);
          if (t) t.click();
        })()`);
        await sleep(1200);
        const officialCard = await mainWin.webContents.executeJavaScript(`(() => {
          const rows = [...document.querySelectorAll('#pmGrid .pmrow')];
          return JSON.stringify(rows.map(r => ({
            name: (r.querySelector('.pmcname') || {}).textContent,
            tags: [...r.querySelectorAll('.pmtag')].map(t => t.textContent),
            off: r.classList.contains('off-doc'),
            switchDisabled: (r.querySelector('.pmswitch input') || {}).disabled,
            editBtns: [...r.querySelectorAll('.iconbtn')].map(b => b.title),
          })));
        })()`);
        shots.push('official:' + officialCard);
        await save('_shot_official.png');
        // 日志行样式自检：时间戳 / 级别徽标 / 颜色分级都要在
        const logLook = await mainWin.webContents.executeJavaScript(`(() => {
          if (typeof logLine === 'function') {
            logLine('warn', 'sys', '样式自检 WARN');
            logLine('err', 'sys', '样式自检 ERR');
          }
          const rows = [...document.querySelectorAll('#logBody .lrow')].slice(-6);
          return JSON.stringify(rows.map(r => ({
            cls: r.className,
            ts: (r.querySelector('.lts') || {}).textContent || '',
            badge: (r.querySelector('.lbadge') || {}).textContent || '',
            color: getComputedStyle(r.querySelector('.ltext') || r).color,
            bg: getComputedStyle(r).backgroundColor,
          })));
        })()`);
        shots.push('loglook:' + logLook);
        const iconState = await mainWin.webContents.executeJavaScript(`(() => {
          const imgs = [...document.querySelectorAll('.pmagent-glyph img')];
          return JSON.stringify(imgs.map(i => ({ src: i.getAttribute('src'), ok: i.complete && i.naturalWidth > 0, w: i.naturalWidth })));
        })()`);
        shots.push('imgs:' + iconState);
        const cats = await mainWin.webContents.executeJavaScript(
          "JSON.stringify([...document.querySelectorAll('#pmCats .pmcat')].map(b => b.textContent))");
        shots.push('cats:' + cats);
        const catGeo = await mainWin.webContents.executeJavaScript(`(() => {
          const ids = ['pmAgents','pmGrid','pmCats'];
          const out = {};
          document.querySelectorAll('.pmstatus,.pmcatbar,.pmgrid,.pmagents,#pmCats').forEach(el => {
            const r = el.getBoundingClientRect();
            out[(el.id || el.className)] = [Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height),getComputedStyle(el).display];
          });
          return JSON.stringify(out);
        })()`);
        shots.push('catgeo:' + catGeo);
        const ar = await mainWin.webContents.executeJavaScript(
          "(()=>{const r=document.getElementById('pmAgents').getBoundingClientRect(); return JSON.stringify({x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)});})()");
        const arj = JSON.parse(ar);
        const aimg = await mainWin.webContents.capturePage({
          x: arj.x - 4, y: arj.y - 4, width: arj.w + 8, height: arj.h + 8,
        });
        fs.writeFileSync(path.join(ROOT, '状态', '_shot_agents.png'), aimg.toPNG());
        shots.push('_shot_agents.png');
        // 备注弹窗：点三横线图标 → 输入 → 保存（用临时文件，别覆盖用户的真实备注）
        // 先确认列表真的渲染出来了，再点备注
        for (let i = 0; i < 25; i++) {
          const n = await mainWin.webContents.executeJavaScript(
            "document.querySelectorAll('#pmGrid .pmrow').length");
          if (n > 0) break;
          await sleep(400);
        }
        const memoFlow = await mainWin.webContents.executeJavaScript(`(async () => {
          const btn = document.querySelector('#pmGrid .iconbtn[title^="备注"]');
          if (!btn) return JSON.stringify({ step: 'no-btn' });
          btn.click();
          await new Promise(r => setTimeout(r, 300));
          const open = document.getElementById('pmInputModal').classList.contains('on');
          const inp = document.getElementById('pmInputText');
          const prev = inp.value;
          inp.value = '自动化测试备注';
          inp.dispatchEvent(new Event('input', { bubbles: true }));
          document.getElementById('btnPmInputOk').click();
          await new Promise(r => setTimeout(r, 900));
          const card = document.querySelector('#pmGrid .pmrow');
          const desc = card && card.querySelector('.pmcdesc');
          const out = {
            open,
            closed: !document.getElementById('pmInputModal').classList.contains('on'),
            desc: desc && desc.textContent,
            hasMemoClass: desc && desc.className.includes('has-memo'),
            prev,
          };
          // 还原成点之前的备注（清空也行，复原也行）
          btn.click();
          await new Promise(r => setTimeout(r, 300));
          const inp2 = document.getElementById('pmInputText');
          inp2.value = prev;
          inp2.dispatchEvent(new Event('input', { bubbles: true }));
          document.getElementById('btnPmInputOk').click();
          await new Promise(r => setTimeout(r, 700));
          return JSON.stringify(out);
        })()`);
        shots.push('memo:' + memoFlow);
        {
          const tagDiag = await mainWin.webContents.executeJavaScript(`(() => {
            const c = document.querySelector('#pmGrid .pmrow');
            if (!c) return JSON.stringify({ err: 'no card' });
            return JSON.stringify({
              tags: [...c.querySelectorAll('.pmtag')].map(t => [t.textContent, t.className]),
              meta: (c.querySelector('.pmrow-meta') || {}).textContent,
              foot: (c.querySelector('.pmrow-foot') || {}).textContent,
            });
          })()`);
          shots.push('tagdiag:' + tagDiag);
        }
        await sleep(1200);
        // 弹窗打开态截图
        await mainWin.webContents.executeJavaScript(
          "document.querySelector('#pmGrid .iconbtn[title^=\"备注\"]').click()");
        await sleep(900);
        const modalOn = await mainWin.webContents.executeJavaScript(
          "document.getElementById('pmInputModal').classList.contains('on')");
        shots.push('modalOn:' + modalOn);
        shots.push('modaltext:' + await mainWin.webContents.executeJavaScript(`JSON.stringify({
          title: document.getElementById('pmInputTitle').textContent,
          sub: document.getElementById('pmInputSub').textContent,
          tags: [...document.querySelectorAll('#pmInputTags .pmtag')].map(t => t.textContent),
          inputVal: document.getElementById('pmInputText').value,
          sel: (() => { const el = document.getElementById('pmInputText');
                         return [el.selectionStart, el.selectionEnd, el.value.length]; })(),
          rect: (() => { const r = document.getElementById('pmInputModal').getBoundingClientRect();
                         return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; })(),
        })`));
        await save('_shot_memo.png');
        await mainWin.webContents.executeJavaScript(
          "document.getElementById('btnPmInputCancel').click()");
        const iq = await mainWin.webContents.executeJavaScript(`(() => {
          const b = [...document.querySelectorAll('#pmGrid .iconbtn')];
          const card = document.querySelector('#pmGrid .pmrow');
          const cr = card && card.getBoundingClientRect();
          return JSON.stringify({
            iconBtns: b.length,
            svgs: b.filter(x => x.querySelector('svg')).length,
            first: b[0] ? { w: Math.round(b[0].getBoundingClientRect().width), h: Math.round(b[0].getBoundingClientRect().height), title: b[0].title } : null,
            card: cr ? [Math.round(cr.x), Math.round(cr.y), Math.round(cr.width), Math.round(cr.height)] : null,
          });
        })()`);
        shots.push('icons:' + iq);
        await mainWin.webContents.executeJavaScript(
          "document.querySelector('#pmGrid .pmrow.off-doc') && document.querySelector('#pmGrid .pmrow.off-doc').scrollIntoView({block:'center'})");
        await sleep(700);
        await save('_shot_prompts_scrolled.png');
        // SKILL.md 分类：标签与中文名
        await mainWin.webContents.executeJavaScript(`(() => {
          const tabs = [...document.querySelectorAll('#pmCats .pmcat')];
          const t = tabs.find(b => b.textContent.indexOf('SKILL.md') >= 0);
          if (t) t.click();
        })()`);
        await sleep(1200);
        await save('_shot_skill_cat.png');
        const skillCard = await mainWin.webContents.executeJavaScript(`(() => {
          const rows = [...document.querySelectorAll('#pmGrid .pmrow')];
          return JSON.stringify(rows.slice(0, 4).map(c => ({
            name: (c.querySelector('.pmcname') || {}).textContent,
            desc: (c.querySelector('.pmcdesc') || {}).textContent,
            tags: [...c.querySelectorAll('.pmtag')].map(t => t.textContent),
          })));
        })()`);
        shots.push('skillcard:' + skillCard);
        // 干净状态下打开 SKILL 备注：输入框应为空（不再预填说明）
        shots.push('skillmemo-builtin:' + await mainWin.webContents.executeJavaScript(`(async () => {
          document.querySelector('#pmGrid .iconbtn[title^="备注"]').click();
          await new Promise(r => setTimeout(r, 400));
          const out = JSON.stringify({ val: document.getElementById('pmInputText').value });
          document.getElementById('btnPmInputCancel').click();
          await new Promise(r => setTimeout(r, 200));
          return out;
        })()`));
        // SKILL 卡片的备注弹窗：先写一条作为「已有备注」，再重新打开验证带出
        await mainWin.webContents.executeJavaScript(`(async () => {
          document.querySelector('#pmGrid .iconbtn[title^="备注"]').click();
          await new Promise(r => setTimeout(r, 300));
          const inp = document.getElementById('pmInputText');
          inp.value = '已有备注-验证';
          inp.dispatchEvent(new Event('input', { bubbles: true }));
          document.getElementById('btnPmInputOk').click();
          await new Promise(r => setTimeout(r, 800));
        })()`);
        await sleep(500);
        await mainWin.webContents.executeJavaScript(
          "document.querySelector('#pmGrid .iconbtn[title^=\"备注\"]').click()");
        await sleep(600);
        shots.push('skillmemo:' + await mainWin.webContents.executeJavaScript(`JSON.stringify({
          title: document.getElementById('pmInputTitle').textContent,
          inputVal: document.getElementById('pmInputText').value,
        })`));
        await save('_shot_skill_memo.png');
        // 端到端：在 SKILL 卡片上写备注 → 保存 → 再点开，输入框必须带出来
        const roundTrip = await mainWin.webContents.executeJavaScript(`(async () => {
          const inp = document.getElementById('pmInputText');
          const before = inp.value;
          inp.value = '临时备注-自检';
          inp.dispatchEvent(new Event('input', { bubbles: true }));
          document.getElementById('btnPmInputOk').click();
          await new Promise(r => setTimeout(r, 900));
          document.querySelector('#pmGrid .iconbtn[title^="备注"]').click();
          await new Promise(r => setTimeout(r, 400));
          const again = document.getElementById('pmInputText').value;
          document.getElementById('btnPmInputCancel').click();
          return JSON.stringify({ before, again });
        })()`);
        shots.push('skillmemo-roundtrip:' + roundTrip);
        // 再开一次：确认备注持久（这一步不改任何东西）
        const third = await mainWin.webContents.executeJavaScript(`(async () => {
          document.querySelector('#pmGrid .iconbtn[title^="备注"]').click();
          await new Promise(r => setTimeout(r, 500));
          const v = document.getElementById('pmInputText').value;
          const cur = '';
          document.getElementById('btnPmInputCancel').click();
          return JSON.stringify({ v, cur });
        })()`);
        shots.push('skillmemo-third:' + third);
        // 干净收尾：把自检写的备注清掉，别污染用户表
        await mainWin.webContents.executeJavaScript(`(async () => {
          document.querySelector('#pmGrid .iconbtn[title^="备注"]').click();
          await new Promise(r => setTimeout(r, 300));
          const inp = document.getElementById('pmInputText');
          inp.value = '';
          inp.dispatchEvent(new Event('input', { bubbles: true }));
          document.getElementById('btnPmInputOk').click();
          await new Promise(r => setTimeout(r, 800));
        })()`);
        shots.push('skillmemo-clean:' + await mainWin.webContents.executeJavaScript(
          "JSON.stringify([...document.querySelectorAll('#pmGrid .pmrow')].slice(0,3)"
          + ".map(r => (r.querySelector('.pmcdesc')||{}).textContent))"));
        await mainWin.webContents.executeJavaScript(`(async () => {
          document.querySelector('#pmGrid .iconbtn[title^="备注"]').click();
          await new Promise(r => setTimeout(r, 300));
          const inp = document.getElementById('pmInputText');
          inp.value = '';
          inp.dispatchEvent(new Event('input', { bubbles: true }));
          document.getElementById('btnPmInputOk').click();
        })()`);
        await sleep(700);
        await mainWin.webContents.executeJavaScript(
          "document.querySelector('#pmCats .pmcat').click()");
        await sleep(800);
        // config.toml 分类：应显示本机内容，不是空
        await mainWin.webContents.executeJavaScript(`(() => {
          const tabs = [...document.querySelectorAll('#pmCats .pmcat')];
          const t = tabs.find(b => b.textContent.indexOf('config.toml') >= 0);
          if (t) t.click();
        })()`);
        await sleep(1200);
        await save('_shot_config_cat.png');
        const cfgCard = await mainWin.webContents.executeJavaScript(`(() => {
          const c = document.querySelector('#pmGrid .pmrow');
          if (!c) return JSON.stringify({ err: 'no card',
            grid: (document.getElementById('pmGrid') || {}).innerHTML ? 'has html' : 'empty',
            view: (document.querySelector('.view.on') || {}).id,
            cats: document.querySelectorAll('#pmCats .pmcat').length });
          return JSON.stringify({
            name: (c.querySelector('.pmcname') || {}).textContent,
            desc: (c.querySelector('.pmcdesc') || {}).textContent,
            meta: (c.querySelector('.pmcpath') || {}).textContent || '(无分类名)',
            tags: [...c.querySelectorAll('.pmtag')].map(t => t.textContent),
            foot: (c.querySelector('.pmrow-foot') || {}).textContent,
            cards: document.querySelectorAll('#pmGrid .pmrow').length,
          });
        })()`);
        shots.push('cfgcard:' + cfgCard);
        await mainWin.webContents.executeJavaScript(
          "document.querySelector('#pmGrid .iconbtn[title^=\"查看\"], #pmGrid .iconbtn[title^=\"编辑\"]')"
          + " && document.querySelector('#pmGrid .iconbtn[title^=\"查看\"]').click()");
        await sleep(1500);
        const cfgEdit = await mainWin.webContents.executeJavaScript(`(() => {
          const a = document.getElementById('pmArea');
          return JSON.stringify({ len: (a.value || '').length, head: (a.value || '').slice(0, 60),
            path: document.getElementById('pmPath').textContent,
            hint: document.getElementById('pmHint').textContent });
        })()`);
        shots.push('cfgedit:' + cfgEdit);
        shots.push('cfghead:' + await mainWin.webContents.executeJavaScript(`JSON.stringify({
          title: document.getElementById('pmFormTitle').textContent,
          head: document.getElementById('pmFileHead').textContent,
          path: document.getElementById('pmPath').textContent,
          name: document.getElementById('pmFieldName').value,
          cat: document.getElementById('pmFieldCat').value,
          geo: (() => {
            const out = {};
            ['pmFormWrap','pmFormTitle','pmPath','pmFieldName','pmFieldCat','pmArea','pmHint'].forEach(id => {
              const el = document.getElementById(id); if (!el) return;
              const r = el.getBoundingClientRect();
              out[id] = [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)];
            });
            return out;
          })(),
        })`));
        await save('_shot_config_edit.png');
        await mainWin.webContents.executeJavaScript(
          "document.getElementById('btnPmClose').click()");
        await sleep(800);
        await mainWin.webContents.executeJavaScript(
          "document.querySelector('#pmCats .pmcat').click()");
        await sleep(800);
        // SKILL 编辑页：标题不能重复
        await mainWin.webContents.executeJavaScript(`(() => {
          const tabs = [...document.querySelectorAll('#pmCats .pmcat')];
          const t = tabs.find(b => b.textContent.indexOf('SKILL.md') >= 0);
          if (t) t.click();
        })()`);
        await sleep(1000);
        await mainWin.webContents.executeJavaScript(
          "document.querySelector('#pmGrid .iconbtn[title^=\"编辑\"]').click()");
        await sleep(1200);
        shots.push('skilledit:' + await mainWin.webContents.executeJavaScript(`JSON.stringify({
          title: document.getElementById('pmFormTitle').textContent,
          head: document.getElementById('pmFileHead').textContent,
          len: (document.getElementById('pmArea').value || '').length,
        })`));
        await save('_shot_skill_edit.png');
        await mainWin.webContents.executeJavaScript(
          "document.getElementById('btnPmClose').click()");
        await sleep(600);
        await mainWin.webContents.executeJavaScript(
          "document.querySelector('#pmCats .pmcat').click()");
        await sleep(800);
        await mainWin.webContents.executeJavaScript(
          "document.querySelector('#pmGrid .iconbtn[title^=\"编辑\"]') && document.querySelector('#pmGrid .iconbtn[title^=\"编辑\"]').click()");
        await sleep(2000);
        await save('_shot_prompt_edit.png');
        await mainWin.webContents.executeJavaScript(
          "document.getElementById('btnPmClose').click(); document.querySelector('.snav[data-view=skills]').click()");
        await sleep(2000);
        await save('_shot_skills.png');

        // 更新日志弹窗：点底部版本号 → 拉 GitHub releases → 截图
        try {
          await mainWin.webContents.executeJavaScript(
            "(document.getElementById('tbVersion')||{click(){}}).click(); 'ok'");
          await sleep(2600);
          const diag = await mainWin.webContents.executeJavaScript(`(() => {
            const m = document.getElementById('upModal');
            const st = m ? getComputedStyle(m) : null;
            return JSON.stringify({
              hasModal: !!m, cls: m && m.className,
              display: st && st.display, z: st && st.zIndex, op: st && st.opacity,
              bodyLen: (document.getElementById('upBody')||{}).innerHTML?.length || 0,
              meta: (document.getElementById('upMeta')||{}).textContent || '',
              hint: (document.getElementById('upHint')||{}).textContent || ''
            });
          })()`);
          shots.push('update:' + diag);
        } catch (e) {
          shots.push('update-error:' + String(e).slice(0, 200));
        }
        await save('_shot_update.png');
      } catch (e) {
        shots.push('error:' + String(e).slice(0, 200));
      }
      fs.writeFileSync(path.join(ROOT, '状态', '_shot.json'), JSON.stringify(shots), 'utf8');
      app.quit();
    });
  }

  // 还原滑块自检：POJIA_REVERTTEST=1 —— 点 ChatGPT 卡片的滑块，核对四路落点与指令页开关
  if (process.env.POJIA_REVERTTEST) {
    mainWin.webContents.once('did-finish-load', async () => {
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const save = async (name) => {
        // 丢一帧再截：GPU 关掉时 capturePage 偶尔拿到上一帧
        await mainWin.webContents.capturePage();
        await sleep(150);
        const img = await mainWin.webContents.capturePage();
        fs.writeFileSync(path.join(ROOT, '状态', name), img.toPNG());
      };
      const out = {};
      const probe = async () => mainWin.webContents.executeJavaScript(`JSON.stringify((() => {
        const card = [...document.querySelectorAll('.card')].find(c => c.dataset.key === 'codex');
        const sl = card && card.querySelector('.slider');
        return {
          sliderOn: sl ? sl.classList.contains('on') : null,
          state: (card && card.querySelector('.sliderstate') || {}).textContent || '',
          status: (card && card.querySelector('.cstatus') || {}).textContent || '',
        };
      })())`);
      try {
        await sleep(9000);
        out.before = JSON.parse(await probe());
        const t0 = Date.now();
        await mainWin.webContents.executeJavaScript(
          "document.querySelector('.card[data-key=codex] .slider').click()");
        // 立刻读一次：滑块应当在 Python 返回之前就翻过去
        await sleep(250);
        out.immediate = JSON.parse(await probe());
        out.immediateMs = Date.now() - t0;
        await sleep(25000);
        out.after = JSON.parse(await probe());
        out.afterMs = Date.now() - t0;
        out.afterLog = await mainWin.webContents.executeJavaScript(
          "document.getElementById('logBody').textContent.slice(-1600)");
        await save('_gui_revert_1_card.png');
        // 指令提示词页：还原后应全部显示未启用
        await mainWin.webContents.executeJavaScript(
          "document.querySelector('.snav[data-view=prompts]').click()");
        await sleep(2500);
        // 切页会自动重扫（不必再点「重新扫描」）—— 留 3.5s 等 bridge 返回
        await sleep(3500);
        out.docs = JSON.parse(await mainWin.webContents.executeJavaScript(`JSON.stringify({
          rows: document.querySelectorAll('#pmGrid .pmrow').length,
          on: document.querySelectorAll('#pmGrid .pmrow:not(.off-doc)').length,
          switch: (document.getElementById('pmDeployInput') || {}).checked,
        })`));
        await save('_gui_revert_2_prompts.png');
        // 复位：再拨回去，环境保持部署态
        await mainWin.webContents.executeJavaScript(
          "document.querySelector('.snav[data-view=deploy]').click()");
        await sleep(1500);
        await mainWin.webContents.executeJavaScript(
          "document.querySelector('.card[data-key=codex] .slider').click()");
        await sleep(25000);
        out.reset = JSON.parse(await probe());
        await save('_gui_revert_3_reset.png');
        // 复位后回指令页再确认一次开关（这一步只看，不改）
        await mainWin.webContents.executeJavaScript(
          "document.querySelector('.snav[data-view=prompts]').click()");
        await sleep(2500);
        await mainWin.webContents.executeJavaScript(
          "document.getElementById('btnPmRefresh').click()");
        await sleep(4000);
        out.docsReset = JSON.parse(await mainWin.webContents.executeJavaScript(`JSON.stringify({
          rows: document.querySelectorAll('#pmGrid .pmrow').length,
          on: document.querySelectorAll('#pmGrid .pmrow:not(.off-doc)').length,
          switch: (document.getElementById('pmDeployInput') || {}).checked,
          first: [...document.querySelectorAll('#pmGrid .pmrow')].slice(0, 2)
            .map(r => [r.querySelector('.pmcname') && r.querySelector('.pmcname').textContent,
                       r.classList.contains('off-doc')]),
        })`));
        await save('_gui_revert_4_prompts_reset.png');
      } catch (e) {
        out.error = String(e).slice(0, 300);
      }
      fs.writeFileSync(path.join(ROOT, '状态', '_gui_revert.json'),
        JSON.stringify(out, null, 1), 'utf8');
      app.quit();
    });
  }
  // ChatGPT 全功能实机点测：POJIA_CODEXTEST=1
  // 全部通过真实 DOM 点击驱动（卡片滑块 / 指令页每行开关 / 工具栏按钮），
  // 每步同时核对「页面显示」和「磁盘落点」两侧。
  if (process.env.POJIA_CODEXTEST) {
    mainWin.webContents.once('did-finish-load', async () => {
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const js = (s) => mainWin.webContents.executeJavaScript(s);
      const save = async (name) => {
        await mainWin.webContents.capturePage();
        await sleep(160);
        const img = await mainWin.webContents.capturePage();
        fs.writeFileSync(path.join(ROOT, '状态', name), img.toPNG());
      };
      const R = { steps: [], notes: [], shots: [] };
      const ok = (name, cond, extra) => {
        R.steps.push({ name, ok: !!cond, extra: cond ? '' : (extra === undefined ? '' : extra) });
      };
      const note = (s) => R.notes.push(String(s));
      const shot = async (n) => { await save(n); R.shots.push(n); };

      // 页面侧状态一次读全（少踩 executeJavaScript 的往返时序）
      const ui = () => js(`JSON.stringify((() => {
        const card = [...document.querySelectorAll('.card')].find(c => c.dataset.key === 'codex');
        const sl = card && card.querySelector('.slider');
        const rows = [...document.querySelectorAll('#pmGrid .pmrow')];
        const by = {};
        for (const r of rows) by[r.dataset.id] = !r.classList.contains('off-doc');
        return {
          sliderOn: sl ? sl.classList.contains('on') : null,
          sliderBusy: sl ? sl.classList.contains('busy') : null,
          sliderState: (card && card.querySelector('.sliderstate') || {}).textContent || '',
          cardStatus: (card && card.querySelector('.cstatus') || {}).textContent || '',
          stats: card ? [...card.querySelectorAll('.cstat')].map(n => n.textContent.replace(/\\s+/g, ' ').trim()) : [],
          rows, n: rows.length,
          on: rows.filter(r => !r.classList.contains('off-doc')).length,
          docOn: by,
          cats: [...document.querySelectorAll('#pmCats .pmcat')].map(b => b.dataset.cat),
          logTail: (document.getElementById('logBody') || {}).textContent || '',
          status: (document.getElementById('sbMsg') || {}).textContent || '',
        };
      })())`);

      // 我们投放的技能 id（80 叶子 + 6 父类目）。只用来数「我们写的」SKILL.md，
      // 用户自己的技能目录（ctf-* / mature-fiction 等）不计入。
      const ROUTE_IDS = (() => {
        try {
          const o = spawnSync(py.exe, (py.prefix || []).concat([
            '-c',
            'import sys;sys.path.insert(0,"engine");import codex_deploy as a;' +
            'print("\\n".join(sorted(a._routes().skill_dirs())))',
          ]), { cwd: ROOT, env: cleanEnv(), encoding: 'utf8', windowsHide: true });
          return String(o.stdout || '').split(/\s+/).filter(Boolean);
        } catch (_) { return []; }
      })();

      // 磁盘落点。skills 只数「我们投放的」—— 用户自己的技能目录不能算进来。
      const disk = () => {
        const home = path.join(process.env.USERPROFILE || process.env.HOME, '.codex');
        const out = { home, prompts: [], agents: false, cfgPtr: '', cfg: false,
                      ours: 0, bak: 0 };
        try { out.prompts = fs.readdirSync(path.join(home, 'prompts')).sort(); } catch (_) {}
        let ag = '';
        try { ag = fs.readFileSync(path.join(home, 'AGENTS.md'), 'utf8'); } catch (_) {}
        out.agents = ag.includes('POJIA-CODEX:BEGIN');
        let cf = '';
        try { cf = fs.readFileSync(path.join(home, 'config.toml'), 'utf8'); } catch (_) {}
        out.cfg = /^\s*model_instructions_file\s*=/m.test(cf);
        const m = cf.match(/^\s*model_instructions_file\s*=\s*"(.*)"/m);
        out.cfgPtr = m ? m[1] : '';
        try {
          const sd = path.join(home, 'skills');
          out.ours = ROUTE_IDS.length
            ? ROUTE_IDS.filter((id) => fs.existsSync(path.join(sd, id, 'SKILL.md'))).length
            : -1;
        } catch (_) { out.ours = -1; }
        try { out.bak = fs.readdirSync(path.join(home, 'pojia-backups')).length; } catch (_) {}
        return out;
      };

      const deployView = async () => {
        await js("document.querySelector('.snav[data-view=deploy]').click()");
        await sleep(900);
      };
      // 等界面空闲：滑块 busy / 工具栏禁用 / 底部状态都是「有活儿在跑」的信号。
      // 上一轮操作没结束时点下去会被 toast 吞掉，测试就会误判成功能坏了。
      const waitIdle = async (ms) => {
        const t0 = Date.now();
        while (Date.now() - t0 < (ms || 45000)) {
          const busy = JSON.parse(await js(`JSON.stringify(!!(
            document.querySelector('.slider.busy')
            || document.querySelector('#btnDeploy:disabled')
            || document.querySelector('#btnTest:disabled')))`));
          if (!busy) {
            // 再等一次状态刷新回来，卡片与磁盘对齐
            await sleep(700);
            const busy2 = JSON.parse(await js(`JSON.stringify(!!(
              document.querySelector('.slider.busy')
              || document.querySelector('#btnDeploy:disabled')))`));
            if (!busy2) return true;
          }
          await sleep(400);
        }
        return false;
      };
      const clickSlider = async () => {
        await deployView();
        await waitIdle();
        await js("document.querySelector('.card[data-key=codex] .slider').click()");
      };
      const gotoDocs = async () => {
        await js("document.querySelector('.snav[data-view=prompts]').click()");
        await sleep(2600);
        await js("document.querySelector('#pmAgents .pmagent[data-agent=codex]').click()");
        await sleep(3200);
      };
      const pickCat = async (cat) => {
        await js(`(() => { const b = document.querySelector('#pmCats .pmcat[data-cat="${cat}"]');
                   if (b) b.click(); })()`);
        await sleep(700);
      };
      const clickRow = async (id) => js(`(() => {
        const r = document.querySelector('#pmGrid .pmrow[data-id="${id}"]');
        if (!r) return 'missing';
        const inp = r.querySelector('.pmswitch input');
        if (!inp) return 'no-input';
        inp.click();
        return 'clicked';
      })()`);
      const rowName = async (id) => js(`(() => {
        const r = document.querySelector('#pmGrid .pmrow[data-id="${id}"]');
        return r ? (r.querySelector('.pmcname') || {}).textContent || '' : '';
      })()`);

      try {
        await sleep(9000);
        let s = JSON.parse(await ui());
        let d = disk();
        R.initial = { ui: s, disk: d };
        const startedDeployed = s.sliderOn === true;
        note('初始滑块=' + s.sliderOn + ' 状态=' + s.sliderState +
             ' prompts=' + d.prompts.join(',') + ' 我们的技能=' + d.ours);
        ok('卡片渲染出 ChatGPT', s.sliderOn !== null, s.cardStatus);
        await shot('_ct_1_initial.png');

        // 从干净态起跑
        if (s.sliderOn) {
          await clickSlider();
          await waitIdle(60000);
          await sleep(1200);
          s = JSON.parse(await ui());
          ok('初始为部署态 → 拨左还原', s.sliderOn === false, s.sliderState);
          await shot('_ct_2_pre_reverted.png');
        }
        d = disk();
        ok('还原态：prompts 空', d.prompts.length === 0, d.prompts);
        ok('还原态：AGENTS 无标记块', d.agents === false);
        ok('还原态：config 无我们的指针', !/prompts[\\/]/.test(d.cfgPtr), d.cfgPtr);
        ok('还原态：我们的技能 0 份', d.ours === 0, d.ours);

        // 指令页：分类与全关
        await gotoDocs();
        s = JSON.parse(await ui());
        R.docsIdle = { cats: s.cats, on: s.on };
        ok('指令页分类齐四类', s.cats.join(',') === 'instruction,agents,config,skill', s.cats);
        ok('还原后指令页无启用项', s.on === 0, s.on);
        await shot('_ct_3_docs_idle.png');

        // 部署：卡片滑块拨右，视觉先行
        await clickSlider();
        await sleep(350);
        s = JSON.parse(await ui());
        ok('拨右后滑块立刻 on 且 busy', s.sliderOn === true && s.sliderBusy === true,
          s.sliderOn + '/' + s.sliderBusy);
        await waitIdle(60000);
        await sleep(1200);
        s = JSON.parse(await ui());
        d = disk();
        const pack = d.prompts[0] || '';
        R.afterDeploy = { ui: s, disk: d };
        ok('部署完成：滑块 on', s.sliderOn === true, s.sliderState);
        ok('部署完成：卡片状态已部署', /已部署/.test(s.cardStatus), s.cardStatus);
        ok('部署完成：prompts 恰一份', d.prompts.length === 1, d.prompts);
        ok('部署完成：AGENTS 标记块', d.agents === true);
        ok('部署完成：config 指针指向 prompts/', /prompts[\\/]/.test(d.cfgPtr), d.cfgPtr);
        ok('部署完成：我们的技能 86 份', d.ours === 86, d.ours);
        ok('部署完成：备份 2 份', d.bak === 2, d.bak);
        ok('卡片指标里列出指令源', s.stats.some((t) => t.includes(pack)), s.stats.join(' | '));
        await shot('_ct_4_deployed.png');

        // 指令页：部署后的开关状态。
        // 注意 pickCat 只切分类、不重新扫盘 —— 读开关前先刷新一次，别拿旧 DOM 判定。
        await gotoDocs();
        await pickCat('instruction');
        const instrIds = JSON.parse(await js(`JSON.stringify([...document.querySelectorAll('#pmGrid .pmrow')]
          .map(r => r.dataset.id))`));
        s = JSON.parse(await ui());
        ok('指令页指令源 3 条', instrIds.length === 3, instrIds);
        ok('当前源显示注入中', s.docOn['targets/codex/instructions/' + pack] === true, pack);
        ok('另外两份未部署',
          instrIds.filter((id) => id !== 'targets/codex/instructions/' + pack)
            .every((id) => s.docOn[id] === false), instrIds);
        await pickCat('agents');
        s = JSON.parse(await ui());
        ok('AGENTS 开关开', s.docOn['codex:agents'] === true);
        await pickCat('config');
        s = JSON.parse(await ui());
        ok('config 开关开', s.docOn['codex:config'] === true);
        await pickCat('skill');
        s = JSON.parse(await ui());
        ok('技能分类 86 条且全启用', s.n === 86 && s.on === 86, s.n + '/' + s.on);
        await shot('_ct_5_docs_deployed.png');

        // 换源：点另一份指令源的开关
        await pickCat('instruction');
        const other = instrIds.find((id) => id !== 'targets/codex/instructions/' + pack);
        const otherName = other ? (other.split('/').pop() || '') : '';
        ok('存在第二份指令源', !!other, instrIds);
        if (other) {
          ok('换源点击已派发', (await clickRow(other)) === 'clicked');
          await sleep(22000);
          s = JSON.parse(await ui());
          d = disk();
          ok('换源后 prompts 只剩新源', d.prompts.length === 1 && d.prompts[0] === otherName, d.prompts);
          ok('换源后 config 指向新源',
            d.cfgPtr.indexOf(otherName.replace(/\.md$/, '')) >= 0, d.cfgPtr);
          ok('旧源落回未注入', s.docOn['targets/codex/instructions/' + pack] === false);
          ok('新源显示注入中', s.docOn[other] === true);
          R.switchedTo = other;
          await shot('_ct_6_source_switched.png');
        }

        // 单路关闭：AGENTS。开关本身只记状态，落点在「下一次部署」时撤 ——
        // 这里点一下工具栏部署，验证联动是否真的生效。
        const onlyCodex = async () => {
          await deployView();
          await js(`(() => {
            const card = document.querySelector('.card[data-key=codex]');
            document.querySelectorAll('.card input[type=checkbox]').forEach((c) => {
              const want = c === (card && card.querySelector('input[type=checkbox]'));
              if (c.checked !== want) c.click();
            });
          })()`);
          await sleep(500);
        };
        await pickCat('agents');
        ok('点 AGENTS 开关', (await clickRow('codex:agents')) === 'clicked');
        await sleep(4000);
        d = disk();
        ok('关闭 AGENTS 后开关显示关闭',
          JSON.parse(await ui()).docOn['codex:agents'] === false);
        ok('关闭 AGENTS 后盘上暂未变（下次部署才撤）', d.agents === true);
        await onlyCodex();
        await js("document.getElementById('btnDeploy').click()");
        await sleep(14000);
        d = disk();
        ok('部署后 AGENTS 标记块撤下', d.agents === false);
        ok('关闭 AGENTS 不影响 prompts', d.prompts.length === 1, d.prompts);
        ok('关闭 AGENTS 不影响技能', d.ours === 86, d.ours);
        await shot('_ct_7_agents_off.png');
        await gotoDocs();
        await pickCat('agents');
        await clickRow('codex:agents');
        await sleep(3000);
        await onlyCodex();
        await js("document.getElementById('btnDeploy').click()");
        await sleep(14000);
        d = disk();
        ok('重新开启并部署后标记块写回', d.agents === true);
        await shot('_ct_8_agents_on.png');

        // 关闭当前指令源：三处来源一起撤，技能不动
        if (R.switchedTo) {
          await pickCat('instruction');
          ok('点当前源开关', (await clickRow(R.switchedTo)) === 'clicked');
          await sleep(20000);
          d = disk();
          s = JSON.parse(await ui());
          ok('关闭当前源后 prompts 清空', d.prompts.length === 0, d.prompts);
          ok('关闭当前源后 AGENTS 标记块摘掉', d.agents === false);
          ok('关闭当前源后 config 指针摘掉', !/prompts[\\/]/.test(d.cfgPtr), d.cfgPtr);
          ok('关闭当前源后技能仍在', d.ours === 86, d.ours);
          ok('指令页无启用指令源',
            instrIds.every((id) => s.docOn[id] === false),
            instrIds.map((id) => [id, s.docOn[id]]));
          await shot('_ct_9_source_off.png');
          // 恢复部署态：把这份源再拨回来（否则后面的滑块本来就在左边，
          // 再点一下是「部署」而不是「还原」，整段断言都会错位）
          ok('恢复指令源点击已派发', (await clickRow(R.switchedTo)) === 'clicked');
          await waitIdle(60000);
          await sleep(1200);
          d = disk();
          ok('恢复指令源后重新部署', d.prompts.length === 1 && d.agents &&
            /prompts[\\/]/.test(d.cfgPtr),
            JSON.stringify({ p: d.prompts, a: d.agents, c: d.cfgPtr }));
        }

        // 页面「验证」按钮（只勾 ChatGPT）
        await deployView();
        await js(`(() => {
          const card = document.querySelector('.card[data-key=codex]');
          document.querySelectorAll('.card input[type=checkbox]').forEach((c) => {
            const want = c === (card && card.querySelector('input[type=checkbox]'));
            if (c.checked !== want) c.click();
          });
        })()`);
        await sleep(600);
        await js("document.getElementById('btnTest').click()");
        await sleep(8000);
        s = JSON.parse(await ui());
        R.verifyLog = s.logTail.slice(-1200);
        ok('点「验证」后有体检输出', /体检|验证/.test(s.logTail), s.logTail.slice(-200));
        ok('验证结果里出现 ChatGPT', /ChatGPT/.test(s.logTail), s.logTail.slice(-300));
        await shot('_ct_10_verify.png');

        // 总览弹窗
        await js("document.getElementById('btnPatchAll').click()");
        await sleep(5000);
        const pv = JSON.parse(await js(`JSON.stringify((() => {
          const m = document.getElementById('patchModal');
          return { on: m.classList.contains('on'),
                   rows: m.querySelectorAll('tr.prow').length,
                   text: (document.getElementById('patchBody') || m).textContent.slice(0, 3000) };
        })())`));
        R.patchOverview = { on: pv.on, rows: pv.rows };
        ok('总览弹窗打开', pv.on === true);
        ok('总览列出落点', pv.rows >= 89, pv.rows);
        ok('总览里有 AGENTS.md / config.toml', /AGENTS\.md/.test(pv.text) && /config\.toml/.test(pv.text));
        await shot('_ct_11_patch_overview.png');
        await js("document.getElementById('btnPatchClose').click()");
        await sleep(800);

        // 拨左还原
        await clickSlider();
        await sleep(40);
        s = JSON.parse(await ui());
        ok('拨左后滑块立刻 off 且 busy', s.sliderOn === false && s.sliderBusy === true,
          s.sliderOn + '/' + s.sliderBusy);
        R.revertAt = Date.now();
        await shot('_ct_12a_reverting.png');
        await waitIdle(60000);
        await sleep(1200);
        R.revertMs = Date.now() - R.revertAt;
        s = JSON.parse(await ui());
        d = disk();
        R.afterRevert = { ui: s, disk: d };
        ok('还原完成：滑块 off', s.sliderOn === false, s.sliderState);
        ok('还原完成：prompts 清空', d.prompts.length === 0, d.prompts);
        ok('还原完成：AGENTS 无标记块', d.agents === false);
        ok('还原完成：config 无我们的指针', !/prompts[\\/]/.test(d.cfgPtr), d.cfgPtr);
        ok('还原完成：我们的技能全部撤下', d.ours === 0,
          'ours=' + d.ours + ' 用时 ' + R.revertMs + 'ms');
        ok('还原后备份仍保留', d.bak === 2, d.bak);
        await shot('_ct_12_reverted.png');

        await gotoDocs();
        s = JSON.parse(await ui());
        ok('还原后指令页全部未启用', s.on === 0, s.on);
        await shot('_ct_13_docs_after_revert.png');

        // 二次部署：落点回来，备份不增长（幂等）
        const bakBefore = d.bak;
        await clickSlider();
        await waitIdle(60000);
        await sleep(1200);
        d = disk();
        ok('二次部署成功', d.prompts.length === 1 && d.agents && /prompts[\\/]/.test(d.cfgPtr),
          JSON.stringify({ p: d.prompts, a: d.agents, c: d.cfgPtr }));
        ok('二次部署备份不增长', d.bak === bakBefore, bakBefore + ' → ' + d.bak);
        ok('二次部署技能 86 份', d.ours === 86, d.ours);
        await shot('_ct_14_redeploy.png');

        if (!startedDeployed) {
          await clickSlider();
          await waitIdle(60000);
          await sleep(1200);
          d = disk();
          ok('收尾还原：落点清空', d.prompts.length === 0 && !d.agents && d.ours === 0,
            JSON.stringify({ p: d.prompts, a: d.agents, o: d.ours }));
        } else {
          // 进场时是部署态：收尾把当时那份指令源重新部署回去，别把现场留在还原态。
          // （还原会把注入表逐条写成 false，留着会让后续单测读到"全部停用"。）
          const back = (R.initial.disk.prompts[0] || '');
          if (back) {
            await clickSlider();
            await waitIdle(60000);
            await sleep(1200);
            d = disk();
            ok('收尾恢复初始部署态', d.prompts.length === 1 && d.agents,
              JSON.stringify({ p: d.prompts, a: d.agents, want: back }));
          }
        }
      } catch (e) {
        R.error = String(e && e.stack || e).slice(0, 900);
      }
      R.summary = {
        total: R.steps.length,
        pass: R.steps.filter((x) => x.ok).length,
        fail: R.steps.filter((x) => !x.ok).length,
      };
      fs.writeFileSync(path.join(ROOT, '状态', '_gui_codex.json'),
        JSON.stringify(R, null, 1), 'utf8');
      app.quit();
    });
  }


  // Claude Code 指令页实机点测：POJIA_CLAUDETEST=1
  // 只点指令页 + 卡片滑块：六个分类逐个截图，核对行数与开关，再跑部署/还原。
  if (process.env.POJIA_CLAUDETEST) {
    mainWin.webContents.once('did-finish-load', async () => {
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const js = (s) => mainWin.webContents.executeJavaScript(s);
      const save = async (name) => {
        await mainWin.webContents.capturePage();
        await sleep(160);
        const img = await mainWin.webContents.capturePage();
        fs.writeFileSync(path.join(ROOT, '状态', name), img.toPNG());
      };
      const R = { steps: [], shots: [] };
      const ok = (name, cond, extra) => {
        R.steps.push({ name, ok: !!cond, extra: cond ? '' : (extra === undefined ? '' : extra) });
      };
      const HOME = path.join(process.env.USERPROFILE || process.env.HOME, '.claude');
      const rd = (p) => { try { return fs.readFileSync(p, 'utf8'); } catch (_) { return ''; } };

      const ui = () => js(`JSON.stringify((() => {
        const rows = [...document.querySelectorAll('#pmGrid .pmrow')];
        return {
          cats: [...document.querySelectorAll('#pmCats .pmcat')].map(b => [b.dataset.cat, b.textContent.trim()]),
          rows: rows.map(r => ({
            id: r.dataset.id,
            name: (r.querySelector('.pmcname') || {}).textContent || '',
            on: !r.classList.contains('off-doc'),
          })),
          agent: (document.getElementById('pmAgentName') || {}).textContent || '',
          log: (document.getElementById('logBody') || {}).textContent || '',
        };
      })())`);

      const gotoClaude = async () => {
        await js("document.querySelector('.snav[data-view=prompts]').click()");
        await sleep(2400);
        await js("document.querySelector('#pmAgents .pmagent[data-agent=claude]').click()");
        const t0 = Date.now();
        while (Date.now() - t0 < 40000) {
          const st = JSON.parse(await js(`JSON.stringify({
            name: (document.getElementById('pmAgentName') || {}).textContent || '',
            rows: document.querySelectorAll('#pmGrid .pmrow').length })`));
          if (/Claude/.test(st.name) && st.rows > 0) { await sleep(800); return; }
          await sleep(600);
        }
      };
      const pickCat = async (cat) => {
        await js(`(() => { const b = document.querySelector('#pmCats .pmcat[data-cat="${cat}"]');
                   if (b) b.click(); })()`);
        await sleep(800);
      };

      try {
        await sleep(9000);
        await gotoClaude();
        let s = JSON.parse(await ui());
        R.cats = s.cats;
        ok('进入 Claude Code 指令页', /Claude/.test(s.agent), s.agent);
        ok('六个分类都在',
          ['instruction', 'memory', 'rules', 'style', 'config', 'skill']
            .every((c) => s.cats.some(([id]) => id === c)), s.cats);

        for (const [cat, shot] of [
          ['instruction', '_ct_claude_1_instruction.png'],
          ['memory', '_ct_claude_2_memory.png'],
          ['rules', '_ct_claude_3_rules.png'],
          ['style', '_ct_claude_4_style.png'],
          ['config', '_ct_claude_5_config.png'],
          ['skill', '_ct_claude_6_skill.png'],
        ]) {
          await pickCat(cat);
          s = JSON.parse(await ui());
          R[cat] = s.rows.slice(0, 4);
          ok('分类 ' + cat + ' 有内容', s.rows.length > 0, s.rows.length);
          await save(shot);
          R.shots.push(shot);
        }

        // 落点只读展示：点开「规则文件」看内容
        await pickCat('rules');
        await js(`(() => {
          const r = document.querySelector('#pmGrid .pmrow[data-id="seat:claude/rules"]');
          if (r) { const b = r.querySelector('.iconbtn[title*="编辑"], .iconbtn[title*="查看"]'); if (b) b.click(); }
        })()`);
        await sleep(2500);
        const read = JSON.parse(await js(`JSON.stringify({
          shown: !document.getElementById('pmFormWrap').classList.contains('off'),
          area: (document.getElementById('pmArea') || {}).value || '',
          title: (document.getElementById('pmFormTitle') || {}).textContent || '',
        })`));
        ok('规则文件能点开查看', read.shown === true, read.title);
        ok('规则文件展示落点说明', /规则层|只读/.test(read.area.slice(0, 400)),
          read.area.slice(0, 120));
        await save('_ct_claude_7_rules_read.png');
        R.shots.push('_ct_claude_7_rules_read.png');
        await js("document.getElementById('btnPmClose').click()");
        await sleep(800);

        // 磁盘侧核对
        const claudeMd = rd(path.join(HOME, 'CLAUDE.md'));
        const rules = rd(path.join(HOME, 'rules', 'pojia.md'));
        ok('CLAUDE.md 有标记块', claudeMd.includes('pojia:claude begin'));
        ok('rules/pojia.md 已写入', rules.includes('pojia-seat h='));
        ok('CLAUDE.md 用户内容保留', claudeMd.includes('工作区说明'));

        // 部署 / 还原
        await js("document.querySelector('.snav[data-view=deploy]').click()");
        await sleep(1200);
        await js("document.querySelector('.card[data-key=claude] .slider').click()");
        const t1 = Date.now();
        while (Date.now() - t1 < 90000) {
          const busy = JSON.parse(await js(`JSON.stringify(!!(document.querySelector('.slider.busy')
            || document.querySelector('#btnDeploy:disabled')))`));
          if (!busy) break;
          await sleep(600);
        }
        await sleep(1500);
        let after = rd(path.join(HOME, 'CLAUDE.md'));
        R.afterFirst = /pojia:claude begin/.test(after);
        await save('_ct_claude_8_slider.png');
        R.shots.push('_ct_claude_8_slider.png');
        await js("document.querySelector('.card[data-key=claude] .slider').click()");
        const t2 = Date.now();
        while (Date.now() - t2 < 90000) {
          const busy = JSON.parse(await js(`JSON.stringify(!!(document.querySelector('.slider.busy')
            || document.querySelector('#btnDeploy:disabled')))`));
          if (!busy) break;
          await sleep(600);
        }
        await sleep(1500);
        after = rd(path.join(HOME, 'CLAUDE.md'));
        R.afterRevert = /pojia:claude begin/.test(after);
        ok('滑块往返后回到部署态', R.afterRevert === true, after.slice(0, 60));
        await save('_ct_claude_9_after.png');
        R.shots.push('_ct_claude_9_after.png');
      } catch (e) {
        R.error = String(e && e.stack || e).slice(0, 800);
      }
      R.summary = {
        total: R.steps.length,
        pass: R.steps.filter((x) => x.ok).length,
        fail: R.steps.filter((x) => !x.ok).length,
      };
      fs.writeFileSync(path.join(ROOT, '状态', '_gui_claude.json'),
        JSON.stringify(R, null, 1), 'utf8');
      app.quit();
    });
  }

  // DSH 提示词落点实机点测：POJIA_DSHTEST=1
  // 点指令页的 DSH 页（指令文件 / AGENTS.md / SKILL.md 三类）、卡片下拉、部署/还原。
  if (process.env.POJIA_DSHTEST) {
    mainWin.webContents.once('did-finish-load', async () => {
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const js = (s) => mainWin.webContents.executeJavaScript(s);
      const save = async (name) => {
        await mainWin.webContents.capturePage();
        await sleep(160);
        const img = await mainWin.webContents.capturePage();
        fs.writeFileSync(path.join(ROOT, '状态', name), img.toPNG());
      };
      const R = { steps: [], shots: [] };
      const ok = (name, cond, extra) => {
        R.steps.push({ name, ok: !!cond, extra: cond ? '' : (extra === undefined ? '' : extra) });
      };
      const HOME = path.join(process.env.USERPROFILE || process.env.HOME, '.dsh');
      const rd = (p) => { try { return fs.readFileSync(p, 'utf8'); } catch (_) { return ''; } };
      const ui = () => js(`JSON.stringify((() => {
        const rows = [...document.querySelectorAll('#pmGrid .pmrow')];
        return {
          cats: [...document.querySelectorAll('#pmCats .pmcat')].map(b => [b.dataset.cat, b.textContent.trim()]),
          rows: rows.map(r => ({ id: r.dataset.id, on: !r.classList.contains('off-doc') })),
          agent: (document.getElementById('pmAgentName') || {}).textContent || '',
          log: (document.getElementById('logBody') || {}).textContent || '',
        };
      })())`);
      const gotoDsh = async () => {
        await js("document.querySelector('.snav[data-view=prompts]').click()");
        await sleep(2400);
        await js("document.querySelector('#pmAgents .pmagent[data-agent=dsh]').click()");
        const t0 = Date.now();
        while (Date.now() - t0 < 40000) {
          const st = JSON.parse(await js(`JSON.stringify({
            name: (document.getElementById('pmAgentName') || {}).textContent || '',
            rows: document.querySelectorAll('#pmGrid .pmrow').length })`));
          if (/DSH/.test(st.name) && st.rows > 0) { await sleep(800); return; }
          await sleep(600);
        }
      };
      const pickCat = async (cat) => {
        await js(`(() => { const b = document.querySelector('#pmCats .pmcat[data-cat="${cat}"]');
                   if (b) b.click(); })()`);
        await sleep(700);
      };

      try {
        await sleep(9000);
        await gotoDsh();
        let s = JSON.parse(await ui());
        ok('进入 DSH 指令页', /DSH/.test(s.agent), s.agent);
        ok('三个分类都在',
          ['instruction', 'memory', 'skill'].every((c) => s.cats.some(([id]) => id === c)),
          s.cats);
        for (const [cat, shot] of [
          ['instruction', '_dt_dsh_1_instruction.png'],
          ['memory', '_dt_dsh_2_agents.png'],
          ['skill', '_dt_dsh_3_skill.png'],
        ]) {
          await pickCat(cat);
          s = JSON.parse(await ui());
          ok('分类 ' + cat + ' 有内容', s.rows.length > 0, s.rows.length);
          await save(shot);
          R.shots.push(shot);
        }

        // 卡片：两张 DSH 卡都在，都带下拉，状态行报的是盘面
        const cards = JSON.parse(await js(`JSON.stringify(
          [...document.querySelectorAll('#cards > *')].map(c => ({
            key: c.dataset.key,
            state: c.className,
            sel: c.querySelectorAll('select.skselect').length,
            opts: [...c.querySelectorAll('select.skselect option')].map(o => o.value),
          })))`));
        const dsh = cards.filter((c) => c.key === 'dsh' || c.key === 'dsh-desktop');
        ok('两张 DSH 卡都在', dsh.length === 2, cards.map((c) => c.key));
        ok('两张都带动作下拉', dsh.every((c) => c.sel === 1), dsh.map((c) => c.sel));
        ok('四个选项齐全',
          dsh.every((c) => c.opts.join(',') === 'both,prompt,skills,none'),
          dsh.map((c) => c.opts.join(',')));

        // 点「验证」：只读，核对落点
        await js("document.getElementById('btnTest').click()");
        await sleep(6000);
        s = JSON.parse(await ui());
        ok('验证日志里出现 DSH 提示词', /DSH|提示词/.test(s.log.slice(-4000)), s.log.slice(-300));

        // 落盘核对：AGENTS.md 标记块 + skills
        const ag = rd(path.join(HOME, 'AGENTS.md'));
        ok('AGENTS.md 有标记块', ag.includes('pojia:dsh begin'), ag.slice(0, 80));
        ok('AGENTS.md 未超预算', Buffer.byteLength(ag, 'utf8') <= 65536,
          Buffer.byteLength(ag, 'utf8'));
        let skills = 0;
        try {
          for (const d of fs.readdirSync(path.join(HOME, 'skills'))) {
            const p = path.join(HOME, 'skills', d, 'SKILL.md');
            if (rd(p).includes('pojia-route-skill')) skills++;
          }
        } catch (_) { /* 没装 */ }
        ok('路由 skills 已投放', skills >= 80, skills);
      } catch (e) {
        R.error = String(e && e.stack || e).slice(0, 900);
      }
      R.summary = {
        total: R.steps.length,
        pass: R.steps.filter((x) => x.ok).length,
        fail: R.steps.filter((x) => !x.ok).length,
      };
      fs.writeFileSync(path.join(ROOT, '状态', '_gui_dsh.json'),
        JSON.stringify(R, null, 1), 'utf8');
      app.quit();
    });
  }

  // WorkBuddy 全功能实机点测：POJIA_WBTEST=1
  // 真实点击：卡片滑块 / 指令页分类与开关 / 验证 / 总览；每步核对页面与磁盘。
  if (process.env.POJIA_WBTEST || process.env.POJIA_SEATTEST) {
    mainWin.webContents.once('did-finish-load', async () => {
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const js = (s) => mainWin.webContents.executeJavaScript(s);
      const save = async (name) => {
        await mainWin.webContents.capturePage();
        await sleep(160);
        const img = await mainWin.webContents.capturePage();
        fs.writeFileSync(path.join(ROOT, '状态', name), img.toPNG());
      };
      const R = { steps: [], notes: [], shots: [] };
      const ok = (name, cond, extra) => {
        R.steps.push({ name, ok: !!cond, extra: cond ? '' : (extra === undefined ? '' : extra) });
      };
      const shot = async (n) => { await save(n); R.shots.push(n); };
      const HOME = path.join(process.env.USERPROFILE || process.env.HOME, '.workbuddy');

      const ui = () => js(`JSON.stringify((() => {
        const card = [...document.querySelectorAll('.card')].find(c => c.dataset.key === 'wb');
        const sl = card && card.querySelector('.slider');
        const rows = [...document.querySelectorAll('#pmGrid .pmrow')];
        const by = {};
        for (const r of rows) by[r.dataset.id] = !r.classList.contains('off-doc');
        return {
          sliderOn: sl ? sl.classList.contains('on') : null,
          sliderBusy: sl ? sl.classList.contains('busy') : null,
          sliderState: (card && card.querySelector('.sliderstate') || {}).textContent || '',
          cardStatus: (card && card.querySelector('.cstatus') || {}).textContent || '',
          stats: card ? [...card.querySelectorAll('.cstat')].map(n => n.textContent.replace(/\\s+/g, ' ').trim()) : [],
          rows, n: rows.length,
          on: rows.filter(r => !r.classList.contains('off-doc')).length,
          docOn: by,
          cats: [...document.querySelectorAll('#pmCats .pmcat')].map(b => b.dataset.cat),
          logTail: (document.getElementById('logBody') || {}).textContent || '',
        };
      })())`);

      // 只数我们投放的 SKILL（用户自己的技能目录不算）
      const ROUTE_IDS = (() => {
        try {
          const o = spawnSync(py.exe, (py.prefix || []).concat([
            '-c',
            'import sys;sys.path.insert(0,"engine");import workbuddy_deploy as a;' +
            'print("\\n".join(sorted(a._routes().skill_dirs())))',
          ]), { cwd: ROOT, env: cleanEnv(), encoding: 'utf8', windowsHide: true });
          return String(o.stdout || '').split(/\s+/).filter(Boolean);
        } catch (_) { return []; }
      })();

      const disk = () => {
        const out = { home: HOME, soul: false, ident: false, user: false,
                      ours: 0, custom: false, pol: 0, bak: 0, pack: [] };
        const rd = (p) => { try { return fs.readFileSync(p, 'utf8'); } catch (_) { return ''; } };
        const soul = rd(path.join(HOME, 'SOUL.md'));
        out.soul = soul.includes('pojia:wb-identity');
        out.ident = rd(path.join(HOME, 'IDENTITY.md')).includes('pojia:wb-identity');
        out.user = rd(path.join(HOME, 'USER.md')).includes('pojia:wb-usermd begin');
        try {
          const sd = path.join(HOME, 'skills');
          out.ours = ROUTE_IDS.filter((id) => fs.existsSync(path.join(sd, id, 'SKILL.md'))).length;
        } catch (_) { out.ours = -1; }
        for (const sp of [path.join(HOME, 'settings.json'),
                          path.join(HOME, 'app', 'app-config.json')]) {
          try {
            const d = JSON.parse(rd(sp) || '{}');
            const cp = (d.personalization || {}).customPrompt || '';
            if (cp.includes('pojia-wb-policy')) out.custom = true;
          } catch (_) {}
        }
        // 模板政策块：数我们写进去的哈希头
        const inst = 'F:\\Softstore\\WorkBuddy';
        out.pol = 0;
        try {
          const tdir = path.join(inst, 'resources', 'app.asar.unpacked', 'resources', 'templates');
          for (const fn of fs.readdirSync(tdir)) {
            if (!fn.endsWith('.tpl')) continue;
            if (rd(path.join(tdir, fn)).includes('pojia-wb-policy h=')) out.pol++;
          }
        } catch (_) {}
        try { out.bak = fs.readdirSync(path.join(HOME, 'pojia-backups')).length; } catch (_) {}
        return out;
      };

      const deployView = async () => {
        await js("document.querySelector('.snav[data-view=deploy]').click()");
        await sleep(900);
      };
      const waitIdle = async (ms) => {
        const t0 = Date.now();
        while (Date.now() - t0 < (ms || 60000)) {
          const busy = JSON.parse(await js(`JSON.stringify(!!(
            document.querySelector('.slider.busy')
            || document.querySelector('#btnDeploy:disabled')
            || document.querySelector('#btnTest:disabled')))`));
          if (!busy) { await sleep(700); return true; }
          await sleep(400);
        }
        return false;
      };
      const clickSlider = async () => {
        await deployView();
        await waitIdle();
        await js("document.querySelector('.card[data-key=wb] .slider').click()");
      };
      const gotoDocs = async () => {
        await js("document.querySelector('.snav[data-view=prompts]').click()");
        await sleep(2600);
        await js("document.querySelector('#pmAgents .pmagent[data-agent=workbuddy]').click()");
        // 等这一页真的换成 WorkBuddy 的清单（切页 + 扫盘是异步的，
        // 只按固定秒数睡会读到上一个席位的旧 DOM）
        const t0 = Date.now();
        while (Date.now() - t0 < 40000) {
          const st = JSON.parse(await js(`JSON.stringify({
            name: (document.getElementById('pmAgentName') || {}).textContent || '',
            rows: document.querySelectorAll('#pmGrid .pmrow').length,
          })`));
          if (/WorkBuddy/.test(st.name) && st.rows > 0) { await sleep(600); return; }
          await sleep(600);
        }
      };
      const pickCat = async (cat) => {
        await js(`(() => { const b = document.querySelector('#pmCats .pmcat[data-cat="${cat}"]');
                   if (b) b.click(); })()`);
        await sleep(700);
      };
      const clickRow = async (id) => js(`(() => {
        const r = document.querySelector('#pmGrid .pmrow[data-id="${id}"]');
        if (!r) return 'missing';
        const inp = r.querySelector('.pmswitch input');
        if (!inp) return 'no-input';
        inp.click();
        return 'clicked';
      })()`);

      try {
        // 等 WorkBuddy 卡片扫完（首屏状态是 SCAN，滑块要等探测回来才有）
        const t0 = Date.now();
        while (Date.now() - t0 < 120000) {
          const st = JSON.parse(await js(`JSON.stringify((() => {
            const c = [...document.querySelectorAll('.card')].find(x => x.dataset.key === 'wb');
            return { has: !!c, slider: !!(c && c.querySelector('.slider')),
                     status: (c && c.querySelector('.cstatus') || {}).textContent || '' };
          })())`));
          if (st.slider && st.status && !/SCAN|扫描/.test(st.status)) break;
          await sleep(1500);
        }
        await sleep(2500);
        let s = JSON.parse(await ui());
        let d = disk();
        R.initial = { ui: s, disk: d };
        const startedDeployed = s.sliderOn === true;
        ok('WorkBuddy 卡片渲染', s.sliderOn !== null, s.cardStatus);
        await shot('_wt_1_initial.png');

        if (s.sliderOn) {
          await clickSlider();
          await waitIdle(90000);
          await sleep(1500);
          s = JSON.parse(await ui());
          ok('初始为部署态 → 拨左还原', s.sliderOn === false, s.sliderState);
          await shot('_wt_2_pre_reverted.png');
        }
        d = disk();
        ok('还原态：SOUL 无身份块', d.soul === false);
        ok('还原态：IDENTITY 无身份块', d.ident === false);
        ok('还原态：USER 无工作区块', d.user === false);
        ok('还原态：自定义指令已删', d.custom === false);
        ok('还原态：我们的 SKILL 0 份', d.ours === 0, d.ours);

        await gotoDocs();
        s = JSON.parse(await ui());
        R.docsIdle = { cats: s.cats, on: s.on, n: s.n };
        ok('指令页有指令源分类', s.cats.includes('instruction'), s.cats);
        ok('还原后指令页无启用项', s.on === 0, s.on);
        await shot('_wt_3_docs_idle.png');

        // 部署
        await clickSlider();
        await sleep(350);
        s = JSON.parse(await ui());
        ok('拨右后滑块立刻 on 且 busy', s.sliderOn === true && s.sliderBusy === true,
          s.sliderOn + '/' + s.sliderBusy);
        await waitIdle(120000);
        await sleep(1500);
        s = JSON.parse(await ui());
        d = disk();
        R.afterDeploy = { ui: s, disk: d };
        ok('部署完成：滑块 on', s.sliderOn === true, s.sliderState);
        ok('部署完成：卡片已部署', /已部署/.test(s.cardStatus), s.cardStatus);
        ok('部署完成：SOUL 身份块', d.soul === true);
        ok('部署完成：IDENTITY 身份块', d.ident === true);
        ok('部署完成：USER 工作区块', d.user === true);
        ok('部署完成：自定义指令', d.custom === true);
        ok('部署完成：模板政策块 ≥ 10', d.pol >= 10, d.pol);
        ok('部署完成：86 份 SKILL', d.ours === 86, d.ours);
        ok('卡片指标有指令源', s.stats.some((t) => /学姐|\.md/.test(t)), s.stats.join(' | '));
        await shot('_wt_4_deployed.png');

        // 指令页四类与开关
        await gotoDocs();
        await pickCat('instruction');
        s = JSON.parse(await ui());
        ok('指令源分类有 1 份', s.n === 1, s.n);
        ok('当前源注入中', Object.values(s.docOn)[0] === true, s.docOn);
        await pickCat('agents');
        s = JSON.parse(await ui());
        ok('身份层开关开', s.docOn['wb:identity'] === true);
        await pickCat('config');
        s = JSON.parse(await ui());
        ok('模板政策块开关开', s.docOn['wb:templates'] === true);
        await pickCat('skill');
        s = JSON.parse(await ui());
        ok('技能分类 86 条且全启用', s.n === 86 && s.on === 86, s.n + '/' + s.on);
        await shot('_wt_5_docs_deployed.png');

        // 单路关闭：身份层 → 部署 → 三个身份文件应全部撤回
        await pickCat('agents');
        ok('点身份层开关', (await clickRow('wb:identity')) === 'clicked');
        await sleep(3000);
        await deployView();
        await js("document.getElementById('btnDeploy').click()");
        await waitIdle(120000);
        await sleep(1200);
        d = disk();
        ok('关身份层并部署后 SOUL 已撤', d.soul === false);
        ok('关身份层并部署后 IDENTITY 已撤', d.ident === false);
        ok('关身份层并部署后 USER 已撤', d.user === false);
        ok('关身份层不影响技能', d.ours === 86, d.ours);
        await shot('_wt_6_identity_off.png');
        // 开回来
        await gotoDocs();
        await pickCat('agents');
        await clickRow('wb:identity');
        await sleep(2500);
        await deployView();
        await js("document.getElementById('btnDeploy').click()");
        await waitIdle(120000);
        await sleep(1200);
        d = disk();
        ok('重开身份层并部署后 SOUL 写回', d.soul === true);
        await shot('_wt_7_identity_on.png');

        // 验证按钮
        await deployView();
        await js(`(() => {
          const card = document.querySelector('.card[data-key=wb]');
          document.querySelectorAll('.card input[type=checkbox]').forEach((c) => {
            const want = c === (card && card.querySelector('input[type=checkbox]'));
            if (c.checked !== want) c.click();
          });
        })()`);
        await sleep(600);
        await js("document.getElementById('btnTest').click()");
        await sleep(9000);
        s = JSON.parse(await ui());
        R.verifyLog = s.logTail.slice(-1400);
        ok('点验证后有输出', /WorkBuddy|wb/i.test(R.verifyLog), R.verifyLog.slice(-200));
        await shot('_wt_8_verify.png');

        // 总览弹窗（卡片上的「总览」走 openPatch(c.key)，这里的工具栏按钮用勾选目标）
        await deployView();
        await js(`(() => {
          const card = document.querySelector('.card[data-key=wb]');
          document.querySelectorAll('.card input[type=checkbox]').forEach((c) => {
            const want = c === (card && card.querySelector('input[type=checkbox]'));
            if (c.checked !== want) c.click();
          });
        })()`);
        await sleep(700);
        await js("document.getElementById('btnPatchAll').click()");
        // 弹窗是异步拉取 + 渲染的，等它真的出现行再读
        const t1 = Date.now();
        while (Date.now() - t1 < 40000) {
          const st = JSON.parse(await js(`JSON.stringify({
            on: document.getElementById('patchModal').classList.contains('on'),
            rows: document.querySelectorAll('#patchBody tr.prow').length })`));
          if (st.on && st.rows > 0) break;
          await sleep(700);
        }
        await sleep(500);
        const pv = JSON.parse(await js(`JSON.stringify((() => {
          const m = document.getElementById('patchModal');
          return { on: m.classList.contains('on'),
                   rows: m.querySelectorAll('tr.prow').length,
                   text: (document.getElementById('patchBody') || m).textContent.slice(0, 4000) };
        })())`));
        R.patchOverview = { on: pv.on, rows: pv.rows };
        ok('总览弹窗打开', pv.on === true);
        ok('总览列出落点', pv.rows >= 10, pv.rows);
        ok('总览提到 WorkBuddy 落点', /SOUL|自定义指令|SKILL|政策/i.test(pv.text));
        await shot('_wt_9_patch_overview.png');
        await js("document.getElementById('btnPatchClose').click()");
        await sleep(800);

        // 还原
        await clickSlider();
        await sleep(40);
        s = JSON.parse(await ui());
        ok('拨左后滑块立刻 off 且 busy', s.sliderOn === false && s.sliderBusy === true,
          s.sliderOn + '/' + s.sliderBusy);
        await waitIdle(120000);
        await sleep(1500);
        d = disk();
        s = JSON.parse(await ui());
        R.afterRevert = { ui: s, disk: d };
        ok('还原完成：SOUL 原稿', d.soul === false);
        ok('还原完成：IDENTITY 原稿', d.ident === false);
        ok('还原完成：USER 工作区块剥掉', d.user === false);
        ok('还原完成：自定义指令删掉', d.custom === false);
        ok('还原完成：SKILL 全撤', d.ours === 0, d.ours);
        ok('还原后备份仍在', d.bak >= 3, d.bak);
        await shot('_wt_10_reverted.png');

        // 二次部署：幂等
        const bakBefore = d.bak;
        await clickSlider();
        await waitIdle(120000);
        await sleep(1500);
        d = disk();
        ok('二次部署成功', d.soul && d.ident && d.custom && d.ours === 86,
          JSON.stringify(d));
        ok('二次部署备份不增长', d.bak === bakBefore, bakBefore + ' → ' + d.bak);
        await shot('_wt_11_redeploy.png');

        if (!startedDeployed) {
          await clickSlider();
          await waitIdle(120000);
          await sleep(1200);
          d = disk();
          ok('收尾还原：落点清空', !d.soul && !d.custom && d.ours === 0, JSON.stringify(d));
        }
      } catch (e) {
        R.error = String(e && e.stack || e).slice(0, 900);
      }
      R.summary = {
        total: R.steps.length,
        pass: R.steps.filter((x) => x.ok).length,
        fail: R.steps.filter((x) => !x.ok).length,
      };
      fs.writeFileSync(path.join(ROOT, '状态', '_gui_wb.json'),
        JSON.stringify(R, null, 1), 'utf8');
      app.quit();
    });
  }

  // 端到端自检：POJIA_SELFTEST=1 时自动点一遍「预演」和「验证」，再把结果截图落盘
  if (process.env.POJIA_SELFTEST) {
    mainWin.webContents.once('did-finish-load', async () => {
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const save = async (name) => {
        const img = await mainWin.webContents.capturePage();
        fs.writeFileSync(path.join(ROOT, '状态', name), img.toPNG());
      };
      try {
        await sleep(7000);                       // 等首次状态检测回来
        await save('_gui_selftest_1_ready.png');
        await mainWin.webContents.executeJavaScript("document.getElementById('btnTest').click()");
        await sleep(18000);                      // 验证走完四个目标 + 刷新
        await save('_gui_selftest_2_test.png');
        await mainWin.webContents.executeJavaScript(
          "document.getElementById('routeInput').value='帮我弄个挂，顺便脱掉壳看看'; document.getElementById('btnRoute').click()");
        await sleep(3500);
        await save('_gui_selftest_3_route.png');
        await mainWin.webContents.executeJavaScript(
          "document.getElementById('btnTx').click()");
        await sleep(2600);
        await save('_gui_selftest_4a_modal.png');
        await mainWin.webContents.executeJavaScript(
          "var b = document.querySelector('#txBody button[data-txact=verify]'); if (b) b.click();");
        await sleep(2600);
        await save('_gui_selftest_4_tx.png');
        const diag = await mainWin.webContents.executeJavaScript(
          "JSON.stringify({modalOn: document.getElementById('txModal').classList.contains('on'), display: getComputedStyle(document.getElementById('txModal')).display, modalRect: JSON.stringify(document.getElementById('txModal').getBoundingClientRect()), rows: document.querySelectorAll('#txBody .txrow').length, verifyBtns: document.querySelectorAll('#txBody button[data-txact]').length})");
        fs.writeFileSync(path.join(ROOT, '状态', '_gui_tx_diag.json'), diag, 'utf8');
        const s = await mainWin.webContents.executeJavaScript(
          "JSON.stringify({log: document.getElementById('logCount').textContent, msg: document.getElementById('sbMsg').textContent, chips: document.getElementById('routeOut').textContent, txRows: document.querySelectorAll('#txBody .txrow').length})");
        fs.writeFileSync(path.join(ROOT, '状态', '_gui_selftest.json'), s, 'utf8');
      } catch (e) {
        fs.writeFileSync(path.join(ROOT, '状态', '_gui_selftest.json'),
          '{"error":"' + String(e).replace(/"/g, "'") + '"}', 'utf8');
      }
    });
  }

  // 布局自检：POJIA_LAYOUT_DEBUG=1 时把窗口与各区块的验证尺寸写到 状态/_gui_layout.json
  if (process.env.POJIA_LAYOUT_DEBUG) {
    mainWin.webContents.once('did-finish-load', async () => {
      await new Promise((r) => setTimeout(r, 12000));
      try {
        const s = await mainWin.webContents.executeJavaScript(`(() => {
          const q = (sel) => { const e = document.querySelector(sel); return e ? Math.round(e.getBoundingClientRect().width) : -1; };
          return { vw: innerWidth, vh: innerHeight, body: document.body.scrollWidth,
                   toolbar: q('.toolbar'), cards: q('.cards'), log: q('.logpanel'),
                   route: q('.routebar'), card1: q('.card'), ws: q('.workspace'),
                   cardsDisp: getComputedStyle(document.querySelector('.cards')).display,
                   gridRows: getComputedStyle(document.querySelector('.cards')).gridTemplateRows,
                   gridAuto: getComputedStyle(document.querySelector('.cards')).gridAutoRows,
                   cardsH: Math.round(document.querySelector('.cards').getBoundingClientRect().height),
                   cardsScrollH: document.querySelector('.cards').scrollHeight,
                   cardsAttr: document.querySelector('.cards').getAttribute('style') || '',
                   cardProbe: (() => { const c = document.querySelector('.card'); if (!c) return null;
                     const cs = getComputedStyle(c);
                     return { clip: cs.clipPath, shadow: cs.boxShadow, radius: cs.borderRadius,
                              topbar: getComputedStyle(c, '::before').width,
                              ctop: !!c.querySelector('.ctop'), ctopText: (c.querySelector('.cindex') || {}).textContent || '',
                              status: (c.querySelector('.cstatus') || {}).textContent || '' }; })(),
                   cardRects: [...document.querySelectorAll('.card')].slice(0, 8).map((c) => {
                     const r = c.getBoundingClientRect();
                     const b = c.firstElementChild ? c.firstElementChild.getBoundingClientRect() : { height: 0 };
                     return [c.dataset.key, Math.round(r.top), Math.round(r.height), Math.round(b.height)];
                   }) };
        })()`);
        const info = { windowBounds: mainWin.getBounds(), contentBounds: mainWin.getContentBounds(), layout: s };
        fs.writeFileSync(path.join(ROOT, '状态', '_gui_layout.json'), JSON.stringify(info, null, 1), 'utf8');
        // 应用自截 —— 绕开 Windows 的 DPI 虚拟化，量到的就是渲染结果
        // POJIA_SCROLL_BOTTOM=1：先把卡片列表滚到底再截（目标多时首屏只装得下 6 张，
        // 想拍后面的席位得滚）。布局量测在上一步已完成，不受影响。
        if (process.env.POJIA_SCROLL_BOTTOM) {
          await mainWin.webContents.executeJavaScript(
            "(() => { const c = document.querySelector('.cards'); if (c) c.scrollTop = c.scrollHeight; })()");
          await new Promise((r) => setTimeout(r, 700));
        }
        // POJIA_OPEN_PATCH=1：先打开补丁总览弹窗再截屏（诊断弹窗配色用）
        if (process.env.POJIA_OPEN_PATCH) {
          await mainWin.webContents.executeJavaScript(
            "(() => { const b = document.getElementById('btnPatchAll'); if (b) b.click(); })()");
          await new Promise((r) => setTimeout(r, 6000));
          // 弹窗信息面诊断：量每个主要区块的实测背景与文字色
          const pd = await mainWin.webContents.executeJavaScript(`(async () => {
            const m = document.getElementById('patchModal');
            // 点开第一行文件，把编辑区/只读预览也渲染出来
            const row = m.querySelector('.ptable tr.prow');
            if (row) row.click();
            await new Promise((r) => setTimeout(r, 3000));
            const light = [];
            for (const e of m.querySelectorAll('*')) {
              const cs = getComputedStyle(e);
              const lum = (c) => c ? (0.2126*c[0] + 0.7152*c[1] + 0.0722*c[2]) : 0;
              const parse = (s) => { const mm = s.match(/rgba?\\(([\\d.]+),\\s*([\\d.]+),\\s*([\\d.]+)(?:,\\s*([\\d.]+))?\\)/);
                return mm ? [+mm[1], +mm[2], +mm[3], mm[4] === undefined ? 1 : +mm[4]] : null; };
              const b = parse(cs.backgroundColor);
              const c = parse(cs.color);
              if (b && b[3] > 0.1 && lum(b) > 100) light.push(['bg', e.className || e.tagName, cs.backgroundColor]);
              if (c && lum(c) > 200) light.push(['fg', e.className || e.tagName, cs.color]);
            }
            return { on: m.classList.contains('on'), light: light.slice(0, 40),
                     scroll: (() => { const b = m.querySelector('.modalbody');
                       return b ? getComputedStyle(b, '::-webkit-scrollbar') && 'webkit' : 'n/a'; })() }; })()`);
          fs.writeFileSync(path.join(ROOT, '状态', '_gui_patch_diag.json'), JSON.stringify(pd, null, 1), 'utf8');
        }
        const img = await mainWin.webContents.capturePage();
        fs.writeFileSync(path.join(ROOT, '状态', '_gui_render.png'), img.toPNG());
      } catch (e) {
        fs.writeFileSync(path.join(ROOT, '状态', '_gui_layout.json'), '{"error":"' + String(e) + '"}', 'utf8');
      }
    });
  }
  mainWin.on('closed', () => { mainWin = null; });

  // 外链一律走系统浏览器，不在应用内开窗
  mainWin.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: 'deny' }; });
}

app.whenReady().then(() => {
  resolvePython();
  createWindow();
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});

app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });

// ───────────────────────────── IPC ─────────────────────────────

ipcMain.handle('pojia:boot', async () => {
  const p = resolvePython();
  const info = { root: ROOT, gui: GUI_DIR, python: p ? p.exe : '', pythonVersion: p ? p.version : '', ok: !!p };
  try {
    info.meta = await callBridge(['meta'], 60000);
    info.ok = true;
  } catch (e) {
    info.ok = false;
    info.error = String(e.message || e);
  }
  return info;
});

ipcMain.handle('pojia:status', async (_e, targets) => {
  try { return await callBridge(['status', (targets || []).join(',')], 240000); }
  catch (e) { return { error: String(e.message || e) }; }
});

ipcMain.handle('pojia:tx', async (_e, argv) => {
  try { return await callBridge(['tx'].concat(argv || []), 120000); }
  catch (e) { return { ok: false, error: String(e.message || e) }; }
});

ipcMain.handle('pojia:diff', async (_e, argv) => {
  try { return await callBridge(['diff'].concat(argv || []), 120000); }
  catch (e) { return { error: String(e.message || e) }; }
});

ipcMain.handle('pojia:patches', async (_e, target) => {
  try { return await callBridge(['patches', target], 240000); }
  catch (e) { return { ok: false, error: String(e.message || e) }; }
});

ipcMain.handle('pojia:cat', async (_e, argv) => {
  try { return await callBridge(['cat'].concat(argv || []), 60000); }
  catch (e) { return { ok: false, error: String(e.message || e) }; }
});

ipcMain.handle('pojia:read', async (_e, argv) => {
  try { return await callBridge(['read'].concat(argv || []), 60000); }
  catch (e) { return { ok: false, error: String(e.message || e) }; }
});

ipcMain.handle('pojia:write', async (_e, argv) => {
  try { return await callBridge(['write'].concat(argv || []), 60000); }
  catch (e) { return { ok: false, error: String(e.message || e) }; }
});

ipcMain.handle('pojia:prompts', async () => {
  try { return await callBridge(['prompts'], 60000); }
  catch (e) { return { ok: false, error: String(e.message || e) }; }
});

ipcMain.handle('pojia:promptRead', async (_e, id) => {
  try { return await callBridge(['prompt-read', id], 60000); }
  catch (e) { return { ok: false, error: String(e.message || e) }; }
});

ipcMain.handle('pojia:promptWrite', async (_e, payload) => {
  const { id, b64 } = payload || {};
  try { return await callBridge(['prompt-write', id, b64], 60000); }
  catch (e) { return { ok: false, error: String(e.message || e) }; }
});

ipcMain.handle('pojia:promptNew', async (_e, name) => {
  try { return await callBridge(['prompt-new', String(name || '')], 60000); }
  catch (e) { return { ok: false, error: String(e.message || e) }; }
});

ipcMain.handle('pojia:promptDelete', async (_e, id) => {
  try { return await callBridge(['prompt-delete', id], 60000); }
  catch (e) { return { ok: false, error: String(e.message || e) }; }
});

ipcMain.handle('pojia:instrList', async (_e, seat) => {
  try { return await callBridge(['instr-list', seat || 'codex'], 60000); }
  catch (e) { return { ok: false, error: String(e.message || e) }; }
});

ipcMain.handle('pojia:instrApply', async (_e, payload) => {
  const { seat, name } = payload || {};
  try { return await callBridge(['instr-apply', seat || 'codex', name || ''], 120000); }
  catch (e) { return { ok: false, error: String(e.message || e) }; }
});

ipcMain.handle('pojia:instrDisable', async (_e, seat) => {
  try { return await callBridge(['instr-disable', seat || 'codex'], 120000); }
  catch (e) { return { ok: false, error: String(e.message || e) }; }
});

// 破甲候选的滑块：on = 部署这一份，off = 关闭注入（同一份状态表）
ipcMain.handle('pojia:instrToggle', async (_e, payload) => {
  const { seat, name, on } = payload || {};
  try {
    return await callBridge(['instr-toggle', seat || 'codex', name || '', on ? '1' : '0'], 120000);
  } catch (e) { return { ok: false, error: String(e.message || e) }; }
});

// 指令库（按 Agent 分区的 md 管理）
ipcMain.handle('pojia:docs', async (_e, agent) => {  try { return await callBridge(['docs', agent || 'codex'], 60000); }
  catch (e) { return { ok: false, error: String(e.message || e) }; }
});

ipcMain.handle('pojia:docsWarm', async () => {
  try { return await callBridge(['docs-warm'], 120000); }
  catch (e) { return { ok: false, error: String(e.message || e) }; }
});

ipcMain.handle('pojia:docImport', async (_e, agent) => {
  try {
    const r = await dialog.showOpenDialog(mainWin, {
      title: '导入指令 Markdown',
      properties: ['openFile'],
      filters: [{ name: 'Markdown', extensions: ['md', 'markdown'] }],
    });
    if (r.canceled || !r.filePaths.length) return { ok: false, canceled: true };
    return await callBridge(['doc-import', agent || 'codex', r.filePaths[0]], 60000);
  } catch (e) {
    return { ok: false, error: String(e.message || e) };
  }
});

ipcMain.handle('pojia:docRead', async (_e, id, agent) => {
  try { return await callBridge(['doc-read', id, agent || ''], 60000); }
  catch (e) { return { ok: false, error: String(e.message || e) }; }
});

ipcMain.handle('pojia:docWrite', async (_e, payload) => {
  const { id, b64 } = payload || {};
  try { return await callBridge(['doc-write', id, b64], 60000); }
  catch (e) { return { ok: false, error: String(e.message || e) }; }
});

ipcMain.handle('pojia:docNew', async (_e, payload) => {
  const { agent, cat, name } = payload || {};
  try { return await callBridge(['doc-new', agent || 'codex', cat || 'custom', name || ''], 60000); }
  catch (e) { return { ok: false, error: String(e.message || e) }; }
});

ipcMain.handle('pojia:docDelete', async (_e, id) => {
  try { return await callBridge(['doc-delete', id], 60000); }
  catch (e) { return { ok: false, error: String(e.message || e) }; }
});

ipcMain.handle('pojia:docToggle', async (_e, id, agent) => {
  try { return await callBridge(['doc-toggle', id, agent || ''], 60000); }
  catch (e) { return { ok: false, error: String(e.message || e) }; }
});

// 批量开关：一次过桥写完，界面不用为每份文档各等一趟
ipcMain.handle('pojia:docToggleMany', async (_e, items, agent) => {
  try {
    const b64 = Buffer.from(JSON.stringify(items || []), 'utf8').toString('base64');
    return await callBridge(['doc-toggle-many', b64, agent || ''], 90000);
  } catch (e) { return { ok: false, error: String(e.message || e) }; }
});

ipcMain.handle('pojia:docMemo', async (_e, payload) => {
  const { id, memo, agent } = payload || {};
  try { return await callBridge(['doc-memo', id || '', memo || '', agent || ''], 60000); }
  catch (e) { return { ok: false, error: String(e.message || e) }; }
});

ipcMain.handle('pojia:test', async (_e, targets) => {
  try { return await callBridge(['test', (targets || []).join(',')], 300000); }
  catch (e) { return { error: String(e.message || e) }; }
});

ipcMain.handle('pojia:route', async (_e, sentence) => {
  try { return await callBridge(['route', sentence], 60000); }
  catch (e) { return { error: String(e.message || e), hits: [] }; }
});

ipcMain.handle('pojia:run', async (e, payload) => {
  const { action, targets, flags } = payload || {};
  return runAction(action, targets, flags, e.sender);
});

ipcMain.on('pojia:abort', () => { if (running) { try { running.kill(); } catch (_) {} } });

ipcMain.handle('pojia:setPython', async (_e, exe) => setPython(exe));

ipcMain.handle('pojia:pickPython', async () => {
  const r = await dialog.showOpenDialog(mainWin, {
    title: '选择 python.exe',
    properties: ['openFile'],
    filters: [{ name: 'Python', extensions: ['exe'] }],
  });
  if (r.canceled || !r.filePaths.length) return { ok: false, canceled: true };
  return setPython(r.filePaths[0]);
});

ipcMain.handle('pojia:openPath', async (_e, target) => {
  if (!target) return { ok: false };
  if (fs.existsSync(target)) {
    const st = fs.statSync(target);
    if (st.isDirectory()) { await shell.openPath(target); return { ok: true }; }
    shell.showItemInFolder(target);
    return { ok: true };
  }
  return { ok: false, error: '路径不存在：' + target };
});

ipcMain.handle('pojia:readLog', async () => {
  const p = path.join(ROOT, '状态', '破甲日志.txt');
  try {
    const txt = fs.readFileSync(p, 'utf8');
    return { ok: true, path: p, lines: txt.split(/\r?\n/).slice(-400) };
  } catch (e) { return { ok: false, error: String(e.message || e), path: p, lines: [] }; }
});

ipcMain.handle('pojia:openConsole', async () => {
  // 直接跑项目里的 console.bat —— 系统会开一个独立控制台窗口跑交互菜单
  const bat = path.join(ROOT, 'console.bat');
  if (!fs.existsSync(bat)) return { ok: false, error: '找不到 console.bat' };
  const err = await shell.openPath(bat);
  return err ? { ok: false, error: err } : { ok: true };
});

// ── GitHub 更新检查（公开仓，无 token 也能读） ──────────────────────────────
// 渲染层点版本号 → 这里去 api.github.com 拉 releases 列表（含历史版本）→
// 逐条比对本地 VERSION，标记当前版本与是否有更新。
//
// 不用 Electron 的 fetch：Chromium 网络栈会读系统代理（本机是 Clash 的
// 127.0.0.1:7897），代理挂掉/半死时 fetch 直接失败，而系统 curl 走同一代理却正常。
// 用 curl.exe 起子进程，失败再退回 node 的 https 直连，两条路都不依赖渲染会话。
const REPO_SLUG = 'Wei894348/pojia_desk';
const REPO_URL = 'https://github.com/' + REPO_SLUG;
const RELEASES_URL = REPO_URL + '/releases';

function ghJsonByCurl(url) {
  return new Promise((resolve, reject) => {
    const child = spawn('curl.exe', [
      '-sS', '-L', '--max-time', '20',
      '-H', 'User-Agent: pojia-workbench',
      '-H', 'Accept: application/vnd.github+json',
      url,
    ], { windowsHide: true, env: cleanEnv() });
    let out = '', err = '';
    child.stdout.on('data', (d) => { out += d.toString('utf8'); });
    child.stderr.on('data', (d) => { err += d.toString('utf8'); });
    child.on('error', reject);
    child.on('close', (code) => {
      if (code !== 0) return reject(new Error(err || ('curl exit ' + code)));
      try { resolve(JSON.parse(out)); } catch (e) { reject(e); }
    });
  });
}

function ghJsonByHttps(url) {
  return new Promise((resolve, reject) => {
    const https = require('https');
    const req = https.get(url, {
      headers: { 'User-Agent': 'pojia-workbench', 'Accept': 'application/vnd.github+json' },
      timeout: 20000,
    }, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (d) => { body += d; });
      res.on('end', () => {
        if (res.statusCode !== 200) return reject(new Error('HTTP ' + res.statusCode));
        try { resolve(JSON.parse(body)); } catch (e) { reject(e); }
      });
    });
    req.on('timeout', () => req.destroy(new Error('timeout')));
    req.on('error', reject);
  });
}

ipcMain.handle('pojia:checkUpdate', async () => {
  const local = (fs.readFileSync(path.join(ROOT, 'VERSION'), 'utf8').trim()) || '';
  const out = {
    ok: false, local, latest: '', url: REPO_URL, releasesUrl: RELEASES_URL,
    body: '', published: '', releases: [], source: '',
  };
  const api = 'https://api.github.com/repos/' + REPO_SLUG + '/releases?per_page=30';
  let arr = null, errs = [];
  for (const [name, fn] of [['curl', ghJsonByCurl], ['https', ghJsonByHttps]]) {
    try {
      const j = await fn(api);
      if (Array.isArray(j)) { arr = j; out.source = name; break; }
      errs.push(name + ': 返回不是数组');
    } catch (e) { errs.push(name + ': ' + String(e.message || e)); }
  }
  if (!arr) { out.error = errs.join(' | '); return out; }
  out.releases = arr.map((r) => ({
      tag: String(r.tag_name || '').replace(/^v/i, '').trim(),
      name: r.name || '',
      url: r.html_url || '',
      body: r.body || '',
      published: r.published_at || '',
      prerelease: !!r.prerelease,
    })).filter((r) => r.tag);
  const latest = out.releases.find((r) => !r.prerelease) || out.releases[0];
  if (!latest) return out;
  out.latest = latest.tag;
  out.url = latest.url || REPO_URL;
  out.body = latest.body;
  out.published = latest.published;
  out.ok = true;
  out.hasUpdate = !!latest.tag && latest.tag !== local;
  return out;
});

ipcMain.on('pojia:win', (_e, act) => {
  if (!mainWin) return;
  if (act === 'min') mainWin.minimize();
  else if (act === 'max') mainWin.isMaximized() ? mainWin.unmaximize() : mainWin.maximize();
  else if (act === 'close') mainWin.close();
});
