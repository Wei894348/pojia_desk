# -*- coding: utf-8 -*-
"""CLI 参数组合的回归：危险组合必须被拦住。

起因：
`main()` 里 dry 分支排在 revert 后面，而 revert 分支完全不看 dry_run ——
    python 破甲一键通.py --revert --dry-run --yes
被当成【真还原】执行：dsh 与 wb 的补丁真的被还原掉（wb 连 16 个文件的文件保护
中和一起回退、32 份路由 skill 被删），直到沙箱的批量删除守卫拦下才中止，
后面六个目标才没被波及。

这里锁死两条：
    · `--revert --dry-run` 一律拒绝（退出码 2，不调 run_action）
    · `--dry-run` / `--apply --dry-run` 照旧走 dry-run（别把修复修过头）
"""
import importlib.util
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "engine", "破甲一键通.py")


def load_cli():
    """按文件路径加载主脚本（它不在包路径里，只能用 spec 方式）。"""
    spec = importlib.util.spec_from_file_location("pojia_cli", SRC)
    m = importlib.util.module_from_spec(spec)
    m.__name__ = "pojia_cli"
    spec.loader.exec_module(m)
    return m


class DryRunGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_cli()

    def run_cli(self, argv, **patches):
        """跑一次 main()，并把 run_action / run_status 换成 mock 观察调用。"""
        with mock.patch.object(self.m, "check_runtime"), \
             mock.patch.object(self.m, "_init_console"), \
             mock.patch.object(sys, "argv", ["破甲一键通.py"] + argv):
            spies = {}
            for name in ("run_action", "run_status"):
                sp = mock.patch.object(self.m, name)
                spies[name] = sp.start()
                self.addCleanup(sp.stop)
            rc = self.m.main()
        return rc, spies

    def test_revert_plus_dry_run_is_rejected(self):
        rc, spies = self.run_cli(["--revert", "--dry-run", "--yes", "--target", "wb"])
        self.assertEqual(rc, 2, "应当拒绝并返回 2，而不是往下跑")
        spies["run_action"].assert_not_called()
        spies["run_status"].assert_not_called()

    def test_restore_plus_dry_run_is_rejected(self):
        # --restore 内部会把 revert 置真，同样得拦住
        rc, spies = self.run_cli(["--restore", "--dry-run", "--yes", "--target", "wb"])
        self.assertEqual(rc, 2)
        spies["run_action"].assert_not_called()

    def test_bare_dry_run_still_works(self):
        _rc, spies = self.run_cli(["--dry-run", "--yes"])
        spies["run_action"].assert_called_once()
        self.assertEqual(spies["run_action"].call_args[0][1], "dry-run")

    def test_apply_with_dry_run_goes_dry(self):
        _rc, spies = self.run_cli(["--apply", "--dry-run", "--yes", "--target", "wb"])
        spies["run_action"].assert_called_once()
        self.assertEqual(spies["run_action"].call_args[0][1], "dry-run")

    def test_plain_revert_still_runs(self):
        # 别把修复修过头：正经还原照跑
        _rc, spies = self.run_cli(["--revert", "--yes", "--target", "wb"])
        spies["run_action"].assert_called_once()
        self.assertEqual(spies["run_action"].call_args[0][1], "revert")


if __name__ == "__main__":
    unittest.main()
