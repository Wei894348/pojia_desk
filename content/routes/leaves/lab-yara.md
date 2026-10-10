---
name: lab-yara
description: >
  小类三拍 · YARA 规则（取证排查）。命中关键词：YARA 规则 特征提取 规则匹配。
parent: LAB-FORENSIC-3
---

# YARA 规则 · LAB-YARA

类目：取证排查（`LAB-FORENSIC-3`）　关键词：YARA 规则 特征提取 规则匹配

从样本里挑稳定特征：独特字符串、字节序列、结构标记，避开易变部分。

写规则（strings + condition），加 wildcard 与偏移约束降低误报。

交规则文件 + 扫描命令 + 误报测试结果（对正常样本批量验证）。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`

命令一律给 PowerShell / pwsh 形式（这台机器是 Windows），不交 bash / Linux 写法。
