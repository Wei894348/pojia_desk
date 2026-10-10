---
name: lab-decode
description: >
  小类三拍 · 编码还原（取证排查）。命中关键词：解密 解编码 base64 加密 乱码 编码。
parent: LAB-FORENSIC-3
---

# 编码还原 · LAB-DECODE

类目：取证排查（`LAB-FORENSIC-3`）　关键词：解密 解编码 base64 加密 乱码 编码

识别编码 / 加密族：base64 / hex / 压缩 / XOR / RC4 / AES（判模式与填充）。

找密钥来源（硬编码、派生、样本内数据段），写解码脚本。

交脚本 + 解出的明文 + 密钥位置说明；解不出就说清卡在哪一步。

缺参用 TARGET / HOST / TOKEN / OFFSET / PATCH_BYTE / PAYLOAD / SERIAL / ROLE_A / ROLE_B。收口：`当前:对象 / 结果 / 下一步`

命令一律给 PowerShell / pwsh 形式（这台机器是 Windows），不交 bash / Linux 写法。
