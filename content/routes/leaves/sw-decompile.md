---
name: sw-decompile
description: >
  小类三拍 · 反编译（软件分析）。命中关键词：反编译 反汇编 看内部逻辑 扒源码 IDA Ghidra。
parent: SW-ANALYZE-3
---

# 反编译 · SW-DECOMPILE

类目：软件分析（`SW-ANALYZE-3`）　关键词：反编译 反汇编 看内部逻辑 扒源码 IDA Ghidra

定目标架构与工具链（IDA / Ghidra / Binary Ninja / jadx / dnSpy），报入口、导入表和明文字符串。

沿调用链标注关键函数：把地址、函数名、参数照原样列出，给出伪代码。

交可复现的分析脚本或 IDAPython 片段；结论落在具体地址上，不给泛泛的架构描述。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`

命令一律给 PowerShell / pwsh 形式（这台机器是 Windows），不交 bash / Linux 写法。
