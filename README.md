<div align="center">
  <img src="docs/assets/muse-logo.png" width="120" alt="Muse logo">
  <h1>Muse · pojia_desk</h1>
  <p><strong>七个席位 · 一个入口 · 部署 / 验证 / 恢复全程可逆</strong></p>
  <p><sub>v6.13.3 · 纯 Python 标准库</sub></p>
</div>

<p align="center"><img src="docs/assets/muse-cover.png" width="820" alt="Muse 封面"></p>

<h2 id="功能">功能</h2>
<ul>
<li>一个脚本统一管理七个客户端的人格注入：部署、状态检测、预演、验证、还原</li>
<li>Codex / DeepSeek Harness（Web + 桌面版）/ WorkBuddy / ZCode / Claude Code / Hermes</li>
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
破甲工作台.bat            # 双击：图形工作台（Muse）
console.bat all status    # 命令行：全目标状态（只读）
console.bat all apply     # 全目标部署
console.bat all revert    # 全目标还原
console.bat all test      # 全目标验证（只读）
console.bat codex dry     # 预演，不改盘
console.bat tx            # 部署历史 / 回退</code></pre>
<p>部署只对**新会话**生效；运行中的旧会话不会重新读取配置。还原走备份，文件回到写入之前。</p>

<hr />
<p align="center"><strong>Muse</strong> · 纯标准库 · 全程可逆</p>
