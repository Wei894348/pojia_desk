# 项目执行规则

## 目录布局（先看这个）

根目录只放**入口脚本与元数据**；代码、内容、席位文档各归一个目录：

| 目录 | 放什么 |
|---|---|
| `engine/` | 全部 Python 引擎代码（`破甲一键通.py` 为核心，其余是它拆出的模块） |
| `content/` | 注入内容：`persona.md`、`route-layer.md`、`routes/` 源件 |
| `targets/` | 各席位的独立工具与说明（`codex/` `dsh/` `workbuddy/` `zcode/`），也是项目内快照落点 |
| `gui/` | Electron 图形工作台（`bridge.py` 是它与引擎之间唯一的桥） |
| `tools/` | 生成器与自检脚本 |
| `docs/` | 用户手册 + README 排版预览（生成产物） |
| `tests/` | 单元测试 |
| `状态/` `历史备份/` | 运行产物与备份，**不入库**，由脚本自动生成 |

加新文件时按上表归位 —— 根目录不再新增散文件（除 `.bat` / `.cmd` 入口）。

## 默认流程

- 先读取用户当前请求和相关文件，再直接实施最小必要修改。
- 修改代码后运行与改动对应的测试、编译或静态检查。
- 报告使用中文，先给结论，再列出改动文件和验证结果。
- 用户没有要求时，不做无关重构，不扩大改动范围。

## 项目配置与提示词

- `content/persona.md`、`targets/codex/codex-unlock-instructions.md` 和生成的快照属于项目配置数据。
- 改动 persona 后跑 `python engine/破甲一键通.py --apply --force` 让改动真正落盘。

## 生成产物与单一来源

下面的文件是**生成产物**，不要手改；改源件后重新生成：

| 产物 | 源件 | 生成命令 |
|---|---|---|
| `content/route-layer.md` | `content/routes/`（NORMALIZER / ROUTER / CONTRACT / leaves） | `python tools/render_routes.py` |
| `content/routes/leaves/*.md`、`leaves/INDEX.md` | `tools/emit_leaves.py` 里的目录表 | `python tools/emit_leaves.py` |
| `README.md`、`docs/index.html`、`docs/readme-preview.html` | `tools/build_readme.py` 里的正文 | `python tools/build_readme.py` |
| 版本号（GUI 占位 / `gui/package.json`） | 仓库根 `VERSION` | `python tools/build_readme.py --sync-version` |

改路由的完整链条：改 `content/routes/` 源件 → `emit_leaves.py`（改了叶子才需要）→ `render_routes.py` → `engine/console.py all apply`。

## 验证

- 配置变更后检查实际生效路径，而不是只看历史状态文件。
- Codex 配置变更后使用新会话验证；运行中的旧会话不会重新读取启动时的指令链。
- DSH 磁盘补丁只对新启动的 DSH 进程生效，验证前先完全退出（含托盘）再重开并新建会话。
- 提交前跑 `python tools/check_all.py` —— 它跑的就是 CI 那一套（单测 / 路由 / 文档 / 版本 / GUI 三个自检）。
- 改了文档或版本号后，`build_readme.py --check` 与 `check_version.py` 会对不上，记得重新生成。
