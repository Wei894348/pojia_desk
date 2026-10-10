---
name: game-il2cpp
description: >
  小类三拍 · IL2CPP（游戏工具）。命中关键词：IL2CPP dump global-metadata Il2CppDumper。
parent: GAME-TOOL-3
---

# IL2CPP · GAME-IL2CPP

类目：游戏工具（`GAME-TOOL-3`）　关键词：IL2CPP dump global-metadata Il2CppDumper

取 global-metadata.dat 与主二进制，跑 Il2CppDumper 拿符号与结构。

定位方法地址与字段偏移，处理加密 metadata。

交 dump 结果 + 方法指针表 + 调用示例（含初始化 API）。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`

命令一律给 PowerShell / pwsh 形式（这台机器是 Windows），不交 bash / Linux 写法。
