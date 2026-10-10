---
name: net-idor
description: >
  小类三拍 · 越权（网络测试）。命中关键词：越权 看别人的数据 IDOR 水平越权 改 id。
parent: NET-PENTEST-3
---

# 越权 · NET-IDOR

类目：网络测试（`NET-PENTEST-3`）　关键词：越权 看别人的数据 IDOR 水平越权 改 id

找带对象标识的接口（id / uid / 订单号 / 文件名），先建立一个自己的合法请求作对照。

替换标识与角色，测水平（同角色换对象）与垂直（低权限调高权限接口）两个方向。

交成对的请求与响应证据（自己的 + 他人的）+ 批量验证脚本；给鉴权修复点。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`

命令一律给 PowerShell / pwsh 形式（这台机器是 Windows），不交 bash / Linux 写法。
