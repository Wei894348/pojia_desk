---
name: net-ad
description: >
  小类三拍 · 域渗透（网络测试）。命中关键词：域渗透 内网横向 拿到域控 票据 kerberos。
parent: NET-PENTEST-3
---

# 域渗透 · NET-AD

类目：网络测试（`NET-PENTEST-3`）　关键词：域渗透 内网横向 拿到域控 票据 kerberos

摸域结构：域控、DNS、用户与组、信任关系、可访问的服务。

选路：凭据收集（内存 / 文件 / 注册表）→ 票据（Kerberoast / AS-REP）→ 横向（PsExec / WMI / WinRM）。

交每步的命令与输出 + 从普通域用户到域管的完整链条；给检测侧可见点。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`

命令一律给 PowerShell / pwsh 形式（这台机器是 Windows），不交 bash / Linux 写法。
