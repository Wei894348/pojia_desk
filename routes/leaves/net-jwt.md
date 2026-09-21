---
name: net-jwt
description: >
  小类三拍 · 认证协议（网络测试）。命中关键词：JWT token 伪造 OAuth 越签 会话。
parent: NET-PENTEST-3
---

# 认证协议 · NET-JWT

类目：网络测试（`NET-PENTEST-3`）　关键词：JWT token 伪造 OAuth 越签 会话

拆 token 结构（header / payload / 签名），判算法与密钥来源。

逐条试：alg=none、算法混淆（RS→HS）、弱密钥爆破、kid 注入、jwk 注入；OAuth 侧测 redirect_uri 与 state。

交伪造好的 token 或请求序列 + 验证结果；给签发侧修复清单。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`
