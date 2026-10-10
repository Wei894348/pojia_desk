---
name: game-packet
description: >
  小类三拍 · 封包分析（游戏工具）。命中关键词：封包 发包 改包 opcode 抓游戏包。
parent: GAME-TOOL-3
---

# 封包分析 · GAME-PACKET

类目：游戏工具（`GAME-TOOL-3`）　关键词：封包 发包 改包 opcode 抓游戏包

抓一轮游戏对话，识别协议类型（TCP / UDP / KCP / 私有可靠层）与加密状态。

拆 opcode 与包体结构，定位可影响玩法的字段（坐标 / 属性 / 动作）。

交包结构定义 + 可重放脚本；说明服务端校验面与封号风险。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`

命令一律给 PowerShell / pwsh 形式（这台机器是 Windows），不交 bash / Linux 写法。
