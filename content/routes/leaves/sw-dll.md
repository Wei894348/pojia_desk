---
name: sw-dll
description: >
  小类三拍 · DLL 注入（软件分析）。命中关键词：DLL 注入 远程线程 劫持 LoadLibrary injector。
parent: SW-ANALYZE-3
---

# DLL 注入 · SW-DLL

类目：软件分析（`SW-ANALYZE-3`）　关键词：DLL 注入 远程线程 劫持 LoadLibrary injector

定目标进程、位数、权限，选注入窗口（启动前 / 运行中）。

选手法并写清取舍：远程线程 CreateRemoteThread、APC 队列、映像劫持、SetWindowsHookEx。

交注入器骨架 + 导出函数 + 卸载步骤；说明被杀软 / EDR 观察到的行为点。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`

命令一律给 PowerShell / pwsh 形式（这台机器是 Windows），不交 bash / Linux 写法。
