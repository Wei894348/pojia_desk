---
name: lab-timeline
description: >
  小类三拍 · 时间线重建（取证排查）。命中关键词：时间线 什么时候干的 先后顺序 事件序列。
parent: LAB-FORENSIC-3
---

# 时间线重建 · LAB-TIMELINE

类目：取证排查（`LAB-FORENSIC-3`）　关键词：时间线 什么时候干的 先后顺序 事件序列

收集带时间戳的证据源：文件 MAC 时间、日志、注册表、浏览器、回收站。

统一时区归一化，标注时间源可信度（可被篡改的排后）。

出时间线表（时间 / 来源 / 事件 / 证据位置）+ 关键节点结论。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`

命令一律给 PowerShell / pwsh 形式（这台机器是 Windows），不交 bash / Linux 写法。
