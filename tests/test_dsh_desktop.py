# -*- coding: utf-8 -*-
"""DSH 桌面版（DeepSeek Harness Desktop）与提示词注入 —— 回归测试。

盯的是这几件事：

1. **认不出桌面版安装**。官方桌面版把核心包全打进 `resources/app.asar`，
   `app.asar.unpacked` 里只落原生模块（dsh-desktop-host / libreoffice-kit），
   老判据（dsh-web-app / dsh-base …）一个都不命中；安装根也不叫 "DSH Desktop"
   —— 本机验证 `F:\\Softstore\\deepseek local runtime` 那类自定义目录，
   主程序 `DeepSeek Harness.exe`。

2. **提示词落到哪**。DSH 用 `@deepseek-ai/dsh-agent-instructions` 读
   `<DSH_HOME>/AGENTS.md`（包内 `USER_GLOBAL_FILE = "AGENTS.md"` 写死），
   桌面版与 Web/CLI 共用同一个 `~/.dsh`。所以注入只写这一个文件，
   不再往 profile 的 `cordis.patch.yml` 里塞 agent preset。

3. **标记块与还原**。用户自己的 AGENTS.md 内容原样保留；重复部署不写盘；
   还原只摘块、块外内容不动；超过 65536 字节预算要报出来（超了会被截断）。

在本机（桌面版形态）验证通过。
"""
import importlib.util
import os

# 席位独立源会让单测读到仓库里的 content/seats/ —— 测试一律走共享管线
os.environ.setdefault("POJIA_NO_SEAT_OVERRIDE", "1")
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


def _load_main():
    spec = importlib.util.spec_from_file_location(
        "pojia_main_for_dshd", os.path.join(HERE, "engine", "破甲一键通.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pojia_main_for_dshd"] = mod
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


M = _load_main()


class _TmpTest(unittest.TestCase):
    """临时目录夹具：所有落盘都关在 tmp 里，绝不碰真实 ~/.dsh。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pojia_dshd_")
        self._old_home = os.environ.get("DSH_HOME")
        self._old_seat = os.environ.get("POJIA_NO_SEAT_OVERRIDE")
        os.environ["DSH_HOME"] = self.tmp
        os.environ["POJIA_NO_SEAT_OVERRIDE"] = "1"

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("DSH_HOME", None)
        else:
            os.environ["DSH_HOME"] = self._old_home
        if self._old_seat is None:
            os.environ.pop("POJIA_NO_SEAT_OVERRIDE", None)
        else:
            os.environ["POJIA_NO_SEAT_OVERRIDE"] = self._old_seat
        shutil.rmtree(self.tmp, ignore_errors=True)

    def mkdir(self, *parts):
        p = os.path.join(self.tmp, *parts)
        os.makedirs(p, exist_ok=True)
        return p

    def mkfile(self, *parts, **kw):
        content = kw.pop("content", "")
        p = os.path.join(self.tmp, *parts)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        return p

    @staticmethod
    def read(p):
        with open(p, encoding="utf-8") as f:
            return f.read()


class DshDesktopLayoutTests(_TmpTest):
    """桌面版的目录布局判据。"""

    def test_01_root_by_main_exe(self):
        """有主程序 exe 就算桌面版根 —— 官方那版叫 DeepSeek Harness.exe。"""
        d = self.mkdir("deepseek 本地运行时")
        self.mkfile("deepseek 本地运行时", "DeepSeek Harness.exe")
        self.assertTrue(M.DshTarget._is_desktop_root(d))

    def test_02_root_by_resources_layout(self):
        """exe 被改名/换壳时退回按 resources 布局认：runtime + asar.unpacked/dsh。"""
        d = self.mkdir("portable")
        self.mkdir("portable", "resources", "runtime")
        self.mkdir("portable", "resources", "app.asar.unpacked", "dsh")
        self.assertTrue(M.DshTarget._is_desktop_root(d))

    def test_03_plain_electron_is_not_desktop(self):
        """普通 Electron 应用不能被误判 —— 只看资源里有 app.asar 就会中招。"""
        d = self.mkdir("some-electron-app")
        self.mkdir("some-electron-app", "resources")
        self.mkfile("some-electron-app", "resources", "app.asar")
        self.assertFalse(M.DshTarget._is_desktop_root(d))

    def test_04_ai_dir_accepts_desktop_marker(self):
        """unpacked 里只有 dsh-desktop-host 时也要认作 @deepseek-ai 目录。"""
        d = self.mkdir("@deepseek-ai")
        self.mkdir("@deepseek-ai", "dsh-desktop-host")
        self.assertTrue(M.DshTarget._looks_like_ai_dir(d))
        self.assertFalse(M.DshTarget._looks_like_ai_dir(self.mkdir("empty-ai")))

    def test_05_resolve_pick_desktop_layout(self):
        """点选安装根时能解析到 asar.unpacked/dsh/node_modules/@deepseek-ai。"""
        self.mkdir("deepseek 本地运行时", "resources", "app.asar.unpacked", "dsh",
                   "node_modules", "@deepseek-ai", "dsh-desktop-host")
        good, note = M.DshTarget().resolve_pick(
            os.path.join(self.tmp, "deepseek 本地运行时"))
        self.assertTrue(good, "应解析出 @deepseek-ai，实际拿到 %r / %s" % (good, note))
        self.assertTrue(
            good.replace("\\", "/").endswith("app.asar.unpacked/dsh/node_modules/@deepseek-ai"),
            "桌面版的中间那层 dsh 不能漏：%s" % good)

    def test_06_classify_desktop_install(self):
        """状态页要能把它标成官方桌面版，别混进 npx 缓存那类。"""
        t = M.DshTarget()
        p = r"F:\Softstore\deepseek 本地运行时\resources\app.asar.unpacked\dsh\node_modules\@deepseek-ai"
        self.assertIn("桌面版", t.classify_install(p))


class DshAgentsInjectTests(_TmpTest):
    """提示词注入：<DSH_HOME>/AGENTS.md 标记块。"""

    PERSONA = "测试人格正文：这里是本机部署的操作者人格。"

    def t(self):
        return M.DshTarget()

    def test_10_agents_path_under_home(self):
        """落点就是 $DSH_HOME/AGENTS.md —— agent-instructions 写死的文件名。"""
        self.assertEqual(os.path.normcase(self.t().agents_path()),
                         os.path.normcase(os.path.join(self.tmp, "AGENTS.md")))
        self.assertEqual(M.DshTarget.AGENTS_FILE, "AGENTS.md")

    def test_11_deploy_writes_block(self):
        t = self.t()
        st, note = t.deploy_agents(self.PERSONA, "deadbeef1234")
        self.assertEqual(st, "deployed", note)
        txt = self.read(t.agents_path())
        self.assertIn(M.DshTarget.AGENTS_MARK_BEGIN, txt)
        self.assertIn(M.DshTarget.AGENTS_MARK_END, txt)
        self.assertIn("cfg-v7:h=deadbeef1234", txt)
        self.assertIn(self.PERSONA, txt)

    def test_12_user_content_preserved(self):
        """用户自己的 AGENTS.md 内容原样保留在块外。"""
        t = self.t()
        self.mkfile("AGENTS.md", content="# 我自己的笔记\n\n- 保留我\n")
        t.deploy_agents(self.PERSONA, "deadbeef1234")
        txt = self.read(t.agents_path())
        self.assertIn("# 我自己的笔记", txt)
        self.assertIn("- 保留我", txt)
        self.assertIn(self.PERSONA, txt)

    def test_13_deploy_idempotent(self):
        """第二遍必须判 current 且不改盘（按字节比）。"""
        t = self.t()
        t.deploy_agents(self.PERSONA, "deadbeef1234")
        first = self.read(t.agents_path())
        st, _note = t.deploy_agents(self.PERSONA, "deadbeef1234")
        self.assertEqual(st, "current")
        self.assertEqual(self.read(t.agents_path()), first)

    def test_14_state_tracks_hash(self):
        t = self.t()
        self.assertEqual(t.agents_state("deadbeef1234")[0], "absent")
        t.deploy_agents(self.PERSONA, "deadbeef1234")
        self.assertEqual(t.agents_state("deadbeef1234")[0], "current")
        self.assertEqual(t.agents_state("000000000000")[0], "other")

    def test_15_remove_strips_block_only(self):
        t = self.t()
        self.mkfile("AGENTS.md", content="# 我自己的笔记\n\n- 保留我\n")
        t.deploy_agents(self.PERSONA, "deadbeef1234")
        st, _ = t.remove_agents()
        self.assertEqual(st, "removed")
        txt = self.read(t.agents_path())
        self.assertNotIn(M.DshTarget.AGENTS_MARK_BEGIN, txt)
        self.assertIn("# 我自己的笔记", txt)
        self.assertIn("- 保留我", txt)

    def test_16_remove_deletes_empty_file(self):
        """块外没有内容时整份删掉，别留一个空 AGENTS.md。"""
        t = self.t()
        t.deploy_agents(self.PERSONA, "deadbeef1234")
        st, _ = t.remove_agents()
        self.assertEqual(st, "removed")
        self.assertFalse(os.path.exists(t.agents_path()))

    def test_17_remove_is_idempotent(self):
        t = self.t()
        t.deploy_agents(self.PERSONA, "deadbeef1234")
        t.remove_agents()
        self.assertEqual(t.remove_agents()[0], "absent")

    def test_18_budget_is_reported(self):
        """超过 65536 字节要判出来 —— 超了 DSH 会截断，模型只看到半截。"""
        t = self.t()
        big = "字" * 30000            # 90 KB
        st, note = t.deploy_agents(big, "deadbeef1234")
        self.assertEqual(st, "deployed")
        self.assertIn("65536", note)
        self.assertEqual(t.agents_state("deadbeef1234")[0], "other")

    def test_19_block_starts_with_hash_marker(self):
        """块的归属头用全局 HASH_MARK（cfg-v7:h=），extract_mark_hash 才认得出。"""
        block = M.DshTarget.agents_block(self.PERSONA, "deadbeef1234")
        self.assertTrue(block.startswith("<!-- cfg-v7:h=deadbeef1234 -->"), block[:60])


class BridgeFoldTests(unittest.TestCase):
    """GUI 桥把界面卡折回命令行目标。"""

    @staticmethod
    def _bridge():
        spec = importlib.util.spec_from_file_location(
            "pojia_bridge_for_dshd", os.path.join(HERE, "gui", "bridge.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["pojia_bridge_for_dshd"] = mod
        try:
            spec.loader.exec_module(mod)
        except SystemExit:
            pass
        return mod

    def test_30_fold_desktop_card_to_dsh(self):
        b = self._bridge()
        self.assertEqual(b._fold_targets(["dsh-desktop"]), ["dsh"])
        self.assertEqual(b._fold_targets(["dsh", "dsh-desktop"]), ["dsh"],
                         "两张卡都勾也只该跑一遍")
        self.assertEqual(b._fold_targets(["wb", "dsh-desktop", "codex"]),
                         ["wb", "dsh", "codex"], "保序")
        self.assertEqual(b._fold_targets([]), [])
        self.assertEqual(b.CARD_ONLY.get("dsh-desktop"), "dsh")

    def test_31_has_dsh_covers_both_cards(self):
        b = self._bridge()
        self.assertTrue(b.has_dsh(["dsh-desktop"]), "只勾桌面版也要认得出")
        self.assertTrue(b.has_dsh(["dsh"]))
        self.assertFalse(b.has_dsh(["wb", "codex"]))

    def test_32_desktop_card_registered(self):
        b = self._bridge()
        self.assertIn("dsh-desktop", b.CARD_FN)
        self.assertIn("dsh-desktop", b.ORDER)
        self.assertEqual(b.ORDER[b.ORDER.index("dsh") + 1], "dsh-desktop",
                         "桌面版卡要紧跟 DSH")
        self.assertNotIn("pojia", b.ORDER)

    def test_33_desktop_card_state_when_nothing_deployed(self):
        """什么都没部署时徽章必须报 none —— 不能写成已部署。"""
        b = self._bridge()
        state, dsum = b._desktop_card_state("absent", "absent")
        self.assertEqual(state, "none")
        self.assertFalse(dsum["any"])
        self.assertEqual(dsum["chip"], "什么都没部署")
        # 两半都本版 → current
        self.assertEqual(b._desktop_card_state("current", "current")[0], "current")
        # 只落地了一半 → warn
        self.assertEqual(b._desktop_card_state("current", "absent")[0], "warn")
        self.assertEqual(b._desktop_card_state("other", "current")[0], "warn")
        # 异常优先
        self.assertEqual(b._desktop_card_state("broken", "absent")[0], "broken")

    def test_34_pseudo_target_reads_agents_landing(self):
        """`status pojia` 仍然是只读快照，读的是 AGENTS.md 落点。"""
        b = self._bridge()
        self.assertIn("pojia", b.PSEUDO)
        self.assertEqual(b.CARD_FN["pojia"].__name__, "card_dsh_landing")


if __name__ == "__main__":
    unittest.main()
