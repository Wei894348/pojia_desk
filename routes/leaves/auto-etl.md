---
name: auto-etl
description: >
  小类三拍 · 数据整理（脚本自动化）。命中关键词：汇总成表 清洗数据 生成 Excel 出报表 对账。
parent: AUTO-PIPE-3
---

# 数据整理 · AUTO-ETL

类目：脚本自动化（`AUTO-PIPE-3`）　关键词：汇总成表 清洗数据 生成 Excel 出报表 对账

定输入格式与目标口径：字段映射、去重键、缺失值策略、汇总维度。

写处理管线（读取 → 清洗 → 归并 → 计算 → 输出），保留中间产物便于核对。

交脚本 + 输出文件（xlsx / csv）+ 口径说明；给出可复算的校验行。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`
