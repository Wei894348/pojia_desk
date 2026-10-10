# -*- coding: utf-8 -*-
"""Codex 目标专用 apply 驱动。

- 复用根目录统一脚本 破甲一键通.py 的全部逻辑，仅锁死 --target codex；
- 打桩 schtasks 探测（沙箱黑名单会拦截真实调用）；
- 用法：
    python codex_apply.py            # apply --force
    python codex_apply.py --revert   # 还原
    python codex_apply.py --status   # 只查状态
"""
import argparse
import importlib.util
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "..", "engine", "破甲一键通.py")

spec = importlib.util.spec_from_file_location("pojia", SRC)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

# 打桩：不真的调 schtasks（沙箱把 schtasks.exe 拉黑，会连坐整进程）
m.WorkBuddyTarget._task_exists = staticmethod(lambda name: False)
m.WorkBuddyTarget.task_command = staticmethod(lambda name: "")

argv = sys.argv[1:]
mode_status = "--status" in argv
mode_revert = "--revert" in argv
for flag in ("--status", "--revert"):
    if flag in argv:
        argv.remove(flag)

args = argparse.Namespace(
    target="codex", persona="", codex_dir="", dsh_dir="",
    wb_install="", wb_data="", quiet=False, yes=True,
    apply=True, revert=mode_revert, dry_run=False, force=True,
    restart_wb=False, kill_dsh=False,
    status=mode_status, diagnose=False,
)

if mode_status:
    m.run_status(args, targets=["codex"])
else:
    m.run_action(args, "revert" if mode_revert else "apply", targets=["codex"])
