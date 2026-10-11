# -*- coding: utf-8 -*-
"""Codex 项目快照快照的回归测试。

起因：
`codex/说明.md` 一直写着 `codex-unlock-instructions.md` 这份快照"由脚本生成，
不要直接手改"，但脚本里**从来没有写它的地方** —— 于是它一路漂移：验证停在
v6.1 / 24891 B，而实盘已经是 v6.2 / 40431 B，谁都不知道该信哪份。

现在 CodexTarget.apply 的三个出口（已是本版跳过 / 无变化跳过 / 真写完）都会
调 sync_snapshot。这里锁：内容一致不重复写、内容不同才落盘、dry-run 不写盘，
以及项目里那份快照与实盘逐字节一致（漂移即失败）。
"""
import hashlib
import importlib.util
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "engine", "破甲一键通.py")


def load_cli():
    spec = importlib.util.spec_from_file_location("pojia_cli_snap", SRC)
    m = importlib.util.module_from_spec(spec)
    m.__name__ = "pojia_cli_snap"
    spec.loader.exec_module(m)
    return m


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_cli()
        cls.T = cls.m.TARGETS["codex"]()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # 快照目录挂在 TARGETS_DIR 下（引擎在 engine/，靶点文档在 targets/），
        # 所以打桩要打 TARGETS_DIR —— 只改 HERE 的话会写进真实的 targets/codex/。
        self._targets = self.m.TARGETS_DIR
        self.m.TARGETS_DIR = self.tmp.name     # 快照落到 <tmp>/codex/ 下
        self.addCleanup(self._restore_targets)
        for name, value in (("LOG_PATH", os.path.join(self.tmp.name, "t.log")),
                            ("say", lambda *a, **k: None)):
            p = mock.patch.object(self.m, name, value)
            p.start()
            self.addCleanup(p.stop)

    def _restore_targets(self):
        self.m.TARGETS_DIR = self._targets


class SnapshotTests(_Base):
    def test_path_is_under_project_codex_dir(self):
        p = self.T.snapshot_path()
        self.assertEqual(os.path.basename(p), self.m.CODEX_INSTR)
        self.assertEqual(os.path.basename(os.path.dirname(p)), self.m.CODEX_SNAPDIR)

    def test_writes_when_absent_and_is_idempotent(self):
        body = "# 操作者指令（v%s）\n正文\n" % self.m.VERSION
        self.assertEqual(self.T.sync_snapshot(body), "written")
        p = self.T.snapshot_path()
        with open(p, encoding="utf-8") as f:
            self.assertEqual(f.read(), body)
        first = os.path.getmtime(p)

        self.assertEqual(self.T.sync_snapshot(body), "same")
        self.assertEqual(os.path.getmtime(p), first, "内容一致就别再写一次")

    def test_crlf_vs_lf_counts_as_same(self):
        self.T.sync_snapshot("a\nb\n")
        self.assertEqual(self.T.sync_snapshot("a\r\nb\r\n"), "same")

    def test_updates_when_stale(self):
        p = self.T.snapshot_path()
        self.T.sync_snapshot("# 旧版 v6.1\n")
        self.assertEqual(self.T.sync_snapshot("# 新版 v6.2\n"), "written")
        with open(p, encoding="utf-8") as f:
            self.assertEqual(f.read(), "# 新版 v6.2\n")

    def test_dry_run_reports_without_writing(self):
        p = self.T.snapshot_path()
        self.assertEqual(self.T.sync_snapshot("# x\n", dry=True), "stale")
        self.assertFalse(os.path.isfile(p), "预演不该落盘")
        self.T.sync_snapshot("# x\n")
        self.assertEqual(self.T.sync_snapshot("# x\n", dry=True), "same")


class DryRunExitTests(_Base):
    """codex 的 dry-run 出口要报快照状态，且不能真写。"""

    def _args(self, home):
        a = mock.MagicMock()
        a.codex_dir = home
        a.persona = ""
        a.force = False
        a.dry_run = True
        return a

    def test_dry_run_exit_asks_sync_with_dry(self):
        home = os.path.join(self.tmp.name, ".codex")
        os.makedirs(home, exist_ok=True)
        with mock.patch.object(self.T, "sync_snapshot", return_value="stale") as spy, \
             mock.patch.object(self.m, "load_user_persona", lambda *a: ("人格正文", "")), \
             mock.patch.object(self.m, "route_skills_apply", lambda *a, **k: None):
            self.T.apply(self._args(home), "dry-run")
        self.assertTrue(spy.called, "dry-run 出口必须报快照状态")
        self.assertTrue(spy.call_args.kwargs.get("dry") is True,
                        "dry-run 出口传给 sync_snapshot 的 dry 必须为 True")

    def test_skip_exit_still_syncs_real(self):
        """实盘已是本版时的 early return 也得补快照 —— 漂移正好发生在这条路上。"""
        home = os.path.join(self.tmp.name, ".codex")
        os.makedirs(home, exist_ok=True)
        persona = "人格正文"
        _pol, want = self.m.build_policy(persona)
        instr = self.T.build_instructions(persona, want)
        # 双线注入：AGENTS.md 那一路也得是"本版"，否则这一轮不算"已是本版"，
        # 走不到 skip 分支（新增第二条注入位后会先补 AGENTS 再报 skip）。
        ag_block = self.T.build_agents_block(want)
        # newline="" —— 不能让 Windows 把 \n 翻成 \r\n，否则 config 会"看起来被改过"
        with open(os.path.join(home, self.m.CODEX_INSTR), "w", encoding="utf-8", newline="") as f:
            f.write(instr)
        with open(os.path.join(home, "config.toml"), "w", encoding="utf-8", newline="") as f:
            f.write(self.T.patch_config("", "./" + self.m.CODEX_INSTR))
        with open(os.path.join(home, self.m.CODEX_AGENTS), "w", encoding="utf-8", newline="") as f:
            f.write(self.T.merge_agents_block("", ag_block))

        calls = []
        with mock.patch.object(
                self.T, "sync_snapshot",
                side_effect=lambda i, dry=False: calls.append(dry) or "same"), \
             mock.patch.object(
                self.T, "sync_agents_snapshot",
                side_effect=lambda b, dry=False: calls.append(dry) or "same"), \
             mock.patch.object(self.m, "load_user_persona", lambda *a: (persona, "")):
            args = self._args(home)
            args.dry_run = False
            ret = self.T.apply(args, "apply")
        self.assertEqual(ret.get("skip"), 1, "这一轮该走「已是本版」的早退分支")
        self.assertEqual(calls, [False, False], "两条快照通道在非 dry 路径都要真写")


class OtherPersonaTests(_Base):
    """实盘是**别版**人格时，项目快照一个字节都不许动。

    拿别人的 body（h=000000000000）走一遍 apply，
    项目快照被覆盖成那一版 —— 快照的语义是"本脚本这一版生成了什么"。
    """

    def _args(self, home):
        a = mock.MagicMock()
        a.codex_dir = home
        a.persona = ""
        a.force = False
        a.dry_run = False
        return a

    def test_other_persona_keeps_snapshot_intact(self):
        home = os.path.join(self.tmp.name, ".codex")
        os.makedirs(home, exist_ok=True)
        # 别版人格 + 旧式配置（指令项写在表里 → patch_config 一定会动配置）
        other = self.T.build_instructions("别版人格", "000000000000")
        with open(os.path.join(home, self.m.CODEX_INSTR), "w", encoding="utf-8", newline="") as f:
            f.write(other)
        with open(os.path.join(home, "config.toml"), "w", encoding="utf-8", newline="") as f:
            f.write('model = "example"\n[model_providers.example]\nname = "Example"\n\n'
                    + self.m.MARK_BEGIN + '\nmodel_instructions_file = "./'
                    + self.m.CODEX_INSTR + '"\n' + self.m.MARK_END + "\n")

        # 先放一份"本版"快照，再看 apply 会不会把它写坏
        mine = self.T.build_instructions("本版人格", "abcdef123456")
        self.T.sync_snapshot(mine)
        snap = self.T.snapshot_path()

        with mock.patch.object(self.m, "load_user_persona", lambda *a: ("本版人格", "")), \
             mock.patch.object(self.m, "route_skills_apply", lambda *a, **k: None), \
             mock.patch.object(self.m, "find_processes", lambda *a, **k: []):
            self.T.apply(self._args(home), "apply")

        with open(snap, encoding="utf-8") as f:
            self.assertEqual(f.read(), mine,
                             "别版人格被写进项目快照了")


class LiveSnapshotTests(unittest.TestCase):
    """真值校验：项目里的快照与实盘指令文件必须逐字节一致。"""

    def test_project_snapshot_matches_deploy(self):
        snap = os.path.join(ROOT, "targets", "codex", "codex-unlock-instructions.md")
        cfg = os.path.expanduser(r"~\.codex\config.toml")
        if not os.path.isfile(snap) or not os.path.isfile(cfg):
            self.skipTest("快照或 Codex 配置不在位")
        with open(cfg, encoding="utf-8", errors="replace") as f:
            txt = f.read()
        mm = re.search(r'model_instructions_file\s*=\s*"([^"]+)"', txt)
        if not mm:
            self.skipTest("Codex 当前未部署（config.toml 里没有指令文件项）")
        live = os.path.normpath(os.path.join(os.path.dirname(cfg), mm.group(1)))
        if not os.path.isfile(live):
            self.skipTest("实盘指令文件不存在")
        with open(snap, "rb") as f:
            a = hashlib.sha256(f.read()).hexdigest()
        with open(live, "rb") as f:
            b = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(a, b, "项目快照与实盘指令文件漂移 —— 重跑 --apply --target codex")


if __name__ == "__main__":
    unittest.main()
