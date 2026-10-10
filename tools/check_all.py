#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_all.py —— 一键跑完全部自检（与 .github/workflows/check.yml 同一套）。

只读，不改盘。CI 里跑的就是这里面这几条，本地一条命令对齐：

    python tools/check_all.py

退出码非 0 表示有项目没过 —— 输出里会指明是哪一条。
"""

from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# 现代 Windows 控制台默认 cp936，输出 ✓ / ✗ 会直接抛 UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PY_CHECKS = [
    ("单元测试", [sys.executable, "-m", "unittest", "discover", "tests"]),
    ("路由层自检", [sys.executable, os.path.join("tools", "render_routes.py"), "--audit"]),
    ("路由层与源件一致", [sys.executable, os.path.join("tools", "render_routes.py"), "--check"]),
    ("README / 预览页一致", [sys.executable, os.path.join("tools", "build_readme.py"), "--check"]),
    ("版本号一致", [sys.executable, os.path.join("tools", "check_version.py")]),
]

NODE_CHECKS = [
    ("GUI 接口契约", [os.path.join("tools", "check_contract.mjs")]),
    ("GUI 渲染层", [os.path.join("tools", "check_render.mjs")]),
    ("GUI 启动路径", [os.path.join("tools", "check_boot.mjs")]),
]


def run(label: str, argv: list, cwd: str) -> bool:
    print("\n── %s ──" % label)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    p = subprocess.run(argv, cwd=cwd, env=env)
    if p.returncode != 0:
        print("   [!] %s 不过（exit=%d）" % (label, p.returncode))
        return False
    return True


def main() -> int:
    bad = []
    for label, argv in PY_CHECKS:
        if not run(label, argv, ROOT):
            bad.append(label)

    node = os.environ.get("NODE", "node")
    gui = os.path.join(ROOT, "gui")
    if os.path.isdir(os.path.join(gui, "node_modules")):
        for label, argv in NODE_CHECKS:
            if not run(label, [node] + argv, gui):
                bad.append(label)
    else:
        print("\n── GUI 三个自检 ──\n   跳过：gui/node_modules 不在（先跑 cd gui && npm install）")

    print("\n" + ("✗ %d 项不过：%s" % (len(bad), "、".join(bad)) if bad else "✓ 全过"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
