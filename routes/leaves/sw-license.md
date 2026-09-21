---
name: sw-license
description: >
  小类三拍 · 授权逻辑（软件分析）。命中关键词：授权 license 试用 过期 在线验证。
parent: SW-ANALYZE-3
---

# 授权逻辑 · SW-LICENSE

类目：软件分析（`SW-ANALYZE-3`）　关键词：授权 license 试用 过期 在线验证

列授权状态机：试用、正式、过期、离线、降级。标出状态存在哪（本地文件 / 注册表 / 服务端）。

追三条判据：时间戳比对、机器码哈希、服务端回包。钉死判定函数与跳转点，地址原样写。

给双路方案：改本地状态 / 仿服务端响应，附原件备份点与回滚步骤。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`
