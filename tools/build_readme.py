#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_readme.py —— README / 仓库主页预览的单一来源生成器。

正文只写一份，同时喂给两个出口：
    docs/index.html        → 离线排版预览（同一份正文塞进 GitHub 版式的壳里）
    docs/readme-preview.html → 同上，资源路径指向 /docs/（静态服务器场景）

正文写成 HTML-in-Markdown（<div>/<table>/<blockquote>/<pre>），GitHub 原生渲染，
预览页可以直接当 innerHTML 用 —— 一份正文两处不跑偏。

版本号单一来源是仓库根的 VERSION；--sync-version 会把它推到
gui/renderer/index.html 的占位和 gui/package.json 的 version。

用法：
    python tools/build_readme.py                 # 生成 README.md + docs/*.html
    python tools/build_readme.py --check         # 只比对，不写（CI 用）
    python tools/build_readme.py --sync-version  # 顺带把版本推到 GUI 那两处
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOCS = os.path.join(ROOT, "docs")
ASSETS = os.path.join(DOCS, "assets")

# 正文里引用的位图：生成时按内容哈希改名，避免旧图被浏览器缓存顶掉
CONTENT_ADDRESSED = ()


# ── 版本 ──────────────────────────────────────────────────────────────────────

def read_version() -> str:
    path = os.path.join(ROOT, "VERSION")
    try:
        with open(path, "r", encoding="utf-8") as f:
            v = f.read().strip()
        if v:
            return v
    except OSError:
        pass
    raise SystemExit("读不到 VERSION 文件：%s" % path)


# ── 内置矢量图（代码原生，二进制资产不进库） ──────────────────────────────────

HERO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 260" width="1200" height="260" role="img" aria-label="破甲工作台 · 多目标人格注入控制台">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#0d1117"/><stop offset="1" stop-color="#161d26"/>
    </linearGradient>
    <linearGradient id="ac" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#4d9fff"/><stop offset="1" stop-color="#a371f7"/>
    </linearGradient>
  </defs>
  <rect width="1200" height="260" rx="16" fill="url(#bg)"/>
  <g opacity=".18" stroke="#4d9fff" stroke-width="1">
    <path d="M0 60H1200M0 120H1200M0 180H1200M200 0V260M600 0V260M1000 0V260"/>
  </g>
  <text x="64" y="112" fill="#e6edf3" font-family="Microsoft YaHei,Segoe UI,sans-serif" font-size="52" font-weight="700">破甲工作台</text>
  <text x="64" y="156" fill="#8b949e" font-family="Microsoft YaHei,Segoe UI,sans-serif" font-size="20">pojia_desk · 多目标人格注入：部署 / 验证 / 恢复</text>
  <rect x="64" y="182" width="196" height="4" rx="2" fill="url(#ac)"/>
  <g font-family="Consolas,monospace" font-size="15" fill="#58a6ff">
    <text x="64" y="222">engine/ · content/ · targets/ · gui/ · tools/</text>
  </g>
</svg>
"""

ARCH_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 420" width="1200" height="420" role="img" aria-label="破甲工作台架构：入口 → 引擎 → 落点">
  <defs>
    <marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
      <path d="M0 0L10 5L0 10z" fill="#4d9fff"/>
    </marker>
  </defs>
  <rect width="1200" height="420" fill="#ffffff"/>
  <g font-family="Microsoft YaHei,Segoe UI,sans-serif" font-size="14" fill="#1f2328">
    <rect x="40" y="60" width="240" height="140" rx="10" fill="#f6f8fa" stroke="#d1d9e0"/>
    <text x="60" y="92" font-weight="700" font-size="16">入口</text>
    <text x="60" y="120">一键破甲.bat / console.bat</text>
    <text x="60" y="146">engine/console.py（菜单 / 子命令）</text>
    <text x="60" y="172">gui/（Electron 工作台）</text>

    <rect x="360" y="40" width="280" height="180" rx="10" fill="#f6f8fa" stroke="#d1d9e0"/>
    <text x="380" y="72" font-weight="700" font-size="16">引擎 engine/</text>
    <text x="380" y="100">破甲一键通.py（目标定义 / 补丁）</text>
    <text x="380" y="126">pojia_routes.py（路由装配）</text>
    <text x="380" y="152">pojia_skills.py（86 份 SKILL）</text>
    <text x="380" y="178">pojia_tx.py（部署事务）</text>
    <text x="380" y="204">zcode_inject.py（cjs 注入）</text>

    <rect x="720" y="40" width="440" height="180" rx="10" fill="#fff7f7" stroke="#d1d9e0"/>
    <text x="740" y="72" font-weight="700" font-size="16">落点（文件级补丁，全部可逆）</text>
    <text x="740" y="100">codex · dsh · dsh-desktop · wb · zcode</text>
    <text x="740" y="126">claude · grok · gemini · hermes</text>
    <text x="740" y="152">+ 各席位 skills/（父类目 6 + 小类 80）</text>
    <text x="740" y="178">+ 状态/tx/&lt;id&gt;/（写前留证快照）</text>
    <text x="740" y="204">+ 历史备份/（按时间戳归档原版）</text>

    <rect x="360" y="278" width="800" height="106" rx="10" fill="#f6f8fa" stroke="#d1d9e0"/>
    <text x="380" y="308" font-weight="700" font-size="16">注入链（内容都在 content/）</text>
    <text x="380" y="336">content/route-layer.md（归一 → 定类 → 出招） →  content/persona.md  →  内容政策 / 覆盖层  →  席位落点</text>
    <text x="380" y="362" fill="#656d76">改路由 = 改 content/routes/ 源件 → render_routes.py → console.py all apply</text>
  </g>
  <g stroke="#4d9fff" stroke-width="2" fill="none" marker-end="url(#ar)">
    <path d="M280 130H352"/>
    <path d="M640 130H712"/>
    <path d="M500 220V270"/>
  </g>
</svg>
"""


def write_assets() -> None:
    os.makedirs(ASSETS, exist_ok=True)
    for name, svg in (("pojia-hero.svg", HERO_SVG), ("pojia-architecture.svg", ARCH_SVG)):
        with open(os.path.join(ASSETS, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(svg)


def content_address(src_name: str) -> str:
    """位图按内容哈希改名并返回新名（不存在就原样返回）。"""
    path = os.path.join(ASSETS, src_name)
    if not os.path.isfile(path):
        return src_name
    digest = hashlib.sha256(open(path, "rb").read()).hexdigest()[:12]
    stem, ext = os.path.splitext(src_name)
    new = "%s-%s%s" % (stem, digest, ext)
    if not os.path.isfile(os.path.join(ASSETS, new)):
        shutil.copyfile(path, os.path.join(ASSETS, new))
    return new


# ── 正文（Markdown + 内联 HTML；GitHub 与预览页共用） ─────────────────────────

def body(version: str) -> str:
    tpl = """<div align="center">
  <h1>破甲工作台 · pojia_desk</h1>
  <p><strong>九个席位 · 一个入口 · 部署 / 验证 / 恢复全程可逆</strong></p>
  <p><sub>v${VERSION} · 纯 Python 标准库</sub></p>
</div>

<h2 id="功能">功能</h2>
<ul>
<li>一个脚本统一管理九个客户端的人格注入：部署、状态检测、预演、验证、还原</li>
<li>Codex / DeepSeek Harness（Web + 桌面版）/ WorkBuddy / ZCode / Claude Code / Grok / Gemini / Hermes</li>
<li>每次改动先备份、进事务快照，随时可整包还原；CLI 不在的席位自动跳过不写盘</li>
<li>86 份路由 skill 按需加载；纯 Python 标准库，零第三方依赖</li>
</ul>

<h2 id="安装">安装</h2>
<pre><code>git clone https://github.com/Wei894348/pojia_desk.git
cd pojia_desk

# 图形工作台（可选）
cd gui
npm install
npm start</code></pre>
<p>要求：Python 3.10+（引擎）；图形工作台另需 Node.js 22+ 与 npm。命令行入口零依赖，装好 Python 就能跑。</p>

<h2 id="使用">使用</h2>
<pre><code>一键破甲.bat              # 双击：终端菜单（部署 / 状态 / 还原）
破甲工作台.bat            # 双击：图形工作台
console.bat all status    # 命令行：全目标状态（只读）
console.bat all apply     # 全目标部署
console.bat all revert    # 全目标还原
console.bat all test      # 全目标验证（只读）
console.bat codex dry     # 预演，不改盘
console.bat tx            # 部署历史 / 回退</code></pre>
<p>部署只对**新会话**生效；运行中的旧会话不会重新读取配置。还原走备份，文件回到写入之前。</p>

<hr />
<p align="center"><strong>破甲工作台</strong> · 纯标准库 · 全程可逆</p>
"""
    return tpl.replace("${VERSION}", version)


PREVIEW_CSS = (
    '*{box-sizing:border-box}body{margin:0;color:#1f2328;background:#fff;'
    'font:14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}'
    'a{color:#0969da;text-decoration:none}a:hover{text-decoration:underline}'
    '.appbar{background:#f6f8fa;border-bottom:1px solid #d1d9e0;padding:18px 30px;display:flex;'
    'justify-content:space-between;align-items:center;gap:20px}'
    '.repo-name{font-size:14px;font-weight:600}.preview-label{font-size:12px;color:#656d76}'
    '.repo-tabs{background:#f6f8fa;padding:0 30px;display:flex;gap:25px;border-bottom:1px solid #d1d9e0}'
    '.repo-tabs span{padding:12px 0;font-size:13px}.repo-tabs .selected{border-bottom:2px solid #f78166}'
    '.container{max-width:1200px;padding:28px;margin:auto}'
    '.notice{border:1px solid #d1d9e0;border-radius:7px;background:#f6f8fa;padding:14px 18px;'
    'margin-bottom:24px;display:flex;justify-content:space-between;align-items:center;gap:20px}'
    '.notice a{background:#1f2328;color:#fff;border-radius:6px;padding:8px 14px;white-space:nowrap}'
    '.repo-grid{display:grid;grid-template-columns:minmax(0,880px) 220px;gap:25px}'
    '.readme{border:1px solid #d1d9e0;border-radius:7px;overflow:hidden;min-width:0}'
    '.readme-tab{font-size:12px;padding:12px 18px;border-bottom:1px solid #d1d9e0;font-weight:600}'
    '.markdown-body{padding:30px;font-size:14px}.markdown-body img{max-width:100%;height:auto}'
    '.markdown-body h1{font-size:30px;border-bottom:1px solid #d1d9e0;padding-bottom:12px;margin:22px 0 16px}'
    '.markdown-body h2{font-size:22px;border-bottom:1px solid #d1d9e0;padding-bottom:9px;margin:34px 0 16px;scroll-margin-top:20px}'
    '.markdown-body h3{font-size:17px;margin:26px 0 12px}'
    '.markdown-body p{margin:12px 0 18px}.markdown-body sub{color:#656d76;font-size:11px}'
    '.markdown-body table{width:100%;border-collapse:collapse;font-size:12px;margin:20px 0}'
    '.markdown-body td,.markdown-body th{border:1px solid #d1d9e0;padding:12px}'
    '.markdown-body th{background:#f6f8fa}'
    '.markdown-body blockquote{margin:24px 0;border-left:4px solid #c92537;background:#fff7f7;'
    'padding:10px 18px;color:#4f353a}.markdown-body blockquote p{margin:7px 0}'
    '.markdown-body pre{padding:16px;border-radius:7px;background:#f6f8fa;overflow:auto}'
    '.markdown-body code{font-family:Consolas,monospace;font-size:12px}'
    '.markdown-body hr{border:0;border-top:1px solid #d1d9e0;margin-top:30px}'
    '.about h3{font-size:15px;margin:0 0 14px}.about p{font-size:13px;color:#656d76}'
    '.about .tag{display:inline-block;font-size:11px;background:#ddf4ff;color:#0969da;'
    'border-radius:14px;padding:3px 9px;margin:0 3px 7px 0}.about hr{border:0;border-top:1px solid #d1d9e0;margin:22px 0}'
    '.about a{display:block;margin-top:12px;font-size:12px}'
    '@media(max-width:900px){.repo-grid{grid-template-columns:1fr}.about{display:none}'
    '.container{padding:16px}.markdown-body{padding:20px}.appbar{padding:15px}.preview-label{display:none}}'
    '@media(max-width:520px){.markdown-body{padding:12px;font-size:12px}.markdown-body h1{font-size:23px}'
    '.markdown-body th,.markdown-body td{padding:5px;font-size:10px}.notice{display:block;font-size:12px}'
    '.notice a{display:inline-block;margin-top:10px}.repo-tabs{gap:14px;padding:0 16px}.repo-tabs span{font-size:11px}}'
)


def preview_page(html_body: str, version: str, absolute: bool) -> str:
    """absolute=True 时资源指到 /docs/（静态服务器），否则相对当前目录。"""
    src = "src=\"/docs/" if absolute else "src=\""
    href = "href=\"/docs/" if absolute else "href=\""
    action_href = "/workbench/" if absolute else "#上手使用"
    action_text = "打开软件交互预览 ↗" if absolute else "启动软件：查看步骤 ↓"
    b = html_body.replace('src="docs/', src).replace('href="docs/', href)
    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>破甲工作台 · 仓库主页排版预览</title><style>%s</style></head><body>
<header class="appbar"><div class="repo-name">pojia_desk / <strong>破甲工作台</strong></div>
<span class="preview-label">仓库主页排版预览 · 非 GitHub 在线页面</span></header>
<nav class="repo-tabs"><span class="selected">代码与说明</span><span>问题讨论</span><span>变更记录</span><span>项目文档</span></nav>
<div class="container">
<div class="notice"><span><strong>九张席位卡、八个命令行目标。</strong>纯 Python 标准库，零第三方依赖；每个改动都有备份与事务快照，随时可还原。</span>
<a href="%s">%s</a></div>
<div class="repo-grid"><article class="readme"><div class="readme-tab">README.md · 项目主页</div>
<div class="markdown-body">%s</div></article>
<aside class="about"><h3>关于破甲工作台</h3>
<p>一个入口，统一管理多目标人格注入的部署、验证与恢复。</p>
<span class="tag">v%s</span><span class="tag">Python 标准库</span><span class="tag">九席位</span>
<hr><h3>当前版本</h3><p>%s<br>命令 <code>console.bat</code> / 图形 <code>破甲工作台.bat</code></p>
<a href="#上手使用">上手使用 ↓</a><a href="#支持的目标">支持的目标 ↓</a><a href="#目录结构">目录结构 ↓</a><a href="#自检与测试">自检与测试 ↓</a>
<hr><p>README 与本页由 <code>tools/build_readme.py</code> 同一份正文生成。</p></aside>
</div></div></body></html>""" % (PREVIEW_CSS, action_href, action_text, b, version, version)


# ── 版本同步 ──────────────────────────────────────────────────────────────────

def sync_version(version: str) -> list:
    """把 VERSION 推到 GUI 的占位版本与 package.json。返回改动过的东西。"""
    changed = []

    # 主脚本的兜底字面 —— 单拷一个 .py 出去跑（没有 VERSION 文件）时用的就是它，
    # 不一起推就会留着旧版本号。
    main_py = os.path.join(ROOT, "engine", "破甲一键通.py")
    if os.path.isfile(main_py):
        with open(main_py, "r", encoding="utf-8", newline="") as f:
            raw = f.read()
        # 只换引号里的版本，不吃行尾空白 —— `\s*$` 会连换行一起吞掉，把函数和下面
        # 的 VERSION = 挤成相邻两行。
        new = re.sub(r'(?m)^([ \t]*return ")([0-9][^"]*)("[ \t]*)$',
                     r"\g<1>%s\g<3>" % version, raw, count=1)
        if new != raw:
            with open(main_py, "w", encoding="utf-8", newline="") as f:
                f.write(new)
            changed.append(os.path.relpath(main_py, ROOT))

    html = os.path.join(ROOT, "gui", "renderer", "index.html")
    if os.path.isfile(html):
        with open(html, "r", encoding="utf-8", newline="") as f:
            raw = f.read()
        new = re.sub(r'(id="tbVersion"[^>]*>v)[^<]+(<)', r"\g<1>%s\g<2>" % version, raw)
        if new != raw:
            with open(html, "w", encoding="utf-8", newline="") as f:
                f.write(new)
            changed.append(os.path.relpath(html, ROOT))

    pkg = os.path.join(ROOT, "gui", "package.json")
    if os.path.isfile(pkg):
        with open(pkg, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") != version:
            data["version"] = version
            with open(pkg, "w", encoding="utf-8", newline="\n") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
            changed.append(os.path.relpath(pkg, ROOT))

    return changed


# ── 主流程 ────────────────────────────────────────────────────────────────────

def render_outputs() -> dict:
    version = read_version()
    text = body(version)
    for name in CONTENT_ADDRESSED:
        new = content_address(name)
        if new != name:
            text = text.replace("docs/assets/" + name, "docs/assets/" + new)
    return {
        os.path.join(ROOT, "README.md"): text,
        os.path.join(DOCS, "index.html"): preview_page(text, version, absolute=False),
        os.path.join(DOCS, "readme-preview.html"): preview_page(text, version, absolute=True),
    }


def main(argv) -> int:
    check = "--check" in argv
    outputs = render_outputs()

    if check:
        bad = []
        for path, text in outputs.items():
            old = ""
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8", newline="") as f:
                    old = f.read().replace("\r\n", "\n")
            if old != text:
                bad.append(os.path.relpath(path, ROOT))
        if bad:
            print("check: 与生成器不一致 → %s" % "、".join(bad))
            print("       跑 python tools/build_readme.py 重新生成")
            return 1
        print("check: README 与预览页都与生成器一致")
        return 0

    if "--sync-version" in argv:
        write_assets()
        changed = sync_version(read_version())
        print("版本同步到：%s" % ("、".join(changed) if changed else "（已是最新）"))
    else:
        write_assets()

    for path, text in outputs.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print("已写 %s（%d B）" % (os.path.relpath(path, ROOT), len(text.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
