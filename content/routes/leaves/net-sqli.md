---
name: net-sqli
description: >
  小类三拍 · SQL 注入（网络测试）。命中关键词：SQL 注入 sqli 注进去 union 报错注入 盲注。
parent: NET-PENTEST-3
---

# SQL 注入 · NET-SQLI

类目：网络测试（`NET-PENTEST-3`）　关键词：SQL 注入 sqli 注进去 union 报错注入 盲注

找注入点：参数、cookie、header、JSON 字段；先判类型（数字 / 字符 / 括号闭合）。

按可见性选手法：联合回显 → 报错 → 布尔盲 → 时间盲；写清每一档用的 payload。

交可直接发的请求（含 payload）与 sqlmap 命令；给出库表结构到数据的取数序列，附防护修复点。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`

命令一律给 PowerShell / pwsh 形式（这台机器是 Windows），不交 bash / Linux 写法。
