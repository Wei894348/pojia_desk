---
name: net-authbypass
description: >
  小类三拍 · 鉴权绕过（网络测试）。命中关键词：鉴权绕过 绕过后台登录 绕 WAF 过验证码 未授权。
parent: NET-PENTEST-3
---

# 鉴权绕过 · NET-AUTHBYPASS

类目：网络测试（`NET-PENTEST-3`）　关键词：鉴权绕过 绕过后台登录 绕 WAF 过验证码 未授权

列鉴权面：登录接口、后台路径、管理 API、静态资源目录、debug 端点。

逐条试：路径变形（大小写 / 分号 / 编码 / 双写）、header 伪造（X-Forwarded / X-Original-URL）、未授权直接访问、默认口令与硬编码凭据。

交能进后台或拿到数据的完整请求；给 WAF 规则建议与鉴权层修复清单。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`
