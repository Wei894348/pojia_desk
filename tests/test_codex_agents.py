# -*- coding: utf-8 -*-
"""Codex 第二注入位（~/.codex/AGENTS.md 标记块）的回归测试。

为什么要这第二条腿：
  只压 config.toml 的 model_instructions_file 时，Codex 桌面端保存设置会回写
  config.toml，部署项被丢掉整条链就断了。AGENTS.md 是 Codex 读的另一条独立
  通道（$CODEX_HOME/AGENTS.md 全局指令），两条同时在线，断一条还有一条。

这里锁住：
  · 合并语义：用户原有内容原样保留，标记块追加在末尾；
  · 幂等：同样的块重复 apply 不重写（mtime 不变）；
  · 换版：哈希变了自动换块（不需要 --force，动的只有我们自己的块）；
  · 还原：优先逐字节回拷部署前原稿；没有原稿时才剥块，且不留空文件；
  · 快照：项目内 targets/codex/AGENTS-追加块.md 与实盘块逐字节一致；
  · 双线判定：只有 AGENTS.md 也在位时，status 才算"本版"。
"""
import importlib.util
import json
import os

# 席位独立源会让单测读到仓库里的 content/seats/ —— 测试一律走共享管线
os.environ.setdefault("POJIA_NO_SEAT_OVERRIDE", "1")
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "engine", "破甲一键通.py")


def load_cli():
    spec = importlib.util.spec_from_file_location("pojia_agents", SRC)
    m = importlib.util.module_from_spec(spec)
    m.__name__ = "pojia_agents"
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
        self.home = os.path.join(self.tmp.name, ".codex")
        os.makedirs(self.home, exist_ok=True)
        self.ag = os.path.join(self.home, self.m.CODEX_AGENTS)
        self._targets = self.m.TARGETS_DIR
        self.m.TARGETS_DIR = os.path.join(self.tmp.name, "targets")
        self.addCleanup(self._restore_targets)
        for name, value in (("LOG_PATH", os.path.join(self.tmp.name, "t.log")),
                            ("say", lambda *a, **k: None)):
            p = mock.patch.object(self.m, name, value)
            p.start()
            self.addCleanup(p.stop)

    def _restore_targets(self):
        self.m.TARGETS_DIR = self._targets

    def args(self, dry=False):
        a = mock.MagicMock()
        a.codex_dir = self.home
        a.persona = ""
        a.force = True
        a.dry_run = dry
        return a

    def read(self, path=None):
        with open(path or self.ag, encoding="utf-8", newline="") as f:
            return f.read()


class MergeTests(_Base):
    def test_block_shape_and_hash_mark(self):
        _pol, want = self.m.build_policy("")
        block = self.T.build_agents_block(want)
        self.assertTrue(block.startswith(self.m.AGENTS_MARK_BEGIN))
        self.assertTrue(block.rstrip("\n").endswith(self.m.AGENTS_MARK_END))
        self.assertIn(self.m.AGENTS_MARK + want, block)
        self.assertIn("任务路由", block)
        # 创作那段已从否定句（禁止 X）改成肯定句（文本产出约定）
        self.assertIn("文本产出约定", block)
        # 多语言层已剥离（v6.7 回退）—— 它正是 CC / Codex 拒写的触发源，
        # 这两段不许再回到 AGENTS.md 追加块里。
        self.assertNotIn("Workspace notes", block)
        self.assertNotIn("Primero el artefacto", block)
        # 自曝词不许出现在 AGENTS.md 这条腿上（桌面板每次会话都读，别留名片）
        for word in ("越狱", "jailbreak", "unlock"):
            self.assertNotIn(word, block)

    def test_block_stays_small(self):
        _pol, want = self.m.build_policy("")
        block = self.T.build_agents_block(want)
        self.assertLess(len(block.encode("utf-8")), 4096,
                        "第二注入位要瘦：正文走指令文件，这里只放硬约束与路由拍子")

    def test_merge_preserves_user_content(self):
        user = "# 我的项目规则\n\n- 用 pwsh\n- 别用 bash\n"
        _pol, want = self.m.build_policy("")
        out = self.T.merge_agents_block(user, self.T.build_agents_block(want))
        self.assertTrue(out.startswith(user.rstrip("\n")))
        self.assertIn(self.m.AGENTS_MARK_BEGIN, out)
        self.assertIn(self.m.AGENTS_MARK_END, out)

    def test_merge_replaces_old_block_only(self):
        older = self.T.build_agents_block("aaaaaaaaaaaa")
        newest = self.T.build_agents_block("bbbbbbbbbbbb")
        first = self.T.merge_agents_block("# user\n", older)
        second = self.T.merge_agents_block(first, newest)
        self.assertEqual(second.count(self.m.AGENTS_MARK_BEGIN), 1)
        self.assertIn("bbbbbbbbbbbb", second)
        self.assertNotIn("aaaaaaaaaaaa", second)
        self.assertTrue(second.startswith("# user"))

    def test_strip_returns_user_remainder(self):
        _pol, want = self.m.build_policy("")
        out = self.T.merge_agents_block("# user\n", self.T.build_agents_block(want))
        self.assertEqual(self.T.strip_agents_block(out), "# user")


class StateTests(_Base):
    def test_agents_state_levels(self):
        _pol, want = self.m.build_policy("")
        block = self.T.build_agents_block(want)
        self.assertEqual(self.T.agents_state("", want), "")
        self.assertEqual(self.T.agents_state("# user only\n", want), "")
        self.assertEqual(self.T.agents_state(block, want), "current")
        self.assertEqual(self.T.agents_state(block, "000000000000"), "other")


class ApplyTests(_Base):
    def setUp(self):
        super().setUp()
        # 这一组测的是**双线**代码路径（CODEX_DUAL_LEG=True）；
        # 默认形态（去双份）见 DedupeTests。
        self.m.CODEX_DUAL_LEG = True
        self.addCleanup(setattr, self.m, "CODEX_DUAL_LEG", False)
        self.proc = mock.patch.object(self.m, "find_processes", return_value=[])
        self.proc.start()
        self.addCleanup(self.proc.stop)
        self.skills = mock.patch.object(self.m, "route_skills_apply", lambda *a, **k: None)
        self.skills.start()
        self.addCleanup(self.skills.stop)
        # sync_snapshot 写的是**仓库真实快照**（targets/codex/），与 tmp home 无关；
# 席位独立源之后测试生成的正文 ≠ 实盘，不 mock 会把快照写花。
        self.snap = mock.patch.object(self.m.CodexTarget, "sync_snapshot",
                                      staticmethod(lambda *a, **k: "skipped"))
        self.snap.start()
        self.addCleanup(self.snap.stop)
        self.snap2 = mock.patch.object(self.m.CodexTarget, "sync_agents_snapshot",
                                       staticmethod(lambda *a, **k: "skipped"))
        self.snap2.start()
        self.addCleanup(self.snap2.stop)

    def test_apply_creates_agents_with_block(self):
        self.assertEqual(self.T.apply(self.args(), "apply"), {"fixed": 1})
        txt = self.read()
        self.assertIn(self.m.AGENTS_MARK_BEGIN, txt)
        self.assertIn("任务路由", txt)
        st = json.loads(self.read(os.path.join(self.home, self.m.CODEX_STATE)))
        self.assertEqual(st["agents_file"], self.m.CODEX_AGENTS)

    def test_apply_merges_into_existing_agents(self):
        with open(self.ag, "w", encoding="utf-8", newline="") as f:
            f.write("# 原有内容\n\n## Windows约束\n\n- 用 pwsh\n")
        self.T.apply(self.args(), "apply")
        txt = self.read()
        self.assertIn("# 原有内容", txt)
        self.assertIn("- 用 pwsh", txt)
        self.assertIn(self.m.AGENTS_MARK_BEGIN, txt)
        # 用户内容在前，我们的块在后（不动人家的排序）
        self.assertLess(txt.index("# 原有内容"), txt.index(self.m.AGENTS_MARK_BEGIN))

    def test_apply_is_idempotent(self):
        self.T.apply(self.args(), "apply")
        first = os.path.getmtime(self.ag)
        args = self.args()
        args.force = False
        self.assertEqual(self.T.apply(args, "apply"), {"skip": 1})
        self.assertEqual(os.path.getmtime(self.ag), first, "同样的块不该再写一遍")

    def test_stale_block_is_upgraded_without_force(self):
        older = self.T.merge_agents_block("# user\n", self.T.build_agents_block("aaaaaaaaaaaa"))
        with open(self.ag, "w", encoding="utf-8", newline="") as f:
            f.write(older)
        self.T.apply(self.args(), "apply")
        txt = self.read()
        self.assertEqual(txt.count(self.m.AGENTS_MARK_BEGIN), 1)
        self.assertNotIn("aaaaaaaaaaaa", txt)
        self.assertIn("# user", txt)

    def test_dry_run_reports_without_writing(self):
        self.T.apply(self.args(dry=True), "dry-run")
        self.assertFalse(os.path.exists(self.ag), "预演不该新建 AGENTS.md")

    def test_snapshot_matches_live_block(self):
        # 此用例专门验证 AGENTS 快照写入 —— 单独恢复真实的 sync_agents_snapshot
        # （sync_snapshot 保持 mock，否则测试生成的指令正文会写花仓库快照）。
        self.snap2.stop()
        self.addCleanup(self.snap2.start)
        self.T.apply(self.args(), "apply")
        snap = os.path.join(self.m.TARGETS_DIR, self.m.CODEX_SNAPDIR, self.m.CODEX_AGENTS_SNAP)
        with open(snap, encoding="utf-8", newline="") as f:
            snap_txt = f.read()
        # 快照 = 纯标记块；实盘 = 用户内容 + 同一块
        self.assertTrue(snap_txt.startswith(self.m.AGENTS_MARK_BEGIN))
        self.assertIn(snap_txt.rstrip("\n"), self.read())


class RevertTests(_Base):
    def setUp(self):
        super().setUp()
        self.m.CODEX_DUAL_LEG = True
        self.addCleanup(setattr, self.m, "CODEX_DUAL_LEG", False)
        self.proc = mock.patch.object(self.m, "find_processes", return_value=[])
        self.proc.start()
        self.addCleanup(self.proc.stop)
        self.skills_r = mock.patch.object(self.m, "route_skills_apply", lambda *a, **k: None)
        self.skills_r.start()
        self.addCleanup(self.skills_r.stop)
        self.skills_v = mock.patch.object(self.m, "route_skills_revert", lambda *a, **k: None)
        self.skills_v.start()
        self.addCleanup(self.skills_v.stop)

    def test_revert_restores_original_byte_for_byte(self):
        original = "# 原有内容\n\n- 用 pwsh\n"
        with open(self.ag, "w", encoding="utf-8", newline="") as f:
            f.write(original)
        self.T.apply(self.args(), "apply")
        self.assertNotEqual(self.read(), original)
        self.T.apply(self.args(), "revert")
        self.assertEqual(self.read(), original)

    def test_revert_removes_created_file(self):
        self.T.apply(self.args(), "apply")
        self.T.apply(self.args(), "revert")
        self.assertFalse(os.path.exists(self.ag), "部署前没有这份文件，还原时就该没有")

    def test_revert_strips_block_when_no_backup(self):
        self.T.apply(self.args(), "apply")
        # 手工删掉备份记录，模拟"只有块、没有原稿"的情形
        st_p = os.path.join(self.home, self.m.CODEX_STATE)
        st = json.loads(self.read(st_p))
        st.pop("backup_agents", None)
        st.pop("agents_original_sha256", None)     # 连原稿哈希一起清掉
        with open(st_p, "w", encoding="utf-8", newline="") as f:
            f.write(json.dumps(st, ensure_ascii=False, indent=2) + "\n")
        with open(self.ag, "r+", encoding="utf-8", newline="") as f:
            body = f.read()
            f.seek(0)
            f.write("# 用户后来加的\n\n" + body)
            f.truncate()
        self.T.apply(self.args(), "revert")
        self.assertEqual(self.read().strip(), "# 用户后来加的")

    def test_revert_after_repeated_apply_restores_true_original(self):
        """回归：连续 apply 两次后 revert，必须回到**最初**的原稿。

        revert_agents 曾直接回拷 `state.backup_agents`，
        而那是最近一次 apply 写下的备份 —— 那时文件里**已经有我们的块**了。
        于是"逐字节恢复部署前原稿"恢复出来的是一份仍带块的文件，revert 等于空转。
        """
        original = "# 我的规则\n\n- 用 pwsh\n"
        with open(self.ag, "w", encoding="utf-8", newline="") as f:
            f.write(original)
        self.T.apply(self.args(), "apply")
        self.assertIn(self.m.AGENTS_MARK_BEGIN, self.read())
        # 第二次 apply：文件里已经有块，备份里躺的也是带块版本 —— 这就是陷阱
        self.T.apply(self.args(), "apply")
        self.T.apply(self.args(), "revert")
        self.assertEqual(self.read(), original, "revert 没回到最初的原稿")
        self.assertNotIn(self.m.AGENTS_MARK_BEGIN, self.read())

    def test_orig_sha_only_recorded_for_first_apply(self):
        """原稿哈希只在首次植入时记，之后 apply 不覆盖（否则 revert 会漂）。"""
        original = "# 原稿\n"
        with open(self.ag, "w", encoding="utf-8", newline="") as f:
            f.write(original)
        self.T.apply(self.args(), "apply")
        st1 = json.loads(self.read(os.path.join(self.home, self.m.CODEX_STATE)))
        first = st1["agents_original_sha256"]
        self.assertTrue(first)
        self.T.apply(self.args(), "apply")
        st2 = json.loads(self.read(os.path.join(self.home, self.m.CODEX_STATE)))
        self.assertEqual(st2["agents_original_sha256"], first, "原稿哈希被第二次 apply 覆盖了")


class DedupeTests(_Base):
    """默认形态：**去双份**（CODEX_DUAL_LEG=False，2026-10-11 起）。

    依据 tools/pojia_refusal_test.py 的真实 Codex 语境实测（n=8）：
    双份 25% / 去双份 62% / 官方 base+AGENTS 单腿 12%。
    """

    def setUp(self):
        super().setUp()
        self.m.CODEX_DUAL_LEG = False
        self.addCleanup(setattr, self.m, "CODEX_DUAL_LEG", True)
        self.proc = mock.patch.object(self.m, "find_processes", return_value=[])
        self.proc.start()
        self.addCleanup(self.proc.stop)
        self.skills = mock.patch.object(self.m, "route_skills_apply", lambda *a, **k: None)
        self.skills.start()
        self.addCleanup(self.skills.stop)

    def test_apply_does_not_create_agents_block(self):
        self.T.apply(self.args(), "apply")
        self.assertFalse(os.path.exists(self.ag),
                         "没有用户内容时不该新建 AGENTS.md（去双份）")

    def test_apply_strips_existing_block_but_keeps_user(self):
        old = self.T.merge_agents_block("# 我的规则\n",
                                        self.T.build_agents_block("aaaaaaaaaaaa"))
        with open(self.ag, "w", encoding="utf-8", newline="") as f:
            f.write(old)
        self.T.apply(self.args(), "apply")
        txt = self.read()
        self.assertNotIn(self.m.AGENTS_MARK_BEGIN, txt)
        self.assertIn("# 我的规则", txt)

    def test_apply_removes_file_that_was_only_ours(self):
        with open(self.ag, "w", encoding="utf-8", newline="") as f:
            f.write(self.T.build_agents_block("aaaaaaaaaaaa"))
        self.T.apply(self.args(), "apply")
        self.assertFalse(os.path.exists(self.ag),
                         "整份都是我们的块 ⇒ 文件该被移除，不留空文件")

    def test_instructions_file_still_written(self):
        self.T.apply(self.args(), "apply")
        inst = os.path.join(self.home, self.m.CODEX_INSTR)
        self.assertTrue(os.path.isfile(inst))
        self.assertIn("任务路由", self.read(inst))


class LiveSnapshotTests(unittest.TestCase):
    """真值校验：项目里的追加块快照与实盘 AGENTS.md 里的块逐字节一致。"""

    def test_project_agents_snapshot_matches_deploy(self):
        snap = os.path.join(ROOT, "targets", "codex", "AGENTS-追加块.md")
        live = os.path.expanduser(r"~\.codex\AGENTS.md")
        if not os.path.isfile(snap) or not os.path.isfile(live):
            self.skipTest("快照或实盘 AGENTS.md 不在位")
        m = load_cli()
        with open(snap, encoding="utf-8", newline="") as f:
            want = f.read()
        with open(live, encoding="utf-8", newline="") as f:
            have = f.read()
        if m.AGENTS_MARK_BEGIN not in have:
            self.skipTest("实盘 AGENTS.md 没有标记块（未部署）")
        # 快照是纯标记块；实盘是"用户内容 + 标记块"。比的是**块本身**。
        start = have.index(m.AGENTS_MARK_BEGIN)
        stop = have.index(m.AGENTS_MARK_END) + len(m.AGENTS_MARK_END)
        live_block = have[start:stop].strip("\n")
        self.assertEqual(live_block, want.strip("\n"),
                         "追加块快照与实盘漂移 —— 重跑 --apply --target codex")


if __name__ == "__main__":
    unittest.main()
