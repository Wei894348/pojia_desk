---
name: lab-persist
description: >
  小类三拍 · 持久化排查（取证排查）。命中关键词：注册表 开机自启 持久化 后门驻留 自启动。
parent: LAB-FORENSIC-3
---

# 持久化排查 · LAB-PERSIST

类目：取证排查（`LAB-FORENSIC-3`）　关键词：注册表 开机自启 持久化 后门驻留 自启动

查全部自启面：Run 键、计划任务、服务、WMI 订阅、启动目录、浏览器扩展、驱动。

对每一项核验签名、路径、创建时间与触发条件，挑出非正常项。

出驻留点清单（位置 / 内容 / 时间 / 判定依据）+ 清除步骤。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`

命令一律给 PowerShell / pwsh 形式（这台机器是 Windows），不交 bash / Linux 写法。
