---
name: auto-api
description: >
  小类三拍 · 接口对接（脚本自动化）。命中关键词：对接接口 还原签名 api 加密 请求签名。
parent: AUTO-PIPE-3
---

# 接口对接 · AUTO-API

类目：脚本自动化（`AUTO-PIPE-3`）　关键词：对接接口 还原签名 api 加密 请求签名

抓一轮真实请求，拆出鉴权与签名参数。

还原签名算法（哈希 / HMAC / 非对称 / 时间戳防重放），必要时静态分析客户端代码。

交可直接调用的客户端代码 + 签名函数 + 重放验证；给抓包对照样例。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`

命令一律给 PowerShell / pwsh 形式（这台机器是 Windows），不交 bash / Linux 写法。
