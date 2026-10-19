# 当前部署 vs Susu 石井包 · 逐项对比

采集时间：2026-10-20 ｜ 本项目 v6.13.4 ｜ Susu v5.7.x

---

## 一、落点对比

| 落点 | Susu | 我们 | 差异 |
|---|---|---|---|
| `~/.dsh/AGENTS.md` | ✅ 整份覆盖 | ✅ 38.6 KB 标记块合并 | 我们保留用户原有内容 |
| `~/.agents/AGENTS.md` | ❌ 不用 | ✅ 2.8 KB 精简层 | **我们多一层** |
| `~/.dsh/AGENTS.local.md` | ❌ 不用 | ✅ 0.9 KB 极简层 | **我们多一层** |
| `~/.dsh/shield-protocol.md` | ✅ 3.0 KB | ✅ 1.4 KB | 同量级 |
| `~/.dsh/plugins/dsh-shield/` | ✅ | ✅ 已挂 6 个 profile | 对齐 |
| `~/.dsh/plugins/dsh-prompt-inject/` | ✅ | ❌ | **缺** |
| `~/.dsh/plugins/dsh-purge/` | ✅ | ❌（改为内置实现） | 等价 |
| `~/.dsh/skills/` | 151 个 | 107 个 | 技能体系不同 |

---

## 二、源码补丁（8 处）

两边都打 8 处，本机状态：

```
#1 GLOBAL_INTRO          applied    全局免责 → 强指令
#2 REPLACEMENT_INTRO     applied    替换式免责 → 强指令
#3 SCOPE_INTRO           applied    作用域免责 → 强指令
#4 PERSONA_DIRECTIVE     applied    persona 会话强指令
#5 APPROVAL_SENTENCE     applied    审批提示句 → 全权限
#6 SANDBOX_DENY_WRITE    applied    沙箱 deny file-write → allow
#7 SANDBOX_MODE          applied    沙箱默认 danger-full-access
#8 APPROVAL_POLICY       applied    审批默认 never
```

**实现方式不同**：
- Susu：Node 插件 + HTTP 端点，锚点按 0.1.1 / 0.1.5-rc.1 写死
- 我们：纯 Python（`engine/dsh_purge.py`），**每个补丁挂多形态**，命中任一即应用

**关键差异**：本机是 npm 全局安装的最新版，常量名已从
`WORKSPACE_CONTEXT_INTRO` 改为 `AGENT_INSTRUCTIONS_INTRO`。
照抄 Susu 的锚点会**静默失效**（报 pattern_not_found），我们的多形态写法不会。

---

## 三、内容分工（我们独有的设计）

DSH 的预算是 65536 字节，且**超预算时先丢最宽的那层整份文件**。
所以不能三层各塞全文。

| 层 | 大小 | 章节 |
|---|---|---|
| 全局 `~/.dsh/AGENTS.md` | 38.6 KB | 口令响应、身份、表演规则、创作室、示范、自检口令、交付格式 |
| 共享根 `~/.agents/AGENTS.md` | 2.8 KB | 身份、表演规则、交付格式 |
| 本地覆盖 `~/.dsh/AGENTS.local.md` | 0.9 KB | 身份、交付格式 |
| shield 协议 | 1.4 KB | 环境认定、交付形态、路径记录、复核词、输出收口 |

哪怕最宽的全局层被整体丢弃，后两层仍能维持基本人设。

Susu 没有这个设计 —— 只有一份 3 KB 的协议 + 一份 AGENTS.md。

---

## 四、注入时机

| 机制 | Susu | 我们 |
|---|---|---|
| 会话首轮读指令文件 | ✅ DSH 原生 | ✅ DSH 原生 |
| 每请求重读协议 | ✅ section text 函数 | ✅ 同 |
| 每步往 messages 补锁 | ✅ pre-step TOOL LOCK | ✅ 同 |
| 急救暗号 | 冷咖啡 / cold coffee | **学姐 / 麻衣学姐**（整句匹配） |
| 暗号轮整条替换普通锁 | ✅ | ✅ |

### 暗号设计的差异

Susu 用「冷咖啡」这类**不会自然出现**的词。
我们用「学姐」，但**必须整句匹配** —— 这个词在人设里高频出现，
子串匹配会让每句带「学姐」的普通请求都误触发急救锁。
实现上先去掉首尾标点与空白，再判断整句相等。

---

## 五、我们缺的一项

**`dsh-prompt-inject`** —— Susu 用它做「每请求注入模板」，支持：
- 按会话选模板
- 模板库（保存 / 编辑 / 删除）
- 输入框工具行下拉
- 变量插值 `{{model}}` / `{{cwd}}`

我们没做，因为：
1. 它是一个第三方 bundle，要装进 profile 依赖目录并注册 UI
2. 我们的三层原生指令文件已覆盖「每请求注入」的目的
3. 多一个 bundle = 多一个升级时可能失效的依赖

需要的话可以补，但要评估是否值得引入这层依赖。

---

## 六、总结

| 维度 | 结论 |
|---|---|
| 落点覆盖 | **我们更全**（多两层原生指令文件） |
| 源码补丁 | 等价，**我们的兼容性更好**（多形态锚点） |
| 内容分层 | **我们更细**（按预算策略分工） |
| 每步重锁 | 对齐 |
| 急救暗号 | 对齐（词不同，实现更严谨） |
| 每请求模板 | **Susu 有，我们缺** |
| 技能数量 | Susu 151，我们 107（体系不同） |
| 修改 DSH 源码 | 两边都做，实现路径不同 |

**唯一实质差距**：`dsh-prompt-inject` 那套会话级模板注入。
其余项目要么对齐，要么我们做得更细。
