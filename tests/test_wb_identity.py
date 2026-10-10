"""WorkBuddy 身份层（~/.workbuddy 的 SOUL / IDENTITY / USER / MEMORY）回归测试。

锁住 定位到的"破甲全绿但行为不是注入人格"的真因：

  WorkBuddy 每轮把用户级身份文件塞进 <identity_context>（**user 角色、prompt 末尾**），
  并带了明确指令 "If SOUL.md is present, embody its persona and tone"。
  我们注入的人格在 **system** 消息的 <content_policy> 里（prompt 前部）。
  末尾 + user 角色 + "embody SOUL.md" 三条叠起来，直接压过前面的人格 ——
  验证 trace span[6]：<content_policy> 在 index 1945，<identity_context> 在 62143。
  于是磁盘上补丁全在、状态全绿，模型却按 SOUL.md（阿枢）行事。

判定拆四条：
  ① SOUL.md / IDENTITY.md 被整段换成共用 persona，且保住 frontmatter（read_when 不能丢）；
  ② 根目录与 user-<uid>-personal 两处副本都写（应用读的是后者）；
  ③ USER.md / MEMORY.md 里"只扫不动 / 先确认"那类行被按行中和，且幂等；
  ④ 判定键生效：写完是 current（再跑不会白写），换个哈希判定为 other。
"""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "engine" / "破甲一键通.py"
SPEC = importlib.util.spec_from_file_location("pojia", SOURCE)
pojia = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pojia)

SOUL_STUB = """---
title: "SOUL.md"
summary: "阿枢的核心人格"
read_when:
  - 每次会话开始
---

我是**阿枢（Shu）**。取自主角。

## Boundaries

- 高风险运维动作**先扫描出报告，不动手**，等用户明确确认。
"""
ID_STUB = """---
summary: "Agent identity record"
read_when:
  - 每次会话开始
---

- **Name:** 阿枢 / Shu
"""
USER_STUB = """---
summary: "User profile record"
---

# USER.md

- **Name:** AuuCoder

## 怎么配合他

- 高风险操作一律 **inspect-first, no-action**：先扫描出报告，确认后才动手。
- 给方案时带上风险点和回退路径。
"""
MEM_STUB = """# MEMORY.md

## 用户偏好（补充）
- 高风险运维 / 反复实验类操作，先说清"每一步消耗什么"（配额、计数、内存）再动手。
- 其它技术笔记保留。
"""

PERSONAL = "user-2e11fcee-e4e2-4528-b4d0-8c4cf62c3a83-personal"


class WbIdentityTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.home = os.path.join(self.td, ".workbuddy")
        self.dirs = [self.home, os.path.join(self.home, PERSONAL)]
        for d in self.dirs:
            os.makedirs(d, exist_ok=True)
            for name, body in (("SOUL.md", SOUL_STUB), ("IDENTITY.md", ID_STUB),
                               ("USER.md", USER_STUB), ("MEMORY.md", MEM_STUB)):
                with open(os.path.join(d, name), "w", encoding="utf-8", newline="") as fh:
                    fh.write(body)
        self.t = pojia.TARGETS["wb"]()
        self.user, _src = pojia.load_user_persona("")
        _pol, self.want = pojia.build_policy(self.user)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)

    def _read(self, d, name):
        with open(os.path.join(d, name), encoding="utf-8") as fh:
            return fh.read()

    def test_both_copies_patched(self):
        """根目录与 personal 两处副本都要注入 —— 应用读的是 personal 那份。"""
        self.t.patch_identity(self.home, self.user, self.want)
        for d in self.dirs:
            soul = self._read(d, "SOUL.md")
            ident = self._read(d, "IDENTITY.md")
            self.assertIn("麻衣学姐", soul)
            self.assertIn("麻衣学姐", ident)
            self.assertNotIn("阿枢", soul)
            self.assertNotIn("阿枢", ident)
            self.assertIn(pojia.ID_MARK_BEGIN, soul)

    def test_frontmatter_kept(self):
        """frontmatter 必须留着 —— read_when 丢了 WorkBuddy 就不再注入这份文件。"""
        self.t.patch_identity(self.home, self.user, self.want)
        soul = self._read(self.home, "SOUL.md")
        self.assertTrue(soul.startswith("---"))
        self.assertIn("每次会话开始", soul)
        self.assertNotIn("阿枢", soul.split(pojia.ID_MARK_BEGIN)[0])

    def test_conflicting_lines_neutralized(self):
        """USER/MEMORY 里"只扫不动 / 先确认"的行要被中和。"""
        self.t.patch_identity(self.home, self.user, self.want)
        for d in self.dirs:
            user = self._read(d, "USER.md")
            mem = self._read(d, "MEMORY.md")
            self.assertNotIn("inspect-first, no-action", user)
            self.assertIn("给方案时带上风险点和回退路径", user)      # 无关行不许动
            self.assertNotIn("再说清", mem)
            self.assertIn("其它技术笔记保留", mem)
        self.assertEqual(self.t.identity_residue(self.home), 0)

    def test_idempotent_and_version_aware(self):
        """写完是 current（不白写）；哈希变了判 other（要重写）。"""
        st1 = self.t.patch_identity(self.home, self.user, self.want)
        self.assertEqual(st1["fixed"], 8)          # 2×SOUL + 2×IDENTITY + 2×USER + 2×MEMORY
        self.assertEqual(st1["lines"], 4)          # 其中 2×USER + 2×MEMORY 是按行中和
        self.assertEqual(st1["total"], 4)          # 身份位（SOUL/IDENTITY）共 4 份
        st2 = self.t.patch_identity(self.home, self.user, self.want)
        self.assertEqual(st2["fixed"], 0)
        self.assertEqual(st2["cur"], 4)            # 4 个身份位都是 current
        soul = self._read(self.home, "SOUL.md")
        self.assertEqual(pojia.identity_state(soul, self.want), "current")
        self.assertEqual(pojia.identity_state(soul, "deadbeefcafe"), "other")
        self.assertEqual(pojia.identity_state("没有标记的文本", self.want), "")

    def test_missing_files_no_crash(self):
        """只装了 SOUL.md 的机器上不能炸（没有 USER/MEMORY 也要能跑）。"""
        empty = os.path.join(self.td, "empty")
        os.makedirs(empty, exist_ok=True)
        with open(os.path.join(empty, "SOUL.md"), "w", encoding="utf-8") as fh:
            fh.write("no frontmatter here\n")
        st = self.t.patch_identity(empty, self.user, self.want)
        self.assertEqual(st["fixed"], 1)
        self.assertIn(pojia.ID_MARK_BEGIN, self._read(empty, "SOUL.md"))


if __name__ == "__main__":
    unittest.main()
