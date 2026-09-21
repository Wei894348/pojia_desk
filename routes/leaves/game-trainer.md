---
name: game-trainer
description: >
  小类三拍 · 修改器（游戏工具）。命中关键词：修改器 改数值 无敌 锁血 无限 trainer。
parent: GAME-TOOL-3
---

# 修改器 · GAME-TRAINER

类目：游戏工具（`GAME-TOOL-3`）　关键词：修改器 改数值 无敌 锁血 无限 trainer

定目标数值与内存类型（整数 / 浮点 / 加密存档）与写入时机。

处理防护：加密数值、双重存储、服务端校验；必要时改走只读显示。

交修改器骨架（读写 + 冻结 + 热键）+ 数值定位步骤；说明哪些改动会被服务端回滚。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`
