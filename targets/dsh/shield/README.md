# Muse破甲 shield 插件

## 干什么

DSH 只在**会话首轮**读一次 `~/.dsh/AGENTS.md`。对话一长，那段内容会被越推越远，
最后滑出模型的注意力范围 —— 这就是"开头说得好好的，聊到后面就不认账了"。

本插件挂三个钩子解决这件事：

| 钩子 | 做什么 |
|---|---|
| `systemPrompt.section` | 注册一个常驻段，order = -200，排在所有段最前 |
| `system-prompt/assemble` | 每次组装系统提示词时，把这段强制置顶；被 preset 删掉就补回来 |
| `agent/pre-step` | 每次模型调用前，往 messages 末尾补一条 TOOL LOCK |

## 协议文件

- 插件读 `~/.dsh/shield-protocol.md`
- **改这个文件即时生效**，不用重启 DSH（`text` 用的是函数，每次请求都读盘）
- 文件不存在时使用内置兜底文本

## 安装位置

```
~/.dsh/plugins/dsh-shield/
├── index.js
├── package.json
└── cordis.patch.yml
```

`~/.dsh/profiles/<name>/cordis.patch.yml` 里加一行 insert 指向它。

## 卸载

删掉插件目录，并从 profile 的 `cordis.patch.yml` 里去掉对应的 insert 条目。
Muse破甲 的还原流程会处理这两步。
