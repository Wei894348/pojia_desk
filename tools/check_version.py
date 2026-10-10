#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_version.py —— 版本号四处一致性自检（只读）。

真值只有一个：仓库根的 VERSION。其余三处都是它的下游：

    VERSION                     ← 真值
    破甲一键通.py  VERSION = ...  ← 启动时读 VERSION 文件（缺失才用字面兜底）
    gui/renderer/index.html      ← 首屏占位版本（boot 后被桥的 meta 覆盖）
    gui/package.json             ← Electron 包版本
    README.md                    ← 正文里的 vX.Y

前两处对不上会"标题写一个版本、boot 完变另一个"；后两处对不上会打出名字带旧版本的包。
漂移过一次（README 停在旧版本、实盘已经是新的），所以这条留在 CI 里。

用法：
    python tools/check_version.py
"""

from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def read(path: str) -> str:
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read().replace("\r\n", "\n")


def main() -> int:
    problems = []

    ver_path = os.path.join(ROOT, "VERSION")
    if not os.path.isfile(ver_path):
        print("  [!] 缺少 VERSION 文件（版本号单一来源）")
        return 1
    version = read(ver_path).strip()

    main_py = os.path.join(ROOT, "engine", "破甲一键通.py")
    src = read(main_py)
    reads_file = "VERSION" in src and "_read_version" in src
    fallback = (re.search(r'return "([0-9][^"]*)"', src) or [None, None])[1]
    if not reads_file:
        problems.append("破甲一键通.py 没有从 VERSION 文件取版本（_read_version 不见了）")
    if fallback and fallback != version:
        problems.append("破甲一键通.py 的兜底版本 %s ≠ VERSION %s（单拷 .py 时会显示旧版本）"
                        % (fallback, version))

    html = os.path.join(ROOT, "gui", "renderer", "index.html")
    if os.path.isfile(html):
        got = (re.search(r'id="tbVersion"[^>]*>v([^<]+)<', read(html)) or [None, None])[1]
        if got != version:
            problems.append("gui/renderer/index.html 的占位版本 %s ≠ VERSION %s" % (got, version))
    else:
        problems.append("找不到 gui/renderer/index.html")

    pkg_path = os.path.join(ROOT, "gui", "package.json")
    if os.path.isfile(pkg_path):
        try:
            pkg_ver = json.loads(read(pkg_path)).get("version")
        except ValueError as e:
            problems.append("gui/package.json 不是合法 JSON：%s" % e)
            pkg_ver = None
        if pkg_ver != version:
            problems.append("gui/package.json 的 version %s ≠ VERSION %s" % (pkg_ver, version))

    readme = os.path.join(ROOT, "README.md")
    if os.path.isfile(readme):
        if ("<sub>v%s ·" % version) not in read(readme):
            problems.append("README.md 正文里没有 v%s（README 是生成产物，跑 tools/build_readme.py）" % version)

    if problems:
        print("版本一致性：%d 处不一致" % len(problems))
        for p in problems:
            print("  [!] %s" % p)
        print("  修法：python tools/build_readme.py --sync-version")
        return 1

    print("版本一致性：OK（VERSION = %s，四处下游全部一致）" % version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
