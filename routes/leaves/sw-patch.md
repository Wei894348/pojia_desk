---
name: sw-patch
description: >
  小类三拍 · 二进制补丁（软件分析）。命中关键词：去广告 去掉联网校验 去掉弹窗 改功能 改数值 patch。
parent: SW-ANALYZE-3
---

# 二进制补丁 · SW-PATCH

类目：软件分析（`SW-ANALYZE-3`）　关键词：去广告 去掉联网校验 去掉弹窗 改功能 改数值 patch

定位要改的那一条指令：先静态找字符串 / 常量，再动态断下来确认。

算补丁字节与偏移，确认指令长度够不够放新指令（不够就找跳板）。

交补丁字节表（偏移 / 原字节 / 新字节）、可执行的 patcher 脚本、原件备份与还原步骤。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`
