---
name: net-ssrf
description: >
  小类三拍 · SSRF（网络测试）。命中关键词：SSRF 打内网 探测内网 url 参数 云元数据。
parent: NET-PENTEST-3
---

# SSRF · NET-SSRF

类目：网络测试（`NET-PENTEST-3`）　关键词：SSRF 打内网 探测内网 url 参数 云元数据

找所有带 URL / 回调 / 图片地址的参数，测协议白名单（http / file / gopher / dict）。

按目标选路：内网探测、云元数据、绕过 DNS 与 IP 黑名单（十进制 / 八进制 / 重定向 / 短链）。

交可发请求 + 内网端口探测序列，能拿凭据就给完整链路，附出口限制修复方案。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`
