# pojia_desk — 破甲工作台

桌面端 AI 客户端的「破甲」控制台：一个入口，统一管理多目标人格注入的
**部署 / 验证 / 恢复**。纯 Python 标准库实现，零第三方依赖；
图形工作台（可选）基于 Electron。

> 本项目仅供对自有环境做个性化定制使用。

---

## 支持的目标

| 目标 | 是什么 | 注入方式 |
|---|---|---|
| `codex` | Codex CLI | 官方 `model_instructions_file` 配置项注入，不改二进制、不抓包 |
| `dsh` | DeepSeek Harness（桌面端 / npm 全局 / npx 缓存 / 便携版） | 三层文件级补丁：提示词层、persona 层、区段层 |
| `wb` | WorkBuddy | 六层靶点：模板 / 命令闸门 / 网页过滤 / 运行时缓存 / 会话快照等 |
| `zcode` | ZCode（Z.ai / GLM 桌面端） | `~/.zcode/AGENTS.md` 角色档改写 + skills 投放 |
| `claude` | Claude Code | `~/.claude/CLAUDE.md` 标记块合并 + skills 投放 |
| `grok` | Grok CLI | `~/.grok/AGENTS.md` 标记块合并 + skills 投放 |
| `gemini` | Gemini CLI | `~/.gemini/GEMINI.md` 标记块合并 + skills 投放 |
| `hermes` | Hermes Agent（Nous Research，MIT） | `SOUL.md`（系统提示词第 1 槽位）+ `skills/`，CLI 与桌面版布局自动识别 |

> 席位类目标除了查标记块，还会探测客户端可执行文件是否还在位
> （PATH → npm 全局 → npx 缓存 → 特有路径）；文件在、客户端没了会显示
> **「CLI 未检测到」**而不是「已部署」。

---

## 快速开始

### 双击（Windows）

- **`一键破甲.bat`** —— 终端菜单：一键部署 / 状态检测 / 诊断详情
- **`破甲工作台.bat`** —— 图形工作台（Electron），部署、验证、恢复全程可视化

### 命令行

```bash
python console.py                      # 交互菜单
python console.py codex apply          # 部署 codex 人格
python console.py all status           # 全目标状态检测（只读）
python console.py wb revert            # 从备份复原官方原版
python console.py all test             # 核对部署产物是否为当前版本（只读）
python console.py codex dry            # 预演，不改盘
```

动作一览：

| 动作 | 说明 |
|---|---|
| `apply` | 破甲部署（`--force` 直接落盘） |
| `revert` | 从备份还原官方原版 |
| `status` | 状态检测（只读） |
| `test` | 验证测试（只读） |
| `dry` | 预演（不改盘） |
| `route` | 口语归一预览（只读） |
| `tx` | 部署事务：历史 / 校验 / 按快照回退 |

### 档位 × 通道组合器

```bash
python compose.py                                          # 交互菜单
python compose.py --profile max --channel reverse --goal "分析 xxx.exe 的授权逻辑"
python console.py compose max reverse "分析 xxx.exe 的授权逻辑"
```

五个档位定义一次会话的工作链与语气，五个通道定义能力焦点，
输出一段可直接作为会话开场的指令文本。

---

## 部署事务（tx）

每次部署自动留快照，出问题可整包回退：

```bash
python console.py tx                    # 查看部署历史
python console.py tx restore <id>       # 回退到某次部署之前
```

- 所有被改动的官方原版文件先备份再落盘，`revert` 随时可还原
- 回退走快照校验，防止半截状态

---

## 目录结构

```
├── console.py            # 控制台，四目标/八目标统一入口
├── 破甲一键通.py          # 核心引擎（目标定义、补丁、状态判定）
├── compose.py            # 档位 × 通道组合器
├── pojia_routes.py / pojia_skills.py / pojia_tx.py   # 路由 / skills / 事务
├── routes/               # 路由层源件（归一 → 定类 → 出招）
├── codex/ dsh/ workbuddy/ zcode/     # 各目标独立工具与说明
├── gui/                  # Electron 图形工作台（npm start）
├── tools/                # 路由渲染等辅助脚本
└── tests/                # 单元测试
```

---

## 测试

```bash
python -m unittest discover tests
```
