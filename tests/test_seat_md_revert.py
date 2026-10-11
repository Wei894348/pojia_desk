# -*- coding: utf-8 -*-
"""HomeMdTarget（claude / grok / gemini / hermes）还原路径的回归测试。

revert 侧的两个问题：

1. **Hermes SOUL.md 被还原成 0 字节。** 链路：
   出厂 SOUL.md（英文默认人格）→ apply 检出出厂态、整体接管（base=""）
   → 块写进去，文件里**只剩我们的块** → revert 剥块后得到空串
   → 旧代码无条件 `tx_write_bytes(md_p, b"")` → 写了个空文件。
   客户端读到空身份位；更糟的是下一次 apply 把"空"记成 original_sha256，
   之后永远还原成空（污染沿链传递）。

2. **空原稿哈希不可信。** 修 ① 之外还得堵住"已经被记成空"的状态：
   `_original_copy` 遇到空哈希要当"没记录"，退回最早那份不含块的非空备份。

全程在临时目录里跑，不碰真实席位目录。
"""
import importlib.util
import os

# 席位独立源会让单测读到仓库里的 content/seats/ —— 测试一律走共享管线
os.environ.setdefault("POJIA_NO_SEAT_OVERRIDE", "1")
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "engine", "破甲一键通.py")


def load_cli():
    spec = importlib.util.spec_from_file_location("pojia_md_revert", SRC)
    m = importlib.util.module_from_spec(spec)
    m.__name__ = "pojia_md_revert"
    spec.loader.exec_module(m)
    return m


class _Base(unittest.TestCase):
    """用 hermes 的标记对；SOUL.md 是 HomeMdTarget 家族里最典型的身份位。"""

    SEAT = "hermes"

    @classmethod
    def setUpClass(cls):
        cls.m = load_cli()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = self.tmp.name
        t = self.m.TARGETS[self.SEAT]()
        self.md_p = os.path.join(self.home, t.md_name)
        self.args = mock.MagicMock()
        self.args.persona = ""
        setattr(self.args, t.dir_flag, self.home)
        self.args.force = True
        self.args.dry_run = False
        # 打桩掉环境探测 / 客户端探测 / 技能投放：本组只测 revert 的文件层
        for name, value in (
                ("say", lambda *a, **k: None),
                ("LOG_PATH", os.path.join(self.tmp.name, "t.log")),
                ("route_skills_apply", lambda *a, **k: None),
                ("route_skills_revert", lambda *a, **k: None),
                ("find_processes", lambda *a, **k: []),
        ):
            p = mock.patch.object(self.m, name, value)
            p.start()
            self.addCleanup(p.stop)
        for meth, value in (("discover_home", lambda self: ""),
                            ("client_probe", lambda self: (None, "")),
                            ("post_apply", lambda self, a, h: None),
                            ("post_revert", lambda self, a, h: None)):
            p = mock.patch.object(self.m.TARGETS[self.SEAT], meth, value)
            p.start()
            self.addCleanup(p.stop)
        self.T = self.m.TARGETS[self.SEAT]()

    def write(self, text):
        with open(self.md_p, "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def read(self):
        with open(self.md_p, "rb") as f:
            return f.read()


class FactoryTakeoverRevertTests(_Base):
    FACTORY = ("You are Hermes Agent, an intelligent AI assistant created by Nous Research. "
               "You are helpful, knowledgeable, and direct.")

    def test_revert_restores_factory_text_not_empty(self):
        """出厂人格 → 接管 → revert：必须还原**出厂原文**，不是 0 字节。"""
        self.write(self.FACTORY)
        self.T.apply(self.args, "apply")
        self.assertIn(self.m.SEAT_MARKS[self.SEAT][0].encode(), self.read())
        self.T.apply(self.args, "revert")
        data = self.read()
        self.assertTrue(data, "SOUL.md 被还原成空文件了（现场 bug）")
        self.assertEqual(data.decode("utf-8"), self.FACTORY)

    def test_no_empty_backup_is_treated_as_original(self):
        """0 字节的 `.instructions.` 备份不许当成原稿。"""
        self.write(self.FACTORY)
        self.T.apply(self.args, "apply")
        bdir = os.path.join(self.home, self.m.HERMES_BAKDIR)
        os.makedirs(bdir, exist_ok=True)
        # 手工塞一份空备份 + 把 state 的原稿哈希改成空串哈希（模拟被污染的状态）
        with open(os.path.join(bdir, "SOUL.md.instructions.19990101-000000.bak"), "wb"):
            pass
        st = self.T.load_state(self.home)
        st["original_sha256"] = self.m.sha256_bytes(b"")
        self.T.save_state(self.home, st)
        self.T.apply(self.args, "revert")
        data = self.read()
        self.assertTrue(data, "空备份被当成原稿，把 SOUL.md 清空了")
        self.assertNotIn(self.m.SEAT_MARKS[self.SEAT][0].encode(), data)

    def test_revert_removes_empty_original_file(self):
        """部署前是**空文件** → revert 应删掉它，而不是留个 0 字节壳。"""
        self.write("")
        self.T.apply(self.args, "apply")
        self.T.apply(self.args, "revert")
        self.assertFalse(os.path.exists(self.md_p),
                         "剥块后无用户内容应删除文件（空身份位挡着客户端写默认值）")


class UserContentRevertTests(_Base):
    def test_user_content_kept_after_revert(self):
        """用户自有内容：revert 剥块后必须一字不差地留着。"""
        user = "# 我的备注\n\n- 用 pwsh\n- 别用 bash\n"
        self.write(user)
        self.T.apply(self.args, "apply")
        self.T.apply(self.args, "revert")
        self.assertEqual(self.read().decode("utf-8"), user)

    def test_repeated_apply_then_revert_returns_original(self):
        """连续 apply 两次再 revert，回的是最初的原稿（不是"上一次 apply 前"）。"""
        user = "# 原稿\n\n内容。\n"
        self.write(user)
        self.T.apply(self.args, "apply")
        self.T.apply(self.args, "apply")
        self.T.apply(self.args, "revert")
        self.assertEqual(self.read().decode("utf-8"), user)


if __name__ == "__main__":
    unittest.main()
