// 接口契约自检 —— 三层之间"谁答应了什么、谁真的调了"对不对得上。
//
// 为什么需要它：这套 GUI 有三层接口，任何一层改名/少写都会变成运行时才炸的
// `undefined is not a function` / `No handler registered`，而且只在点某个按钮时才出现。
//   renderer/app.js  →  preload.js（window.pojia.*）  →  main.js（ipcMain.*）
// 顺带查 index.html 的静态 id 与 app.js 里 $('…') 的引用是否对得上。
//
// 用法：node gui/tools/check_contract.mjs
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const GUI = path.dirname(HERE);
const read = (p) => fs.readFileSync(p, 'utf8');

const appSrc = read(path.join(GUI, 'renderer', 'app.js'));
const preSrc = read(path.join(GUI, 'preload.js'));
const mainSrc = read(path.join(GUI, 'main.js'));
const htmlSrc = read(path.join(GUI, 'renderer', 'index.html'));
const cssSrc = read(path.join(GUI, 'renderer', 'style.css'));

let pass = 0, fail = 0, note = 0;
const check = (name, cond, extra) => {
  if (cond) { pass++; console.log('  ✓ ' + name); }
  else { fail++; console.log('  ✗ ' + name + (extra ? '  → ' + extra : '')); }
};
const hint = (name, extra) => { note++; console.log('  · ' + name + (extra ? '  → ' + extra : '')); };

const uniq = (a) => [...new Set(a)];
const grab = (src, re, i = 1) => uniq([...src.matchAll(re)].map((m) => m[i]));

// ── 1. renderer → preload：api.X 必须在 exposeInMainWorld 里 ──
console.log('【1】renderer 调的 window.pojia.* 都在 preload 里');
const exposed = grab(preSrc, /^\s{2}([A-Za-z_$][\w$]*)\s*:/gm);
const called = grab(appSrc, /\bapi\.([A-Za-z_$][\w$]*)\s*\(/g);
check('preload 暴露的成员不为空', exposed.length > 0, exposed.join(','));
const missingApi = called.filter((k) => !exposed.includes(k));
check('renderer 调用的 ' + called.length + ' 个方法全都存在',
  missingApi.length === 0, '缺：' + missingApi.join(','));
const unusedApi = exposed.filter((k) => !called.includes(k));
if (unusedApi.length) hint('preload 里定义了但渲染层没用的方法：' + unusedApi.join('、'));

// ── 2. preload → main：ipcRenderer 的通道必须在 main 里有 handler ──
console.log('【2】preload 的 IPC 通道在 main 里都注册了');
const invoked = grab(preSrc, /ipcRenderer\.invoke\(\s*'([^']+)'/g);
const sent = grab(preSrc, /ipcRenderer\.send\(\s*'([^']+)'/g);
const handled = grab(mainSrc, /ipcMain\.handle\(\s*'([^']+)'/g);
const listened = grab(mainSrc, /ipcMain\.on\(\s*'([^']+)'/g);
const badInvoke = invoked.filter((c) => !handled.includes(c));
const badSend = sent.filter((c) => !listened.includes(c));
check('invoke 的 ' + invoked.length + ' 个通道都有 ipcMain.handle',
  badInvoke.length === 0, '缺：' + badInvoke.join(','));
check('send 的 ' + sent.length + ' 个通道都有 ipcMain.on',
  badSend.length === 0, '缺：' + badSend.join(','));
// 反向：main 定义了一次性通道但没人调，通常是改名后的残骸
const orphanMain = handled.filter((c) => !invoked.includes(c));
check('main 里没有孤儿 handle（改了名没人调的残骸）',
  orphanMain.length === 0, orphanMain.join(','));
// main 往渲染层推的 log 通道，preload 必须监听
const push = grab(mainSrc, /sender\.send\(\s*'([^']+)'/g);
const listenedByPreload = grab(preSrc, /ipcRenderer\.on\(\s*'([^']+)'/g);
check('main 推送的通道 preload 都在监听',
  push.every((c) => listenedByPreload.includes(c)), push.filter((c) => !listenedByPreload.includes(c)).join(','));

// ── 3. app.js 的 $('id') 在 index.html 或动态创建里能找到 ──
console.log('【3】$(\'id\') 引用的元素都存在');
const htmlIds = grab(htmlSrc, /\bid="([\w-]+)"/g);
const dynIds = new Set([
  ...grab(appSrc, /\.id\s*=\s*'([\w-]+)'/g),
  ...grab(appSrc, /\bmk\(\s*'([\w-]+)'/g),
  ...grab(appSrc, /(?:querySelector|getElementById)\(\s*'#?([\w-]+)'/g),
  ...grab(appSrc, /id="([\w-]+)"/g),        // innerHTML 动态拼的 id（更新弹窗的「去更新」按钮）
]);
// $() 里允许拼字符串（变量），只查字面量
const wanted = grab(appSrc, /\$\(\s*'([\w-]+)'\s*\)/g);
const missingId = wanted.filter((k) => !htmlIds.includes(k) && !dynIds.has(k));
check('$(\'…\') 的 ' + wanted.length + ' 个字面量 id 都有出处',
  missingId.length === 0, '缺：' + missingId.join(','));
check('index.html 的静态 id 没有重复', htmlIds.length === uniq(htmlIds).length);
hint('静态 id ' + htmlIds.length + ' 个 / 动态建 id ' + dynIds.size + ' 个');

// ── 4. 样式：app.js 用到的 class 在 style.css 里有定义 ──
console.log('【4】渲染层用到的 class 在 style.css 里有定义');
const usedCls = new Set([
  ...grab(appSrc, /className\s*=\s*'([^']+)'/g).flatMap((s) => s.split(/\s+/)),
  ...grab(appSrc, /classList\.(?:add|toggle)\(\s*'([\w-]+)'/g),
]);
// 只认"看着像类名"的：长度 > 2、不是纯数字、不含 ${}
const looksLikeCls = (c) => c.length > 2 && !/^\d/.test(c) && !c.includes('$');
const cssCls = new Set(grab(cssSrc, /\.(-?[A-Za-z_][\w-]*)/g));
const undef = [...usedCls].filter((c) => looksLikeCls(c) && !cssCls.has(c));
check('没有"用了但没定义"的 class', undef.length === 0, undef.join(','));

// ── 5. 静态资源与入口文件在位 ──
console.log('【5】入口 / 资源在位');
const boot = (() => { try { return JSON.parse(read(path.join(GUI, 'package.json'))); } catch (_) { return null; } })();
check('package.json 能解析且 main 指向的文件存在',
  !!boot && !!boot.main && fs.existsSync(path.join(GUI, boot.main)), boot && boot.main);
check('index.html 引的 app.js / style.css 都在',
  fs.existsSync(path.join(GUI, 'renderer', 'app.js'))
  && fs.existsSync(path.join(GUI, 'renderer', 'style.css')));
check('index.html 里没有引用已删掉的脚本',
  !/<script[^>]+src="[^"]*(preset|dd)[^"]*"/.test(htmlSrc));

// ── 6. 版本号别漂 ──
// 桥的 meta.version 来自主脚本的 VERSION，页面首屏那个只是占位 —— 两边对不上时
// 用户会看到"标题写 v6.1、boot 完变 v6.2"的闪烁，或者干脆一直显示错的。
console.log('【6】页脚版本号与主脚本一致');
// 版本号单一来源是仓库根的 VERSION 文件（主脚本启动时读它，字面只是兜底）。
const srcVer = (() => {
  try { return read(path.join(GUI, '..', 'VERSION')).trim(); } catch (_) { return ''; }
})();
const htmlVer = (/id="tbVersion"[^>]*>v([^<]+)</.exec(htmlSrc) || [])[1];
check('VERSION 文件读到了', !!srcVer, String(srcVer));
check('index.html 的占位版本 = VERSION（' + srcVer + '）', htmlVer === srcVer,
  'html=' + htmlVer);

console.log('\n' + (fail ? '✗ ' + fail + ' 项不过' : '✓ 全过') + '（' + pass + ' 项）'
  + (note ? '，另有 ' + note + ' 条提示' : ''));
process.exit(fail ? 1 : 0);
