// 复验 DSH 的 agent preset 现状 —— 走 DSH **自己的 profile 加载器**，把最终组装出来的
// 那份 patch 栈里的 preset 声明抽出来看：谁在、插件能不能解析、有没有"一进程只能挂一份"
// 的宿主级行。
//
// 为什么不沿用旧的 discoverPresets：0.1.7 起 `@deepseek-ai/dsh-agent-presets` 这个包已
// 经不存在（拆成 `dsh-agent-preset` + `dsh-agent-preset-registry`），而且
// `$DSH_HOME/.agent-presets/<id>/` 目录已经没有代码会读了（DSH 自带 SKILL.md 原话：
// "Nothing reads that directory any more"）。走 loadProfile 拿到的是 DSH 真实会用的
// 那一份 patch 栈，比再实现一遍发现逻辑可靠。
//
// 用法：node tools/verify_dsh_presets.mjs [profile]     （默认 web）
//      DSH_HOME 环境变量可覆盖默认的 ~/.dsh

import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const DSH_HOME = process.env.DSH_HOME || join(homedir(), '.dsh');
const PROFILE = process.argv[2] || 'web';

/** 一进程只能挂一份的行 —— 与 破甲一键通.py 的 PRESET_DROP_ROWS 同步。 */
const HOST_GLOBAL_PKGS = ['@deepseek-ai/dsh-tool-cordis'];

/** 找可用的 @deepseek-ai 安装根（悬空的 pnpm 软链不算，且必须带 presets/）。 */
function findBase() {
  const cands = [];
  // 1) npx 缓存 —— 真实安装，带完整的 dsh-web-app/presets
  const npx = join(process.env.LOCALAPPDATA || '', 'npm-cache', '_npx');
  if (existsSync(npx)) {
    for (const h of readdirSync(npx)) {
      cands.push(join(npx, h, 'node_modules', '@deepseek-ai'));
    }
  }
  // 2) ~/.dsh/profiles/node_modules —— pnpm 软链层，可能悬空、且不一定带 presets
  cands.push(join(DSH_HOME, 'profiles', 'node_modules', '@deepseek-ai'));
  return (
    cands.find(
      (p) =>
        existsSync(join(p, 'dsh', 'package.json')) &&
        existsSync(join(p, 'dsh-app-boot', 'package.json')) &&
        existsSync(join(p, 'dsh-web-app', 'presets'))
    ) || ''
  );
}

const BASE = findBase();
if (!BASE) {
  console.error('没找到可用的 @deepseek-ai 安装（要同时有 dsh/ 和 dsh-app-boot/），DSH 可能没装。');
  process.exit(1);
}

const require = createRequire(pathToFileURL(join(BASE, 'dsh', 'package.json')).href);
let boot;
try {
  boot = require('@deepseek-ai/dsh-app-boot');
} catch (error) {
  console.error('加载 dsh-app-boot 失败：' + error.message);
  process.exit(1);
}

let profile;
try {
  profile = boot.loadProfile('dsh', PROFILE, join(BASE, 'dsh', 'package.json'), undefined, {
    userLayer: true,
  });
} catch (error) {
  console.error('加载 profile "' + PROFILE + '" 失败：' + error.message);
  process.exit(1);
}

/** 从 patch 栈里递归抽出所有 agent-preset 声明。 */
function collectPresets(node, out = []) {
  if (Array.isArray(node)) {
    for (const item of node) collectPresets(item, out);
    return out;
  }
  if (!node || typeof node !== 'object') return out;
  if (node.name === '@deepseek-ai/dsh-agent-preset' && node.config) out.push(node.config);
  for (const value of Object.values(node)) collectPresets(value, out);
  return out;
}

/** 声明里引用了、但盘上不存在的包 —— 这种行会让整个 preset activation 失败。 */
function missingPkgs(plugins = []) {
  const miss = [];
  for (const plugin of plugins) {
    const name = plugin && plugin.name;
    if (typeof name !== 'string' || !name.startsWith('@')) continue;
    const pkg = name.split('/').slice(0, 2).join('/');
    if (!existsSync(join(BASE, '..', pkg)) && !miss.includes(name)) miss.push(name);
  }
  return miss;
}

/** 扫描各层文件里出现的 preset id —— 用来看"这条声明是谁提供的"。 */
function layerDeclarations(label, files) {
  const rows = [];
  for (const file of files) {
    if (!existsSync(file)) continue;
    let text = '';
    try {
      text = readFileSync(file, 'utf8');
    } catch {
      continue;
    }
    // `- id:` 与裸 `id:` 两种写法都要认（shipped 是列表元素，profile 里可能两种都有）
    for (const m of text.matchAll(/^\s*(?:-\s*)?id:\s*(preset-[a-z0-9-]+)\s*$/gm)) {
      rows.push({ where: label + ' · ' + file.split(/[\\/]/).pop(), id: m[1] });
    }
  }
  return rows;
}

// profile.patches 只是 **profile 自己的 patch 文件**（用户层），shipped bundle 的
// 那几层在 profile.layers[].patches 里 —— 两边都要收，否则只看得见破甲模式。
const allPatches = [...(profile.patches || [])];
for (const layer of profile.layers || []) {
  if (Array.isArray(layer.patches)) allPatches.push(...layer.patches);
}
const presets = collectPresets(allPatches);

console.log('安装根：' + BASE);
console.log('profile：' + PROFILE + '（' + (profile.patches || []).length + ' 层 patch）');
console.log('');
console.log('id'.padEnd(16) + 'order'.padEnd(8) + '插件'.padEnd(8) + 'name');
console.log('-'.repeat(68));

let broken = 0;
const carriers = [];
for (const config of presets) {
  const plugins = config.plugins || [];
  const miss = missingPkgs(plugins);
  const live = plugins
    .filter((plugin) => plugin && HOST_GLOBAL_PKGS.includes(plugin.name))
    .map((plugin) => plugin.name);
  if (live.length) carriers.push({ id: config.id, live });

  const bad = miss.length > 0;
  if (bad) broken += 1;
  console.log(
    String(config.id).padEnd(16) +
      String(config.order ?? '-').padEnd(8) +
      String(plugins.length).padEnd(8) +
      String(config.name ?? '(无 name)')
  );
  if (bad) console.log('  ⚠ 引用了盘上不存在的包：' + miss.join(', ') + '（会 activation 失败）');
}

console.log('');
console.log(presets.length + ' 个 preset，' + broken + ' 个 broken');

// ---- 各层声明来源：一眼看出破甲模式写在哪个文件里 ----
const layers = [
  ...layerDeclarations(
    'shipped',
    existsSync(join(BASE, 'dsh-web-app', 'presets'))
      ? readdirSync(join(BASE, 'dsh-web-app', 'presets')).map((f) =>
          join(BASE, 'dsh-web-app', 'presets', f)
        )
      : []
  ),
  ...layerDeclarations('profile', [join(DSH_HOME, 'profiles', PROFILE, 'cordis.patch.yml')]),
];
console.log('');
console.log('声明来源：');
for (const row of layers) console.log('  ' + row.id.padEnd(18) + row.where);

// ---- 宿主级互斥行：一进程只能挂一份 ----
console.log('');
if (carriers.length === 0) {
  console.log('宿主级互斥行：0 个 preset 携带 —— 任意组合都能在同一进程里共存');
} else {
  for (const carrier of carriers) {
    console.log('携带「' + carrier.live.join(', ') + '」：' + carrier.id);
  }
  if (carriers.length > 1) {
    console.log(
      '⚠ ' +
        carriers.length +
        ' 个 preset 携带同一份进程级注册（' +
        carriers.map((c) => c.id).join(' / ') +
        '）。同时挂载时后挂的那个会失败：' +
        'Host Cordis inspect provider "..." is already registered。' +
        '把其中一份的该行注释掉（破甲一键通 --dsh-preset deploy 会自动做）。'
    );
  } else {
    console.log('（只有一个 preset 携带，与别的模式不冲突）');
  }
}

process.exit(broken || carriers.length > 1 ? 2 : 0);
