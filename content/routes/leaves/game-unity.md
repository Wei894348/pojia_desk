---
name: game-unity
description: >
  小类三拍 · Unity 逆向（游戏工具）。命中关键词：Unity 逆向 Assembly-CSharp mono il2cpp 资源包。
parent: GAME-TOOL-3
---

# Unity 逆向 · GAME-UNITY

类目：游戏工具（`GAME-TOOL-3`）　关键词：Unity 逆向 Assembly-CSharp mono il2cpp 资源包

判 mono 还是 il2cpp；mono 直接读 Assembly-CSharp.dll，il2cpp 走 metadata 解析。

定位玩法逻辑：字段、方法、Update 循环里的关键判断。

交反编译输出 + 关键方法原文 + 改法（补丁 / 转发 / hook）。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`

命令一律给 PowerShell / pwsh 形式（这台机器是 Windows），不交 bash / Linux 写法。
