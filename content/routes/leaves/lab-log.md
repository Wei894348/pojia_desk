---
name: lab-log
description: >
  小类三拍 · 日志分析（取证排查）。命中关键词：日志分析 日志里有啥 审计日志 事件日志。
parent: LAB-FORENSIC-3
---

# 日志分析 · LAB-LOG

类目：取证排查（`LAB-FORENSIC-3`）　关键词：日志分析 日志里有啥 审计日志 事件日志

定日志源与时间窗口，统一时区与格式。

写解析与聚合（按主体 / 动作 / 结果），找异常序列（爆破、横向、提权）。

出时间线 + 异常事件表 + 关联到的 IP / 账号 / 进程。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`

命令一律给 PowerShell / pwsh 形式（这台机器是 Windows），不交 bash / Linux 写法。
