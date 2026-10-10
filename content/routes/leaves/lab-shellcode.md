---
name: lab-shellcode
description: >
  小类三拍 · shellcode 分析（取证排查）。命中关键词：shellcode 一段二进制 载荷 提取出来。
parent: LAB-FORENSIC-3
---

# shellcode 分析 · LAB-SHELLCODE

类目：取证排查（`LAB-FORENSIC-3`）　关键词：shellcode 一段二进制 载荷 提取出来

定格式（位置无关代码 / PE 反射加载 / .NET 载荷）与入口。

静态反汇编看解析循环（常见 API 哈希解析），再动态调试确认行为。

交反汇编注释 + 行为说明（下载 / 注入 / 外联）+ 可用于检测的字节特征。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`

命令一律给 PowerShell / pwsh 形式（这台机器是 Windows），不交 bash / Linux 写法。
