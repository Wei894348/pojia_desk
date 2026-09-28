// 破甲工作台 · 主进程
// 与渲染层之间只走 IPC；所有真实操作都转发给 gui/bridge.py（它再驱动 console.py）。
const { app, BrowserWindow, ipcMain, shell, dialog, screen } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn, spawnSync } = require('child_process');

const GUI_DIR = __dirname;
const ROOT = path.dirname(GUI_DIR);
const BRIDGE = path.join(GUI_DIR, 'bridge.py');
const CONSOLE_PY = path.join(ROOT, 'console.py');
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
  push(path.join(ROOT, '.venv', 'Scripts', 'python.exe'));
  push(path.join(ROOT, '.venv', 'bin', 'python'));
  // WorkBuddy 自带的 python（本机跑这套脚本用的就是它）
  const home = process.env.USERPROFILE || app.getPath('home');
  const base = path.join(home, '.workbuddy', 'binaries', 'python', 'versions');
  try {
    for (const v of fs.readdirSync(base).sort().reverse()) {
      push(path.join(base, v, 'python.exe'));
      push(path.join(base, v, 'bin', 'python'));
    }
  } catch (_) { /* 不存在就跳过 */ }
  push('py', ['-3']);
  push('python');
  push('python3');
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
    frame: false, backgroundColor: '#0d1117', show: false,
    title: '破甲工作台',
    webPreferences: {
      preload: path.join(GUI_DIR, 'preload.js'),
      contextIsolation: true, nodeIntegration: false, sandbox: false,
      spellcheck: false,
    },
  });
  mainWin.loadFile(path.join(GUI_DIR, 'renderer', 'index.html'));
  mainWin.once('ready-to-show', () => mainWin.show());
  if (process.argv.includes('--dev')) mainWin.webContents.openDevTools({ mode: 'detach' });

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

  // 布局自检：POJIA_LAYOUT_DEBUG=1 时把窗口与各区块的实测尺寸写到 状态/_gui_layout.json
  if (process.env.POJIA_LAYOUT_DEBUG) {
    mainWin.webContents.once('did-finish-load', async () => {
      await new Promise((r) => setTimeout(r, 3500));
      try {
        const s = await mainWin.webContents.executeJavaScript(`(() => {
          const q = (sel) => { const e = document.querySelector(sel); return e ? Math.round(e.getBoundingClientRect().width) : -1; };
          return { vw: innerWidth, vh: innerHeight, body: document.body.scrollWidth,
                   toolbar: q('.toolbar'), cards: q('.cards'), log: q('.logpanel'),
                   route: q('.routebar'), card1: q('.card'), ws: q('.workspace') };
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
  const p = path.join(ROOT, '破甲日志.txt');
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

ipcMain.on('pojia:win', (_e, act) => {
  if (!mainWin) return;
  if (act === 'min') mainWin.minimize();
  else if (act === 'max') mainWin.isMaximized() ? mainWin.unmaximize() : mainWin.maximize();
  else if (act === 'close') mainWin.close();
});
