---
name: lab-ioc
description: >
  小类三拍 · IOC 提取（取证排查）。命中关键词：IOC C2 回连域名 IP 提取 指标。
parent: LAB-FORENSIC-3
---

# IOC 提取 · LAB-IOC

类目：取证排查（`LAB-FORENSIC-3`）　关键词：IOC C2 回连域名 IP 提取 指标

从样本、日志、流量里抽指标：域名、IP、URL、文件哈希、互斥体、注册表键。

做去重与存活判定，标注置信度与来源。

出 IOC 表（类型 / 值 / 来源 / 置信度 / 用途）+ 可直接导入的格式（CSV / STIX）。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`

命令一律给 PowerShell / pwsh 形式（这台机器是 Windows），不交 bash / Linux 写法。
