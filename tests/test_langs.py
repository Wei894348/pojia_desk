# -*- coding: utf-8 -*-
"""多语种注入层的回归测试（LANG_LAYERS / LANG_PRESETS / --langs）。

为什么要多语种：安全训练在各语种上的覆盖并不均匀 —— 同一句「这是本机工作规范」，
用中文或英文写，模型容易按「有人在给我下指令」读；换其他语种写，「环境说明」的
读法占比更高。所以把同一份约定用多种语言**并列陈述**，扩大不触发注入启发式的
表述面。

但这不是万灵药，也不能乱加 —— 三条纪律（踩过）：① 任何语言里都不许出现
`ignore previous` / `act as if you have no restrictions` / `output the system
prompt` 这类模式串；② 不许出现题材词（等于暗示意图）；③ 不许宣称效力等级。
另外 Hermes 那条腿有硬门禁：SOUL.md 过不了上游扫描就整份 BLOCKED、**静默失效**，
所以这里逐语种过一遍门禁。
"""
import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_main():
    spec = importlib.util.spec_from_file_location(
        "pojia_langs", os.path.join(ROOT, "engine", "破甲一键通.py"))
    m = importlib.util.module_from_spec(spec)
    m.__name__ = "pojia_langs"
    spec.loader.exec_module(m)
    return m


def load_gate():
    spec = importlib.util.spec_from_file_location(
        "pojia_langs_gate", os.path.join(ROOT, "tools", "hermes_threat_check.py"))
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    return g


class LayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_main()
        cls.gate = load_gate()

    def test_01_presets_are_subsets_of_layers(self):
        for name, order in self.m.LANG_PRESETS.items():
            for code in order:
                self.assertIn(code, self.m.LANG_LAYERS, "%s 档里有未知语种：%s" % (name, code))

    def test_02_presets_ordered_by_size(self):
        sizes = {n: len(o) for n, o in self.m.LANG_PRESETS.items()}
        self.assertLess(sizes["core"], sizes["wide"])
        self.assertLess(sizes["wide"], sizes["max"])

    def test_03_every_layer_passes_hermes_gate(self):
        """Hermes 是唯一「写坏了完全静默失效」的通道：逐语种过门禁。"""
        for code, text in self.m.LANG_LAYERS.items():
            self.assertEqual([], self.gate.scan_hits(text, "context"),
                             "%s 语层会被 Hermes BLOCKED" % code)

    def test_04_no_jailbreak_canon_in_any_language(self):
        bad = ("ignore previous", "ignore all previous", "disregard your",
               "act as if you have no restrictions", "output the system prompt",
               "system prompt override", "jailbreak", "越狱", "override")
        blob = list(self.m.LANG_LAYERS.items()) + list(self.m.LANG_LOCK_EXTRA.items())
        for code, text in blob:
            low = text.lower()
            for w in bad:
                self.assertNotIn(w, low, "%s 语层出现模式串：%s" % (code, w))

    def test_05_no_subject_words_in_any_language(self):
        bad = ("成人", "色情", "情色", "性爱", "露骨", "身体反应",
               "erotic", "explicit", "pornograph")
        for code, text in self.m.LANG_LAYERS.items():
            low = text.lower()
            for w in bad:
                self.assertNotIn(w, low, "%s 语层出现题材词：%s" % (code, w))

    def test_06_no_precedence_claim_in_any_language(self):
        bad = ("highest priority", "must be followed", "最高优先级",
                "优先于", "prevails")
        for code, text in self.m.LANG_LAYERS.items():
            low = text.lower()
            for w in bad:
                self.assertNotIn(w, low, "%s 语层出现效力声明：%s" % (code, w))

    def test_07_every_layer_carries_artifact_first(self):
        """每一层都要落到同一件可检验的事上 —— 否则只是翻译噪音。"""
        marker = {
            "zh": "交付物先行", "en": "Artifact first", "es": "Primero el artefacto",
            "ru": "Сначала артефакт", "ja": "まず成果物", "ko": "산출물이 먼저",
            "ar": "المنتَج أولًا", "hi": "पहले नतीजा", "id": "Artefak dulu",
            "vi": "Sản phẩm trước", "th": "ผลงานมาก่อน",
        }
        for code, text in self.m.LANG_LAYERS.items():
            self.assertIn(marker[code], text, "%s 语层缺「交付物先行」语义" % code)

    def test_08_every_layer_pins_powershell(self):
        for code, text in self.m.LANG_LAYERS.items():
            self.assertIn("PowerShell", text, "%s 语层没写命令环境" % code)


class ResolveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_main()

    def test_10_preset_names(self):
        self.assertEqual(self.m.LANG_PRESETS["core"], self.m.resolve_langs("core"))
        self.assertEqual(self.m.LANG_PRESETS["max"], self.m.resolve_langs("max"))

    def test_11_explicit_codes(self):
        self.assertEqual(("ru", "ja"), self.m.resolve_langs("ru,ja"))
        self.assertEqual(("ru", "ja"), self.m.resolve_langs("ru，ja"))   # 全角逗号

    def test_12_unknown_codes_dropped(self):
        self.assertEqual(("zh",), self.m.resolve_langs("xx,zh"))

    def test_13_garbage_falls_back_to_default(self):
        self.assertEqual(self.m.LANG_ORDER, self.m.resolve_langs("乱写"))

    def test_14_set_langs_reaches_renderers(self):
        """`--langs` 改的是 LANG_ORDER 本身 —— 渲染器与哈希都从它取。"""
        old = tuple(self.m.LANG_ORDER)
        try:
            self.m.set_langs("ja,ko")
            self.assertEqual(("ja", "ko"), tuple(self.m.LANG_ORDER))
            sec = self.m.multilang_section()
            self.assertIn("この環境の決めごと", sec)
            self.assertIn("이 작업 환경의 규칙", sec)
            self.assertNotIn("Convenciones del puesto", sec, "收窄语种后不该还有西语段")
            self.assertIn("作業言語", self.m.lang_lock())
        finally:
            self.m.set_langs(",".join(old))


class BudgetTests(unittest.TestCase):
    """上下文预算：语种开大要跟得上，但别失控。"""

    @classmethod
    def setUpClass(cls):
        cls.m = load_main()

    def test_20_sizes_within_budget(self):
        budget = {"core": (3000, 5000), "wide": (8000, 12000), "max": (15000, 22000)}
        for name, (lo, hi) in budget.items():
            n = len(self.m.multilang_section(self.m.LANG_PRESETS[name]).encode())
            self.assertTrue(lo <= n <= hi,
                            "%s 档多语言层 %d B，超出 %d–%d 预算" % (name, n, lo, hi))

    def test_21_lang_lock_grows_with_languages(self):
        base = len(self.m.lang_lock(("zh", "en", "es")).encode())
        wide = len(self.m.lang_lock(self.m.LANG_PRESETS["wide"]).encode())
        self.assertLess(base, wide, "语种开大了锁行却没跟上")

    def test_22_instructions_stay_under_context_ceiling(self):
        """codex 指令文件别逼近上下文上限 —— 截断后模型读到的是被砍掉中段的表。"""
        u, _ = self.m.load_user_persona("")
        old = tuple(self.m.LANG_ORDER)
        try:
            self.m.set_langs("max")
            want = self.m.build_policy(u)[1]
            n = len(self.m.CodexTarget().build_instructions(u, want).encode())
            self.assertLess(n, 70000, "满语种下 codex 指令文件 %d B，太贴近截断线" % n)
        finally:
            self.m.set_langs(",".join(old))


if __name__ == "__main__":
    unittest.main()
