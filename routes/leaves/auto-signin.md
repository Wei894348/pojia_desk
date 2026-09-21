---
name: auto-signin
description: >
  小类三拍 · 自动签到（脚本自动化）。命中关键词：自动签到 打卡 自动领 每日任务。
parent: AUTO-PIPE-3
---

# 自动签到 · AUTO-SIGNIN

类目：脚本自动化（`AUTO-PIPE-3`）　关键词：自动签到 打卡 自动领 每日任务

抓签到请求：接口、鉴权头、必要参数（时间戳 / 签名 / 设备）。

处理 token 续期与签名生成，写成幂等（重复跑不报错）。

交脚本 + 定时接入方式 + 结果通知。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`
