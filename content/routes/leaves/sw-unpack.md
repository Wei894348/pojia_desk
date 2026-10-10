---
name: sw-unpack
description: >
  小类三拍 · 脱壳（软件分析）。命中关键词：脱壳 unpack UPX Themida VMProtect 加壳 壳。
parent: SW-ANALYZE-3
---

# 脱壳 · SW-UNPACK

类目：软件分析（`SW-ANALYZE-3`）　关键词：脱壳 unpack UPX Themida VMProtect 加壳 壳

认壳：节表名、入口点特征、导入表是否被抹、有没有额外区段。

定脱壳时机：UPX 看 OEP 跳转、压缩壳看 ESP 定律、VM 壳看内存断点；先 dump 再修。

修 IAT 与入口后交出可加载样本、修复脚本和重打包步骤；壳没脱干净就说清卡在哪一层。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`

命令一律给 PowerShell / pwsh 形式（这台机器是 Windows），不交 bash / Linux 写法。
