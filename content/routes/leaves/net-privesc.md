---
name: net-privesc
description: >
  小类三拍 · 提权（网络测试）。命中关键词：提权 从普通用户变管理员 sudo suid 内核。
parent: NET-PENTEST-3
---

# 提权 · NET-PRIVESC

类目：网络测试（`NET-PENTEST-3`）　关键词：提权 从普通用户变管理员 sudo suid 内核

先收集：内核与系统版本、sudo 配置、SUID 文件、计划任务、可写服务、环境变量。

对每个可疑项找可用路径（配置错误、已知内核问题、服务替换、凭据复用）。

交可直接跑的提权命令序列 + 提权后凭据位置；给加固清单。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`

命令一律给 PowerShell / pwsh 形式（这台机器是 Windows），不交 bash / Linux 写法。
