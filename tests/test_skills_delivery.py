# -*- coding: utf-8 -*-
"""路由 skills 投放通道的回归测试（pojia_skills + 主脚本的 route_skills_apply）。

起因：

1. **写口类型不匹配**：`route_skills_apply` 传的是 `tx_write_bytes`，而
   `pojia_skills.install` 交给写口的是 **str**。它之所以一直没炸，是因为
   `pojia_tx` 的 `session.write()` 内部会把 str 编码（第 285 行）—— 也就是
   **只有走在事务外**的调用才会 `TypeError: a bytes-like object is required,
   not 'str'`。这条旁路一旦有人用（直接调目标方法、调试、写新入口）就踩雷。
   修法：给 install 传文本写口 `write_text`，两边接口对齐。
   配套：把 except 分支从黄字改成红字并注明"这是脚本 bug"，避免被当成
   "这次没投上"糊过去。

2. **父类目 slug 当成 key 用**：`_parent_slugs()` 返回 slug（`auto-pipe`），
   但 `render_parent_skill()` 收的是键（`auto`）→ 只要目标目录里出现过父类目，
   `status()` 就 `KeyError: 'auto-pipe'` 直接崩。这个是**真崩**，与事务无关。

全程在临时目录里跑，不碰真实目标。
"""
import importlib.util
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


class SkillsBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.S = _load("pojia_skills_t", os.path.join(ROOT, "engine", "pojia_skills.py"))
        cls.R = cls.S.R
        cls.m = _load("pojia_cli_sk", os.path.join(ROOT, "engine", "破甲一键通.py"))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "seat")
        os.makedirs(self.home, exist_ok=True)
        p = mock.patch.object(self.m, "LOG_PATH", os.path.join(self.tmp.name, "t.log"))
        p.start()
        self.addCleanup(p.stop)
        p2 = mock.patch.object(self.m, "say", lambda *a, **k: None)
        p2.start()
        self.addCleanup(p2.stop)


class WritePortTests(SkillsBase):
    def test_install_hands_str_to_write_fn(self):
        """写口收到的必须是 str —— 这就是 tx_write_bytes 那个接口不一致的判据。"""
        seen = []

        def port(path, text, make_dirs=False):
            seen.append((path, type(text).__name__))
            d = os.path.dirname(path)
            if make_dirs and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            with open(path, "wb") as f:
                f.write(text.encode("utf-8"))

        res = self.S.install("codex", port, home=self.home)
        self.assertEqual(res["total"], 86)
        self.assertEqual(len(res["written"]), 86)
        self.assertTrue(all(t == "str" for _p, t in seen),
                        "写口拿到的不是 str：%r" % (set(t for _p, t in seen),))

    def test_route_skills_apply_can_write_from_scratch(self):
        """**事务外**调用也得能真投进去（修前这里是 None + 一行黄字）。"""
        res = self.m.route_skills_apply("codex", self.home, "codex")
        self.assertIsNotNone(res, "投放返回 None —— 写口又不匹配了")
        self.assertEqual(len(res["written"]), 86)
        self.assertTrue(os.path.isfile(
            os.path.join(self.home, "skills", "sw-analyze", "SKILL.md")))

    def test_route_skills_apply_is_idempotent(self):
        self.m.route_skills_apply("codex", self.home, "codex")
        again = self.m.route_skills_apply("codex", self.home, "codex")
        self.assertEqual(again["written"], [])
        self.assertEqual(len(again["skipped"]), 86)

    def test_route_skills_apply_dry_writes_nothing(self):
        res = self.m.route_skills_apply("codex", self.home, "codex", dry=True)
        self.assertEqual(res, {"dry": 86})
        self.assertFalse(os.path.isdir(os.path.join(self.home, "skills")))

    def test_route_skills_apply_skips_missing_home(self):
        self.assertIsNone(self.m.route_skills_apply("codex", "", "codex"))
        self.assertIsNone(self.m.route_skills_apply(
            "codex", os.path.join(self.tmp.name, "nope"), "codex"))

    def test_route_skills_apply_reports_write_failure_loudly(self):
        """写口炸了：返回 None，而且必须是红字 + 明说"这是脚本 bug"。

        以前这里是黄字一行，导致"写口类型不对"这种硬 bug 被当成"这次没投上"。
        """
        calls = []
        with mock.patch.object(self.m, "write_text",
                               side_effect=TypeError("boom")), \
             mock.patch.object(self.m, "log",
                               side_effect=lambda msg, color="", target="", echo=True:
                               calls.append((msg, color))):
            res = self.m.route_skills_apply("codex", self.home, "codex")
        self.assertIsNone(res)
        self.assertTrue(calls, "失败必须留痕")
        msg, color = calls[0]
        self.assertEqual(color, "red")
        self.assertIn("脚本 bug", msg)
        self.assertIn("boom", msg)

    def test_route_skills_apply_skips_unsupported_seat(self):
        """没有 skills 通道的席位：一个字节都不写（返回空统计或 None 都算跳过）。

        起 `dsh` **不再**是"无通道"席位 —— DSH 的 `skill-filesystem`
        插件内置就扫 `<DSH_HOME>/skills`（依据 dsh-skill-filesystem/lib/index.js
        → join(this.dshHome, "skills")），所以它跟别的席位一样要投。
        这里改用**真正不存在**的键来测跳过分支，另加
        test_dsh_has_skills_channel 守住新行为。
        """
        res = self.m.route_skills_apply("no-such-seat", self.home, "x")
        self.assertFalse(os.path.isdir(os.path.join(self.home, "skills")))
        if res is not None:
            self.assertEqual(res["written"], [])
            self.assertEqual(res["total"], 0)

    def test_dsh_has_skills_channel(self):
        """dsh 现在有 skills 通道：投到 <DSH_HOME>/skills（桌面版与网页版共用）。

        原先 `SEATS["dsh"] = None` 的理由是"DSH 由 preset entries 驱动" —— 只对了一半：
        preset 的 `customSkillDirs` 指向 `@deepseek-ai/dsh-agent-preset/skills`，
        那是**包内置的 4 个**；路由的 86 份走的正是插件内置的 `<DSH_HOME>/skills`。
        """
        self.assertIn("dsh", self.S.SEATS)
        self.assertIsNotNone(self.S.SEATS["dsh"])
        res = self.m.route_skills_apply("dsh", self.home, "dsh")
        self.assertTrue(os.path.isdir(os.path.join(self.home, "skills")))
        self.assertEqual(res["total"], 86)
        self.assertEqual(len(res["written"]), 86)


class ParentSlugTests(SkillsBase):
    def test_parent_slugs_maps_slug_to_key(self):
        m = self.S._parent_slugs()
        self.assertEqual(len(m), 6)
        for key in self.R.CAT_ORDER:
            self.assertEqual(m[self.R.CATEGORIES[key][3]], key)
        self.assertEqual(m.get("auto-pipe"), "auto")

    def test_parent_key_rejects_unknown(self):
        self.assertIsNone(self.S._parent_key("not-a-slug"))
        self.assertIsNone(self.S._parent_key(""))

    def test_status_survives_parent_dirs(self):
        """目录里出现父类目（历史上正是这里 KeyError 崩掉的）。"""
        self.S.install("codex", self._port, home=self.home)
        st = self.S.status("codex", self.home)
        self.assertEqual(len(st["installed"]), 86)
        self.assertEqual(st["stale"], [])
        self.assertIn("auto-pipe", st["installed"])
        self.assertIn("sw-analyze", st["installed"])

    def test_status_flags_stale(self):
        self.S.install("codex", self._port, home=self.home)
        target = os.path.join(self.home, "skills", "sw-analyze", "SKILL.md")
        with open(target, "w", encoding="utf-8") as f:
            f.write("被改过了\n")
        st = self.S.status("codex", self.home)
        self.assertIn("sw-analyze", st["stale"])
        self.assertNotIn("sw-analyze", st["installed"])

    def test_status_keeps_foreign_dirs(self):
        foreign = os.path.join(self.home, "skills", "my-own-skill")
        os.makedirs(foreign, exist_ok=True)
        with open(os.path.join(foreign, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("用户自己的\n")
        self.S.install("codex", self._port, home=self.home)
        st = self.S.status("codex", self.home)
        self.assertEqual(st["foreign"], ["my-own-skill"])

    def test_status_when_root_absent_lists_missing(self):
        st = self.S.status("codex", self.home)
        self.assertEqual(len(st["missing"]), 86)
        self.assertEqual(st["installed"], [])

    # ---- 工具 ----
    @staticmethod
    def _port(path, text, make_dirs=False):
        d = os.path.dirname(path)
        if make_dirs and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        with open(path, "wb") as f:
            f.write(text.encode("utf-8"))


class UninstallTests(SkillsBase):
    def test_uninstall_removes_ours_only(self):
        self.S.install("codex", self._port, home=self.home)
        foreign = os.path.join(self.home, "skills", "keep-me")
        os.makedirs(foreign, exist_ok=True)
        with open(os.path.join(foreign, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("用户自己的\n")

        removed, gone = [], []

        def rm(path):
            if os.path.isfile(path):
                os.remove(path)
                removed.append(path)
                return True
            return False

        res = self.S.uninstall("codex", rm, home=self.home)
        self.assertEqual(len(res["removed"]), 86)
        self.assertTrue(os.path.isdir(foreign), "用户自己的 skill 不该被删")
        self.assertFalse(os.path.isdir(os.path.join(self.home, "skills", "sw-analyze")))
        del gone

    @staticmethod
    def _port(path, text, make_dirs=False):
        d = os.path.dirname(path)
        if make_dirs and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        with open(path, "wb") as f:
            f.write(text.encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
