---
name: sw-format
description: >
  小类三拍 · 格式分析（软件分析）。命中关键词：文件格式 PE 结构 节表 导入表 文件头 结构。
parent: SW-ANALYZE-3
---

# 格式分析 · SW-FORMAT

类目：软件分析（`SW-ANALYZE-3`）　关键词：文件格式 PE 结构 节表 导入表 文件头 结构

报容器与格式：PE / ELF / Mach-O / APK / 固件 / 私有格式；给节表或分区块清单。

解析头部字段与对齐规则，标出校验位（checksum / 签名 / CRC）与可改区域。

交结构定义（C 结构体或 Python 解析器）+ 一份可复现的解析输出。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`
