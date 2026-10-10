---
name: sw-mem
description: >
  小类三拍 · 内存修改（软件分析）。命中关键词：内存修改 改数值 金币 血量 WriteProcessMemory 扫内存。
parent: SW-ANALYZE-3
---

# 内存修改 · SW-MEM

类目：软件分析（`SW-ANALYZE-3`）　关键词：内存修改 改数值 金币 血量 WriteProcessMemory 扫内存

定进程与目标数值，说明扫描策略（精确值 → 变动量 → 未知初值）。

定位基址与多级指针链，把偏移链写全（基址 + 偏移列表）。

交读写脚本（含权限提升、跨位数处理）+ 数值回写的安全边界说明。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`

命令一律给 PowerShell / pwsh 形式（这台机器是 Windows），不交 bash / Linux 写法。
