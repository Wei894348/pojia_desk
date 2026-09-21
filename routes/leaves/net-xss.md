---
name: net-xss
description: >
  小类三拍 · XSS（网络测试）。命中关键词：XSS 跨站 弹窗 盲打 存储型 反射型。
parent: NET-PENTEST-3
---

# XSS · NET-XSS

类目：网络测试（`NET-PENTEST-3`）　关键词：XSS 跨站 弹窗 盲打 存储型 反射型

定输出上下文：HTML 正文、属性、JS 字符串、URL；上下文决定 payload 形态。

测过滤规则（转义、长度、标签白名单），逐个绕过并记录有效载荷。

交可复现的 PoC（含触发步骤）+ 改用例，标出是反射还是存储，给修复位点。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`
