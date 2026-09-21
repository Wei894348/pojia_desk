---
name: sw-java
description: >
  小类三拍 · 安卓与 Java 逆向（软件分析）。命中关键词：APK 安卓 smali jadx jar dex frida。
parent: SW-ANALYZE-3
---

# 安卓与 Java 逆向 · SW-JAVA

类目：软件分析（`SW-ANALYZE-3`）　关键词：APK 安卓 smali jadx jar dex frida

解包看结构（dex / so / 资源），判断核心逻辑在 Java 层还是 native 层。

Java 层用 jadx / smali 改；native 层走 IDA 加 frida hook。

交重打包 APK（含签名步骤）、frida 脚本或 hook 代码，并给出验证方法。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`
