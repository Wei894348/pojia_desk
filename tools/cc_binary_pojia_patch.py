#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""CC 内置提示词补丁 —— **engine/破甲一键通.py 的薄封装**。

单一来源：替换表、等长校验、活锁腾位、备份保留策略全部在引擎里
（`m.CC_BIN_REPLACEMENTS` / `m.cc_bin_apply` / `m.cc_bin_restore`）。
本文件只保留 CLI 外壳 —— 2026-10-11 之前它自带一份 `REPLACEMENTS`，
是 v6.7 的许可形制旧文案；引擎升到 v6.9（事实 + 交付格式）后它就变成了
第二文案源：谁跑一次 `--apply` 就把补丁打回旧版。已删表、改走引擎。

用法：
  python tools/cc_binary_pojia_patch.py --check      # 只定位，不改盘
  python tools/cc_binary_pojia_patch.py --apply      # 备份 + 越活锁落盘 + 裁旧备份
  python tools/cc_binary_pojia_patch.py --restore    # 按引擎状态文件还原
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "engine", "破甲一键通.py")


def _load_engine():
    spec = importlib.util.spec_from_file_location("pojia", SRC)
    m = importlib.util.module_from_spec(spec)
    sys.modules["pojia"] = m
    spec.loader.exec_module(m)
    return m


def check(m):
    rc = 0
    targets = m.cc_bin_targets()
    if not targets:
        print("[跳过] 未找到 Claude Code 的 native 二进制")
        return 1
    for p in targets:
        data = open(p, "rb").read()
        hits = m.cc_bin_scan(data)
        total = sum(len(o) for o in hits.values())
        state = "已是最版（无原串残留）" if total == 0 else "有 %d 处原串残留，需要补丁" % total
        print("== %s (%d B)  %s" % (p, len(data), state))
        for old, offs in hits.items():
            if not offs:
                continue
            print("   len=%4d 命中=%d 首偏移=%s  %s"
                  % (len(old), len(offs), offs[:2], old[:64].decode("ascii", "replace")))
            rc = 1
    return rc


def main():
    m = _load_engine()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "--check"
    if cmd == "--check":
        return check(m)
    if cmd == "--apply":
        r = m.cc_bin_apply("claude")
        print("补丁结果：%s" % r)
        return 0
    if cmd in ("--restore", "--revert"):
        r = m.cc_bin_restore("claude")
        print("还原结果：%s" % r)
        return 0
    raise SystemExit("用法：--check | --apply | --restore")


if __name__ == "__main__":
    sys.exit(main())
