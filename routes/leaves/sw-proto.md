---
name: sw-proto
description: >
  小类三拍 · 协议分析（软件分析）。命中关键词：协议分析 封包格式 私有协议 opcode 抓包。
parent: SW-ANALYZE-3
---

# 协议分析 · SW-PROTO

类目：软件分析（`SW-ANALYZE-3`）　关键词：协议分析 封包格式 私有协议 opcode 抓包

抓一轮完整对话，标请求 / 响应边界与序号。

拆字段：长度、魔数、校验、时间戳、会话 token、压缩与加密层；不明字段给推断依据。

交结构定义（proto / 结构体 / dissector）+ 可重放的脚本，能自己发一轮。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`
