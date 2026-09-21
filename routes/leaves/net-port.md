---
name: net-port
description: >
  小类三拍 · 端口扫描（网络测试）。命中关键词：端口扫描 nmap 全端口 服务识别 masscan。
parent: NET-PENTEST-3
---

# 端口扫描 · NET-PORT

类目：网络测试（`NET-PENTEST-3`）　关键词：端口扫描 nmap 全端口 服务识别 masscan

先快扫全网段拿存活，再对存活主机做全端口 + 服务版本探测。

给出可直接跑的命令（含速率、并发、指纹选项），标出可能触发风控的参数。

出端口 / 服务 / 版本表，圈出可进一步验证的服务（Web、DB、远程管理、中间件）。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`
