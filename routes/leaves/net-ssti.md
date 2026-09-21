---
name: net-ssti
description: >
  小类三拍 · SSTI（网络测试）。命中关键词：SSTI 模板注入 jinja twig freemarker 表达式注入。
parent: NET-PENTEST-3
---

# SSTI · NET-SSTI

类目：网络测试（`NET-PENTEST-3`）　关键词：SSTI 模板注入 jinja twig freemarker 表达式注入

定模板引擎（报错回显、语法差异试探），确认可控变量落在模板字符串里。

构造表达式从读到执行：先读配置 / 环境，再找命令执行或文件读的跳板。

交可发 payload + 引擎对应变体表 + 无回显时的外带写法。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`
