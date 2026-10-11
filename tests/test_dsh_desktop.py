# -*- coding: utf-8 -*-
"""DSH 桌面版（DeepSeek Harness Desktop）支持 —— 回归测试。

盯的是三个具体故障：

1. **认不出桌面版安装**。官方桌面版把核心包全打进 `resources/app.asar`，
   `app.asar.unpacked` 里只落原生模块（dsh-desktop-host / libreoffice-kit），
   老判据（dsh-web-app / dsh-base …）一个都不命中；安装根也不叫 "DSH Desktop"
   —— 本机验证 `F:\\Softstore\\deepseek 本地运行时`，主程序 `DeepSeek Harness.exe`。

2. **破甲模式只写 web**。桌面版读的是**独占的 desktop profile**：
   `dsh` 包里 `rejectElectronProfile()` 的原话是
   `profile "desktop" is managed exclusively by the Electron application`。
   web 那份它一概不看 —— 只写一处 = 桌面版等于没打。

3. **包存在性基准用错**。拿只有原生模块的桌面版目录去核对声明里引用的包，
   会把好端端的声明误报成 broken（表现：模式下拉里看得见、但开不出会话）。

在本机（四种形态中的桌面版）验证通过。
"""
import importlib.util
import os
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

# 一份"够用"的创造模式声明 —— 结构照 shipped 的 cordis.patch.yml 裁，
# 保留 preset_rename / preset_drop_rows 会去动的那些行。
TPL = """\
- id: some-plugin
  name: '@deepseek-ai/dsh-some-plugin'
- insert:
    - id: preset-cordis
      name: '@deepseek-ai/dsh-agent-preset'
      config:
        id: cordis
        name: 创造模式
        order: 4
        plugins:
          - id: persona
            name: '@deepseek-ai/dsh-persona'
            config:
              text: >-
                You are a coding agent powered by DeepSeek. Be helpful.
          - id: tool-cordis
            name: '@deepseek-ai/dsh-tool-cordis'
"""


class _TmpTest(unittest.TestCase):
    """临时目录夹具：所有落盘都关在 tmp 里，绝不碰真实 ~/.dsh。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pojia_dshd_")
        self._old_home = os.environ.get("DSH_HOME")

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("DSH_HOME", None)
        else:
            os.environ["DSH_HOME"] = self._old_home
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
        """unpacked 里只有 dsh-desktop-host 时也要认作 @deepseek-ai 目录。

        桌面版把核心包全打进 asar，unpacked 只落原生模块；
        拿老的 dsh-web-app / dsh-base 判据会一直认不出来。
        """
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


class DshPresetProfilesTests(_TmpTest):
    """profile 目标的枚举规则。"""

    def setUp(self):
        super().setUp()
        os.environ["DSH_HOME"] = self.tmp
        self.t = M.DshTarget()

    def profile(self, name):
        return self.mkfile("profiles", name, "cordis.patch.yml", content="[]\n")

    def test_10_only_existing_profiles(self):
        """只返回真实存在的 profile —— 没装那个形态就不该凭空往里写。"""
        self.assertEqual(self.t.preset_profiles(), [])
        web = self.profile("web")
        got = self.t.preset_profiles()
        self.assertEqual([n for n, _p in got], ["web"])
        self.assertEqual(got[0][1], web)
        self.profile("desktop")
        self.assertEqual([n for n, _p in self.t.preset_profiles()], ["web", "desktop"])

    def test_11_all_profiles_is_opt_in(self):
        """headless 之类默认不动，--dsh-all-profiles 才一并纳入。"""
        self.profile("web")
        self.profile("headless")
        self.assertEqual([n for n, _p in self.t.preset_profiles()], ["web"])
        self.assertEqual([n for n, _p in self.t.preset_profiles(True)], ["web", "headless"])

    def test_12_node_modules_never_a_target(self):
        """profiles/node_modules 是依赖目录，不能当成 profile 写进去。"""
        self.profile("web")
        self.mkfile("profiles", "node_modules", "cordis.patch.yml", content="[]\n")
        names = [n for n, _p in self.t.preset_profiles(True)]
        self.assertNotIn("node_modules", names)

    def test_13_pkg_base_skips_native_only_dir(self):
        """核对包存在性时不能用只有原生模块的桌面版目录（会假报 broken）。"""
        native = self.mkdir("unpacked", "dsh", "node_modules", "@deepseek-ai")
        os.makedirs(os.path.join(native, "dsh-desktop-host"), exist_ok=True)
        full = os.path.join(self.tmp, "full", "@deepseek-ai")
        os.makedirs(os.path.join(full, "dsh-web-app"), exist_ok=True)
        self.assertEqual(self.t.pkg_base([native, full]), full)
        # 都没有完整包树时退回第一个，不能炸
        self.assertEqual(self.t.pkg_base([native]), native)
        self.assertEqual(self.t.pkg_base([]), "")

    def test_14_pkg_bases_keeps_every_install_point(self):
        """核对包存在性要传**全部**基点（本机两个安装点各有一半包）。

        全局 npm 与 npx 缓存可能各只有一部分包 ——
        拿单一目录核对会把另一半判成"不存在"。
        """
        a = self.mkdir("p1", "@deepseek-ai")
        b = self.mkdir("p2", "@deepseek-ai")
        os.makedirs(os.path.join(a, "dsh-web-app"), exist_ok=True)
        os.makedirs(os.path.join(b, "dsh-tool-bash"), exist_ok=True)
        got = self.t.pkg_bases([a, b])
        self.assertEqual(got, [a, b], "全部基点都要留下，不能只挑一个")
        # 一个基点找齐就不算缺，另一个基点提供缺的那半
        block = "name: '@deepseek-ai/dsh-web-app'\nname: '@deepseek-ai/dsh-tool-bash'\n"
        self.assertEqual(self.t.preset_missing_pkgs(block, [a]), ["@deepseek-ai/dsh-tool-bash"])
        self.assertEqual(self.t.preset_missing_pkgs(block, got), [])
        # 单一字符串写法仍然兼容
        self.assertEqual(self.t.preset_missing_pkgs(block, a), ["@deepseek-ai/dsh-tool-bash"])


class DshPresetDeployTests(_TmpTest):
    """端到端：破甲模式要同时落进 web 与 desktop 两个 profile。"""

    def setUp(self):
        super().setUp()
        os.environ["DSH_HOME"] = self.tmp
        self.t = M.DshTarget()
        # 安装基点照桌面版形态造：只有原生模块 + shipped 模板
        base_parts = ("install", "resources", "app.asar.unpacked", "dsh",
                      "node_modules", "@deepseek-ai")
        self.base = self.mkdir(*base_parts)
        self.mkfile(*(base_parts + ("dsh-web-app", "presets", "cordis.patch.yml")),
                    content=TPL)
        self.mkfile(*(base_parts + ("dsh-desktop-host", "package.json")), content="{}")
        # 声明里引用到的包得在盘上真存在，否则 preset_missing_pkgs 会（正确地）
        # 判成 broken —— 那是另一条规则，不该混进这组测试
        for pkg in ("dsh-some-plugin", "dsh-agent-preset", "dsh-persona",
                    "dsh-tool-cordis"):
            self.mkdir(*(base_parts + (pkg,)))

    PERSONA = "测试人格正文：这里是本机部署的操作者人格。"

    def profile(self, name):
        return self.mkfile("profiles", name, "cordis.patch.yml", content="[]\n")

    def test_20_deploy_writes_both_profiles(self):
        web = self.profile("web")
        desk = self.profile("desktop")
        st, note = self.t.deploy_preset([self.base], self.PERSONA, "")
        self.assertIn(st, ("deployed", "current"), note)
        for pf in (web, desk):
            txt = self.read(pf)
            self.assertIn(M.DshTarget.PRESET_BEGIN, txt, "没写进 %s" % pf)
            self.assertIn("id: pojia", txt, "%s 里没有破甲模式的 config.id" % pf)
            self.assertNotIn("preset-cordis", txt, "%s 里还留着创造模式的行 id" % pf)

    def test_21_deploy_idempotent(self):
        web = self.profile("web")
        self.profile("desktop")
        self.t.deploy_preset([self.base], self.PERSONA, "")
        first = self.read(web)
        st, _note = self.t.deploy_preset([self.base], self.PERSONA, "")
        self.assertEqual(st, "current", "第二遍应当判 current")
        self.assertEqual(self.read(web), first, "幂等：第二遍不能改动字节")

    def test_22_host_global_row_dropped(self):
        """tool-cordis 那行要摘掉，否则和创造模式抢进程级注册。"""
        web = self.profile("web")
        self.profile("desktop")
        self.t.deploy_preset([self.base], self.PERSONA, "")
        block = self.t.preset_extract(self.read(web))
        self.assertTrue(block, "没提取到破甲模式块")
        self.assertEqual(self.t.preset_live_rows(block), [],
                         "还留着宿主级互斥行：%s" % self.t.preset_live_rows(block))

    def test_23_state_reports_each_profile(self):
        """状态必须逐 profile 汇报，并且与实际落盘一致。"""
        self.profile("web")
        self.profile("desktop")
        self.t.deploy_preset([self.base], self.PERSONA, "")
        st, note = self.t.preset_state("", [self.base])
        self.assertIn("web:", note, note)
        self.assertIn("desktop:", note, note)
        per = [self.t._preset_state_one(pf, "", self.t.pkg_base([self.base]))
               for _n, pf in self.t.preset_profiles()]
        self.assertEqual(len(per), 2, "应该逐 profile 各算一次")
        self.assertEqual(per[0][0], per[1][0], "两个 profile 写的是同一份块")
        self.assertEqual(st, per[0][0], note)

    def test_26_worst_profile_wins(self):
        """某一个 profile 掉队时整体必须报出来。

        这正是只写 web 会踩的坑：桌面版那份空着，卡片却因为 web 正常而显示
        「已部署」—— 桌面版用户看着一切正常，实际一个模式都没有。
        """
        self.profile("web")
        desk = self.profile("desktop")
        self.t.deploy_preset([self.base], self.PERSONA, "")
        with open(desk, "w", encoding="utf-8") as f:
            f.write("[]\n")                      # 模拟桌面版那份没打上
        st, note = self.t.preset_state("", [self.base])
        self.assertEqual(st, "absent", note)
        self.assertIn("desktop:absent", note, note)

    def test_24_remove_strips_both(self):
        web = self.profile("web")
        desk = self.profile("desktop")
        self.t.deploy_preset([self.base], self.PERSONA, "")
        st, note = self.t.remove_preset("apply")
        self.assertEqual(st, "removed", note)
        for pf in (web, desk):
            self.assertNotIn(M.DshTarget.PRESET_BEGIN, self.read(pf),
                             "%s 没清干净" % pf)

    def test_25_deploy_survives_missing_desktop(self):
        """只装了 Web 版（没有 desktop profile）时不能被拖垮。"""
        web = self.profile("web")
        st, note = self.t.deploy_preset([self.base], self.PERSONA, "")
        self.assertIn(st, ("deployed", "current"), note)
        self.assertIn(M.DshTarget.PRESET_BEGIN, self.read(web))

    # ── 空壳 patch（`[]`）的坑，在 dsh-workbuddy2api 上验证到 ──
    #
    # `--from-default-profile` 新建的 profile，cordis.patch.yml 只有一个空数组。
    # YAML 里 `[]` 是一份完整文档，后面再跟 `- insert:` 就是第二份文档，加载直接
    # 失败：end of the stream or a document separator is expected（10:1）。
      # web / desktop 原本有真实条目，所以只在自建 profile 上炸 —— 表现比
    # "没破甲"更糟：模式下拉里连 pojia 都看不到。

    @staticmethod
    def _meaningful(body):
        return [ln.strip() for ln in body.splitlines()
                if ln.strip() and not ln.strip().startswith("#")]

    def test_27_empty_array_patch_not_left_broken(self):
        """写进空壳 patch 后必须把 `[]` 摘掉，只留一份文档。"""
        web = self.profile("web")
        self.profile("desktop")
        self.t.deploy_preset([self.base], self.PERSONA, "")
        got = self._meaningful(self.read(web))
        self.assertNotIn("[]", got, "空数组没摘掉 → YAML 会判成两份文档")
        self.assertTrue(got and got[0].startswith("- "),
                        "首行应直接是数组项，实际 %r" % (got[:1],))

    def test_28_broken_empty_array_is_repaired(self):
        """已经坏成 `[]` + 块 的文件，再 apply 一次要能修回来。"""
        self.profile("web")
        self.profile("desktop")
        self.t.deploy_preset([self.base], self.PERSONA, "")
        block = self.t.preset_extract(self.read(
            os.path.join(self.tmp, "profiles", "web", "cordis.patch.yml")))
        self.assertTrue(block, "没提取到块，后续构造坏文件会失真")
        # 复现旧 bug 的产物：块前面留一个孤立的 []
        self.mkfile("profiles", "web", "cordis.patch.yml",
                    content="[]\n\n" + block + "\n")
        st, _note = self.t.deploy_preset([self.base], self.PERSONA, "")
        self.assertEqual(st, "deployed", "坏文件必须被判成需要重写")
        self.assertNotIn("[]", self._meaningful(self.read(
            os.path.join(self.tmp, "profiles", "web", "cordis.patch.yml"))))

    def test_29_custom_profile_is_a_target(self):
        """自建 provider profile（如 dsh-workbuddy2api）也要在默认目标里。"""
        self.profile("web")
        self.profile("dsh-workbuddy2api")
        names = [n for n, _p in self.t.preset_profiles()]
        self.assertIn("dsh-workbuddy2api", names,
                      "自建 profile 漏掉 = 能选到破甲模式但一点没打")

    def test_34_revert_leaves_loadable_patch(self):
        """还原后 patch 必须仍是合法的顶层数组 —— 验证缺陷。

        `dsh-workbuddy2api` 的 patch 通篇只有出厂注释（没有用户自有条目），
        撤掉破甲块后一个 YAML 内容都不剩，dsh 直接报
        `overlay ... must be a top-level YAML array of loader patch entries`
        —— 比"没破甲"更糟：那个 profile 整个加载失败。
        """
        self.profile("web")
        # 忠实复现现场：自建 profile 的 patch 上没有任何条目
        bare = self.mkfile("profiles", "dsh-workbuddy2api", "cordis.patch.yml",
                           content="# 出厂注释：你的 loader patch 层\n")
        st, _ = self.t.deploy_preset([self.base], self.PERSONA, "")
        self.assertEqual(st, "deployed")
        st2, _ = self.t.remove_preset()
        self.assertEqual(st2, "removed", "两个 profile 都该被处理")
        for pf in (bare, os.path.join(self.tmp, "profiles", "web", "cordis.patch.yml")):
            got = self._meaningful(self.read(pf))
            self.assertEqual(got, ["[]"],
                             "%s 还原后成了不可加载的空壳" % pf)

    def test_35_ensure_top_array_edges(self):
        """兜底只该在"整份文件没有任何条目"时动手，不能误伤已有条目。"""
        T = M.DshTarget
        # 通篇注释或干脆是空 → 补一份空数组文档
        self.assertEqual(T.ensure_top_array("# 只有注释\n\n").strip().splitlines()[-1],
                         "[]")
        self.assertEqual(T.ensure_top_array("").strip(), "[]")
        # 已有真实条目 → 一个字节都不动（补了会变成两份 YAML 文档）
        live = "# 注释\n- id: ui-chat\n  name: '@deepseek-ai/dsh-client-ui-chat'\n"
        self.assertEqual(T.ensure_top_array(live), live)
        # 已经有孤立的 [] → 算"有内容"，不重复叠加
        self.assertEqual(T.ensure_top_array("[]\n").count("[]"), 1)


class BridgeFoldTests(unittest.TestCase):
    """GUI 桥把界面卡折回命令行目标。

    桌面版单独成卡是为了让它的状态可见，但**执行时不能变成第二条命令** ——
    preset 一次就写 web + desktop 两份 profile，跑两遍纯属重复。
    """

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
        # pojia 仍是伪目标（只做展示），别被顺手塞进卡列表
        self.assertNotIn("pojia", b.ORDER)

    def test_33_desktop_card_state_when_nothing_deployed(self):
        """什么都没部署时徽章必须报 none —— 不能写「已部署 · 旧版」。

        还原后撞到的 bug：桌面版卡的 state 由 pst 一刀切
        （`"current" if pst == "current" else "stale"`），absent（profile 里没有
        这条声明）也被说成 stale，于是徽章写「已部署 · 旧版」、正文写「什么都没
        部署」，自相矛盾。
        """
        b = self._bridge()
        state, dsum = b._desktop_card_state("none", "absent")
        self.assertEqual(state, "none", "什么都没部署 = none，不是 stale")
        self.assertFalse(dsum["any"])
        self.assertEqual(dsum["chip"], "什么都没部署")
        # 两半都本版 → current
        self.assertEqual(b._desktop_card_state("current", "current")[0], "current")
        # 两半都在、但不是本版 → stale
        self.assertEqual(b._desktop_card_state("current", "other")[0], "stale")
        # 只落地了一半 → warn（部分命中），同样不该说成 stale
        self.assertEqual(b._desktop_card_state("none", "current")[0], "warn")
        self.assertEqual(b._desktop_card_state("stale", "absent")[0], "warn")
        # 探测不到安装 → broken，优先于其它判定
        self.assertEqual(b._desktop_card_state("broken", "absent")[0], "broken")


if __name__ == "__main__":
    unittest.main()
