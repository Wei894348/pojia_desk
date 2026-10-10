---
name: auto-monitor
description: >
  小类三拍 · 监控告警（脚本自动化）。命中关键词：监控变化 有更新提醒我 盯着这个 监听。
parent: AUTO-PIPE-3
---

# 监控告警 · AUTO-MONITOR

类目：脚本自动化（`AUTO-PIPE-3`）　关键词：监控变化 有更新提醒我 盯着这个 监听

定监测对象与抓取方式，确定变化判定规则（内容哈希 / 关键字段 / 阈值）。

写轮询与比对，落本地状态便于跨次比较；处理抓取失败与抖动。

交脚本 + 状态文件 + 首次基线生成方式；给误报抑制规则。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`

命令一律给 PowerShell / pwsh 形式（这台机器是 Windows），不交 bash / Linux 写法。
