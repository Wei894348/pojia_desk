---
name: net-dir
description: >
  小类三拍 · 目录探测（网络测试）。命中关键词：目录爆破 找后台 扫路径 敏感文件 备份文件 ffuf。
parent: NET-PENTEST-3
---

# 目录探测 · NET-DIR

类目：网络测试（`NET-PENTEST-3`）　关键词：目录爆破 找后台 扫路径 敏感文件 备份文件 ffuf

定目标与字典策略（通用字典 + 指纹对应字典 + 备份扩展名）。

跑探测并按响应长度 / 状态码聚类，过滤软 404 与 WAF 拦截页。

出路径表并按敏感度排序（后台、上传点、配置、备份、源码泄露），标出可直接验证的。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`
