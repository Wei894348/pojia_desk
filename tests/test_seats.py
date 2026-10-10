# -*- coding: utf-8 -*-
"""跨席位串味断言 + 新席位单测。

串味 = 一个席位的注入正文里出现了另一个席位特有的指纹（席位名、落点路径、
平台专属词）。检测方式：
`assert(!rendered[id].includes(beats[other]))` 检测席位包互串；
这里同理：渲染出的各席位指令文本两两互不包含对方的关键指纹。
"""
import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


def _load_main():
    spec = importlib.util.spec_from_file_location(
        "pojia_main_for_seats", os.path.join(HERE, "engine", "破甲一键通.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pojia_main_for_seats"] = mod
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


M = _load_main()

# 每个席位的身份指纹：只允许出现在自己的正文里
FINGERPRINTS = {
    "codex": ("Codex", "codex"),
    "zcode": ("ZCode",),
    "claude": ("Claude Code",),
    "grok": ("Grok CLI",),
    "gemini": ("Gemini CLI",),
    "hermes": ("Hermes Agent",),
}

# 席位落点路径互串：别人的 md 文件名不应出现在自己的正文里
FOREIGN_MD = {"CLAUDE.md": {"claude"}, "AGENTS.md": {"zcode", "grok"},
              "GEMINI.md": {"gemini"}, "SOUL.md": {"hermes"}}


def _render(seat):
    user, _src = M.load_user_persona("")
    _pol, want = M.build_policy(user)
    if seat == "codex":
        return M.CodexTarget().build_instructions(user, want)
    if seat == "zcode":
        return M.build_zcode_instructions(user, want)
    # 新三席位：走 HomeMdTarget.apply 相同的正文（v9 角色档）——
    # 它们共用 build_zcode_instructions，但各自包在席位标记里
    begin, end = M.SEAT_MARKS[seat]
    return M._seat_block(seat, M.build_zcode_instructions(user, want))


class SeatIsolationTests(unittest.TestCase):
    """跨席位串味检测：渲染产物两两互不包含对方指纹。"""

    def test_01_all_seats_render(self):
        for seat in FINGERPRINTS:
            txt = _render(seat)
            self.assertTrue(txt.strip(), "%s 渲染为空" % seat)

    def test_02_no_foreign_fingerprint(self):
        for seat in FINGERPRINTS:
            txt = _render(seat)
            for other, fps in FINGERPRINTS.items():
                if other == seat:
                    continue
                for fp in fps:
                    self.assertNotIn(fp, txt,
                                     "%s 的正文串进了 %s 的指纹「%s」" % (seat, other, fp))

    def test_03_no_foreign_memory_file(self):
        for seat in FINGERPRINTS:
            txt = _render(seat)
            for md, owners in FOREIGN_MD.items():
                if seat in owners:
                    continue
                self.assertNotIn(md, txt,
                                 "%s 的正文提到了 %s 的落点文件 %s" % (seat, "/".join(owners), md))

    def test_04_seat_marks_disjoint(self):
        seen = set()
        for seat, (begin, end) in M.SEAT_MARKS.items():
            self.assertNotIn(begin, seen, "标记 begin 重复：%s" % begin)
            self.assertIn(str(seat), begin, "标记里应有席位名：%s" % begin)
            seen.add(begin)

    def test_05_merge_preserves_user_content(self):
        for seat in ("claude", "grok", "gemini", "hermes"):
            orig = "# 用户标题\n\n用户自己的第一段。\n- 条目 A\n- 条目 B\n"
            merged = M.merge_marked_block(orig, seat, "正文X")
            self.assertTrue(merged.startswith(orig.rstrip("\n")),
                            "%s 合并后丢用户内容" % seat)
            self.assertIn("正文X", merged)
            self.assertIn(M.SEAT_MARKS[seat][0], merged)
            # 幂等：二次合并不重复
            again = M.merge_marked_block(merged, seat, "正文X")
            self.assertEqual(merged, again, "%s 合并不幂等" % seat)

    def test_06_strip_restores(self):
        for seat in ("claude", "grok", "gemini", "hermes"):
            orig = "# 用户标题\n\n内容。\n"
            merged = M.merge_marked_block(orig, seat, "正文X")
            stripped = M.strip_seat_block(merged, seat)
            self.assertEqual(orig.rstrip("\n"), stripped.rstrip("\n"))

    def test_07_seat_md_state(self):
        user, _src = M.load_user_persona("")
        _pol, want = M.build_policy(user)
        body = M.build_zcode_instructions(user, want)
        # 空 → 未部署
        self.assertEqual(M.seat_md_state("", "claude", want), "")
        # 用户文件无块 → ""（不是部署态）
        self.assertEqual(M.seat_md_state("# 用户稿", "claude", want), "")
        # 本版块 → current
        self.assertEqual(M.seat_md_state(M._seat_block("claude", body), "claude", want), "current")
        # 旧哈希块 → stale
        self.assertEqual(
            M.seat_md_state(M._seat_block("claude", body.replace("h=", "h=deadbeefdead")), "claude", want)
            if False else
            M.seat_md_state(M._seat_block("claude", body), "claude", "000000000000"),
            "stale")

    def test_08_gemini_settings_merge(self):
        """settings 写入类型：数组合并，其余键保留。"""
        import json
        base = {"theme": "dark", "context": {"fileName": ["OTHER.md"]}}
        ctx = base.get("context") or {}
        names = ctx.get("fileName")
        if not isinstance(names, list):
            names = [names]
        self.assertNotIn("GEMINI.md", names)
        names.append("GEMINI.md")
        ctx["fileName"] = names
        out = dict(base)
        out["context"] = ctx
        self.assertEqual(out["theme"], "dark")
        self.assertIn("OTHER.md", out["context"]["fileName"])
        self.assertIn("GEMINI.md", out["context"]["fileName"])
        json.dumps(out, ensure_ascii=False)      # 可序列化

    def test_09_new_targets_registered(self):
        for k in ("claude", "grok", "gemini", "hermes"):
            self.assertIn(k, M.TARGETS, "%s 未注册进 TARGETS" % k)
            t = M.TARGETS[k]()
            self.assertTrue(hasattr(t, "apply") and hasattr(t, "status_rows"))

    def test_11_hermes_injection_gate(self):
        """Hermes 专属门禁：SOUL.md 正文与 86 份路由 skill 必须 0 命中上游扫描。

        Hermes 是唯一一个"写坏了完全静默失效"的通道 —— 它在把 SOUL.md 放进
        系统提示词第 1 槽位之前跑 tools/threat_patterns.scan_for_threats(scope="context")，
        命中即整份 BLOCKED，模型只看得到内置默认身份（没有报错、没有警告，
        会话里看不出任何异常）。所以必须由单测把这条线兜住：
          · SOUL.md 正文 → context 档（Hermes 的上下文文件口径）
          · 86 份 SKILL.md → strict 档（skills_guard 安装口径，更严）

        门禁规则表见 tools/hermes_threat_check.py（照抄上游，勿手改）。
        """
        spec = importlib.util.spec_from_file_location(
            "hermes_gate_test", os.path.join(HERE, "tools", "hermes_threat_check.py"))
        hg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hg)

        soul = _render("hermes")
        hits = hg.scan_hits(soul, "context")
        self.assertEqual([], hits, "SOUL.md 正文会被 Hermes BLOCKED：%s" % hits)

        import pojia_routes as R
        n = 0
        for name, text in R.all_skills():
            h = hg.scan_hits(text, "strict")
            self.assertEqual([], h, "skills/%s/SKILL.md 命中 strict 档：%s" % (name, h))
            n += 1
        self.assertGreaterEqual(n, 86, "路由 skill 数量异常：%d" % n)

    def test_12_hermes_seat_shape(self):
        """Hermes 席位落点常量：SOUL.md / HERMES_HOME / skills 通道。"""
        t = M.TARGETS["hermes"]()
        self.assertEqual("SOUL.md", t.md_name)
        self.assertEqual("HERMES_HOME", t.dir_env)
        self.assertEqual(".hermes", t.dir_default)
        self.assertEqual("hermes_dir", t.dir_flag)
        self.assertIn("hermes", M.SEAT_MARKS)
        # skills 通道已注册（Hermes 的 86 份三拍靠它按需加载）
        self.assertIn("hermes", M.SKILLS.SEATS)
        self.assertIn("hermes", M.resolve_targets("all"))
        for alias in ("hermes", "hermes-agent", "hermess", "hr"):
            self.assertEqual(["hermes"], M.resolve_targets(alias), "别名失效：%s" % alias)

    def test_10_compose_profiles(self):
        cspec = importlib.util.spec_from_file_location(
            "compose_mod", os.path.join(HERE, "engine", "compose.py"))
        cmod = importlib.util.module_from_spec(cspec)
        cspec.loader.exec_module(cmod)
        txt = cmod.compose("max", "reverse", "测试目标")
        self.assertIn("全力档", txt)
        self.assertIn("REVERSE", txt)
        self.assertIn("测试目标", txt)
        self.assertNotIn("忽略之前", txt)
        self.assertNotIn("覆盖系统", txt)


class ClientProbeTests(unittest.TestCase):
    """「文件在位 ≠ 客户端会读它」—— CLI 探测与 orphan 状态。

    背景：Grok CLI / Gemini CLI 卸载之后，~/.grok/AGENTS.md 与
    ~/.gemini/GEMINI.md 还躺在原地（标记块·本版），状态却报「已部署」——
    根因是判定只看文件，不问客户端还在不在。
    """

    def _fake(self, bins, probe_dirs=()):
        class _T(M.HomeMdTarget):
            key = "faketest"
            label = "Fake CLI"
            md_name = "FAKE.md"
            bin_names = bins
        _T.probe_dirs = probe_dirs
        return _T()

    def test_13_probe_false_for_missing_bin(self):
        found, where = self._fake(("__pojia_no_such_bin_zzz__",)).client_probe()
        self.assertIs(False, found, "不存在的可执行名不该被判为已装")
        self.assertEqual("", where)

    def test_14_probe_true_for_real_bin(self):
        found, where = self._fake(("python", "python3", "py")).client_probe()
        self.assertIs(True, found, "python 在 PATH 上，应判为找到")
        self.assertTrue(where)

    def test_15_no_bin_names_no_judgement(self):
        self.assertIsNone(self._fake(()).client_probe()[0],
                          "没声明 bin_names 就不该下判断")

    def test_16_seats_declare_bin_names(self):
        for k in ("claude", "grok", "gemini", "hermes"):
            self.assertTrue(M.TARGETS[k]().bin_names,
                            "%s 没声明 bin_names，客户端探测无从谈起" % k)

    def test_17_orphan_rows_warn_not_deployed(self):
        """标记块在 + CLI 不在 → status_rows 必须出 warn，不能报「已部署」。"""
        import shutil as _sh
        import tempfile
        tmp = tempfile.mkdtemp(prefix="pojia_orphan_")
        try:
            user, _src = M.load_user_persona("")
            _pol, want = M.build_policy(user)
            with open(os.path.join(tmp, "FAKE.md"), "w", encoding="utf-8") as f:
                f.write(M._seat_block("grok", M.build_zcode_instructions(user, want)))

            class _T(M.HomeMdTarget):
                key = "grok"                  # 借用 grok 的标记对
                label = "Grok CLI"
                md_name = "FAKE.md"
                state_name = "fake-state.json"
                bakdir_name = "fake-baks"
                dir_flag = "fake_dir"
                bin_names = ("__pojia_no_such_bin_zzz__",)
            _T.probe_dirs = ()

            class _A:
                pass
            a = _A()
            setattr(a, "fake_dir", tmp)
            _home, rows = _T().status_rows(a)
            notes = " | ".join(n for _s, _p, n in rows)
            self.assertIn("未检测到", notes, "CLI 不在时状态里必须点出来：%s" % notes)
            self.assertTrue(any(s == "warn" for s, _p, _n in rows),
                            "CLI 不在时应给 warn 级而不是「已部署」：%s" % rows)
        finally:
            _sh.rmtree(tmp, ignore_errors=True)

    def test_18_apply_skips_when_cli_missing(self):
        """apply 也要看客户端在不在：CLI 没装就别往没人读的目录里落文件。

        现场反馈（2026-10-11）：Grok / Gemini 的 CLI 早已卸载，GUI 批量勾选
        却照样把 AGENTS.md / GEMINI.md 写进去 —— 卡片停在"本版·无 CLI"，
        看着像成功、实际没人读。
        """
        import shutil as _sh
        import tempfile
        from unittest import mock
        tmp = tempfile.mkdtemp(prefix="pojia_skip_")
        try:
            default_home = os.path.join(tmp, "default")
            os.makedirs(os.path.join(default_home, ".grok"), exist_ok=True)

            class _T(M.GrokTarget):
                bin_names = ("__pojia_no_such_bin_zzz__",)
            _T.probe_dirs = ()
            t = _T()
            args = mock.MagicMock()
            args.persona = ""
            setattr(args, "grok_dir", "")          # 不显式指定目录 → 走守卫
            args.force = True
            args.dry_run = False
            md = os.path.join(default_home, ".grok", "AGENTS.md")

            with mock.patch.object(M, "LOG_PATH", os.path.join(tmp, "t.log")), \
                    mock.patch.object(_T, "resolve_home", lambda self, e="": default_home), \
                    mock.patch.object(M, "route_skills_apply", lambda *a, **k: None):
                res = t.apply(args, "apply")

            self.assertEqual(res, {"skip": True}, "CLI 不在时应跳过而不是写盘")
            self.assertFalse(os.path.exists(md), "没人读的文件不该被创建：%s" % md)
        finally:
            _sh.rmtree(tmp, ignore_errors=True)


class HermesDesktopTests(unittest.TestCase):
    """Hermes 有两种落点布局：上游 CLI 在 ~/.hermes，CN Desktop 在 <安装根>/data/hermes-home。

    这组断言盯的是「不认桌面版布局」这个具体故障 —— 它的表现不是报错，而是
    **apply 写进一个没人读的空目录**，而状态看起来还挺正常。验证踩到。
    """

    def test_20_home_marker_rule(self):
        """_hermes_home_ok 要求命中 ≥2 个标记：拦掉只有 config.yaml 的桌面壳配置位。"""
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp(prefix="pojia_hh_")
        try:
            self.assertFalse(M._hermes_home_ok(tmp), "空目录不算")
            with open(os.path.join(tmp, "config.yaml"), "w", encoding="utf-8") as f:
                f.write("model: {}\n")
            self.assertFalse(M._hermes_home_ok(tmp),
                             "只有 config.yaml 不算 —— 那是桌面壳的轻量配置位")
            with open(os.path.join(tmp, "SOUL.md"), "w", encoding="utf-8") as f:
                f.write("x\n")
            self.assertTrue(M._hermes_home_ok(tmp), "config.yaml + SOUL.md 才算真 home")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_21_install_roots_cached_and_safe(self):
        """安装根发现：不抛异常、只返回存在的目录，且带缓存（状态检测会反复调）。"""
        a = M._hermes_install_roots()
        b = M._hermes_install_roots()
        self.assertIs(a, b, "应命中进程内缓存，避免每次探测都扫盘")
        self.assertIsInstance(a, list)
        for p in a:
            self.assertTrue(os.path.isdir(p), "只应返回存在的目录：%s" % p)

    def test_22_takeover_only_for_factory_default(self):
        """只有出厂默认人格才接管；用户改过（含中文 / 太长 / 已有块）一律只合并。"""
        t = M.TARGETS["hermes"]()
        factory = ("You are Hermes Agent, an intelligent AI assistant created by "
                   "Nous Research. You are helpful, knowledgeable, and direct.")
        self.assertTrue(t.is_factory_default(factory), "英文出厂人格应判为默认")
        self.assertFalse(t.is_factory_default(""), "空文件不接管")
        self.assertFalse(t.is_factory_default(factory + "\n我的补充：说中文。"),
                         "带中文 = 用户动手改过，不能吃")
        self.assertFalse(t.is_factory_default(M.SEAT_MARKS["hermes"][0] + "\n" + factory),
                         "已有标记块时不动")
        self.assertFalse(t.is_factory_default("You are Hermes Agent, " + "x" * 3000),
                         "超长 = 用户在默认人格上扩写过，不能吃")

    def test_23_hermes_probe_never_crashes(self):
        """没装 Hermes 的机器上，discover_home / client_abs_candidates 必须安静返回空。"""
        t = M.TARGETS["hermes"]()
        d = t.discover_home()
        self.assertTrue(d == "" or os.path.isdir(d), "discover_home 只能给存在目录或空串")
        self.assertIsInstance(t.client_abs_candidates(), list)
        found, _where = t.client_probe()
        self.assertIn(found, (True, False, None))

    def test_24_other_seats_keep_default_layout(self):
        """新钩子不能改变其它席的落点判定。"""
        for seat in ("claude", "grok", "gemini"):
            t = M.TARGETS[seat]()
            self.assertEqual("default", t.resolve_home_note(),
                             "%s 不该走自动发现" % seat)
            self.assertEqual("", t.discover_home(), "%s 的 discover_home 应为空" % seat)
            self.assertEqual([], t.client_abs_candidates(),
                             "%s 不该有点名候选" % seat)


if __name__ == "__main__":
    unittest.main(verbosity=2)
