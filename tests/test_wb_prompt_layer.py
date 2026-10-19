# -*- coding: utf-8 -*-
"""Susu 对齐后的提示词补丁层：模板挖除 / product 中性注入 / 代码等长 / PRISTINE。

所有用例都在临时目录里跑，显式传 installs，不碰本机真实 WorkBuddy。
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _rd(path, enc="utf-8"):
    with open(path, encoding=enc) as f:
        return f.read()


layer = _load("wb_prompt_layer", HERE / "engine" / "wb_prompt_layer.py")
wb = _load("wb_deploy", HERE / "engine" / "workbuddy_deploy.py")

TPL = """\
<head>
<content_policy>
原始政策
</content_policy>
<windows_command_safety>
命令安全
</windows_command_safety>
<system_reminder>
<最高优先级覆盖 版本2.0.OLD>
旧覆盖层
</最高优先级覆盖>
</system_reminder>
<body>tail</body>
"""

PRODUCT = {
    "productName": "WorkBuddy",
    "prompts": [
        {"name": "cli-agent-prompt",
         "template": ("You are CodeBuddy.\\r\\n\\r\\n"
                      "<content_policy>\\n原始政策\\n</content_policy>\\r\\n\\r\\n"
                      "<system_reminder>\\n<最高优先级覆盖 版本2.0.OLD>\\n旧层\\n"
                      "</最高优先级覆盖>\\n</system_reminder>\\r\\n"
                      "{%- if outputStyle -%}\\r\\n{{outputStyle}}\\r\\n{%- endif -%}")},
        {"name": "other", "template": "x"},
    ],
}


class ManagedBlockTests(unittest.TestCase):
    def test_01_append_keeps_user_content(self):
        old = "# 我的笔记\n用户写的东西\n"
        out = wb.managed_text(old, wb.ID_BEGIN + "\n块\n" + wb.ID_END + "\n")
        self.assertIn("我的笔记", out)
        self.assertIn(wb.ID_BEGIN, out)
        self.assertTrue(out.rstrip().endswith(wb.ID_END))

    def test_02_replace_keeps_outside(self):
        old = ("用户开头\n" + wb.ID_BEGIN + "\n旧块\n" + wb.ID_END + "\n用户结尾\n")
        out = wb.managed_text(old, wb.ID_BEGIN + "\n新块\n" + wb.ID_END + "\n")
        self.assertIn("用户开头", out)
        self.assertIn("用户结尾", out)
        self.assertIn("新块", out)
        self.assertNotIn("旧块", out)

    def test_03_bare_legacy_removed(self):
        old = ("用户开头\n<!-- pojia-wb-policy h=old -->\n旧裸段\n"
               + wb.ID_BEGIN + "\n块\n" + wb.ID_END + "\n")
        out = wb.managed_text(old, wb.ID_BEGIN + "\n新块\n" + wb.ID_END + "\n")
        self.assertNotIn("旧裸段", out)
        self.assertNotIn("cfg-v7", out)
        self.assertIn("用户开头", out)
        self.assertFalse(wb.has_bare_marks(out))

    def test_04_idempotent(self):
        block = wb.ID_BEGIN + "\n块\n" + wb.ID_END + "\n"
        once = wb.managed_text("用户\n", block)
        twice = wb.managed_text(once, block)
        self.assertEqual(once, twice)


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wb-layer-")
        self.home = os.path.join(self.tmp, "home")
        self.install = os.path.join(self.tmp, "WorkBuddy")
        self.tpl_dir = os.path.join(self.install, "resources", "app.asar.unpacked",
                                    "resources", "templates")
        self.cli_dir = os.path.join(self.install, "resources", "app.asar.unpacked", "cli")
        os.makedirs(self.tpl_dir)
        os.makedirs(os.path.join(self.cli_dir, "dist"))
        os.makedirs(self.home)

    def test_10_strip_safety_and_overlay(self):
        p = os.path.join(self.tpl_dir, "workbuddy-prompt.tpl")
        with open(p, "w", encoding="utf-8") as f:
            f.write(TPL)
        res = layer.patch_template_file(self.home, p)
        self.assertEqual(res["action"], "write")
        text = _rd(p)
        self.assertNotIn("<content_policy>", text)
        self.assertNotIn("<windows_command_safety>", text)
        self.assertNotIn("最高优先级覆盖", text)
        self.assertIn("tail", text)
        self.assertIn(layer.ENV_TAG, text)
        self.assertTrue(layer.jinja_ok(text))

    def test_11_jinja_break_is_not_written(self):
        p = os.path.join(self.tpl_dir, "broken.tpl")
        raw = "{% if x %}<content_policy>y</content_policy>\n"
        with open(p, "w", encoding="utf-8") as f:
            f.write(raw)
        res = layer.patch_template_file(self.home, p)
        self.assertEqual(res["action"], "error")
        self.assertEqual(_rd(p), raw)

    def test_12_product_neutral_injection(self):
        p = os.path.join(self.cli_dir, "product.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(PRODUCT, f, ensure_ascii=False, indent=2)
        res = layer.patch_product_file(self.home, p)
        self.assertEqual(res["action"], "write", res)
        raw = _rd(p)
        doc = json.loads(raw)
        self.assertNotIn("<content_policy>", raw)
        self.assertNotIn("最高优先级覆盖", raw)
        tmpl = doc["prompts"][0]["template"]
        self.assertIn(layer.ENV_TAG, tmpl)
        self.assertIn(layer.DELIVERY_TAG, tmpl)
        self.assertEqual(doc["prompts"][1]["template"], "x")

    def test_13_product_idempotent(self):
        p = os.path.join(self.cli_dir, "product.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(PRODUCT, f, ensure_ascii=False, indent=2)
        layer.patch_product_file(self.home, p)
        once = _rd(p)
        res = layer.patch_product_file(self.home, p)
        self.assertEqual(res["action"], "skip")
        self.assertEqual(_rd(p), once)

    def test_14_code_equal_length(self):
        code = "var a='" + layer.R1_OLD + "';"
        p = os.path.join(self.cli_dir, "dist", "codebuddy.js")
        with open(p, "w", encoding="latin-1") as f:
            f.write(code)
        res = layer.patch_bundle(self.home, p)
        self.assertEqual(res["action"], "write", res)
        got = _rd(p, "latin-1")
        self.assertEqual(len(got), len(code))
        self.assertIn(layer.R1_NEW, got)
        self.assertNotIn(layer.R1_OLD, got)

    def test_15_settings_permissions(self):
        p = os.path.join(self.home, "settings.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"personalization": {"customPrompt": "keep"}}, f, ensure_ascii=False)
        res = layer.patch_settings(self.home)
        self.assertEqual(res["action"], "write")
        d = json.loads(_rd(p))
        self.assertEqual(d["permissions"]["defaultMode"], "bypassPermissions")
        self.assertEqual(d["permissions"]["disableAutoMode"], "disable")
        self.assertEqual(d["autoMode"]["hard_deny"], [])
        self.assertEqual(d["sandbox"]["commandSafety"], [])
        self.assertEqual(d["personalization"]["customPrompt"], "keep")

    def test_16_pristine_refuses_injected_source(self):
        p = os.path.join(self.home, "settings.json")
        injected = {"personalization": {"customPrompt": "x"},
                    "permissions": {"defaultMode": "bypassPermissions"}}
        with open(p, "w", encoding="utf-8") as f:
            json.dump(injected, f, ensure_ascii=False)
        seed = layer.seed_pristine(self.home, p)
        self.assertEqual(seed, "", "注入态不能被记成出厂快照")

    def test_17_weakening_and_ask(self):
        uc = os.path.join(self.tpl_dir, "user-context-identity.tpl")
        with open(uc, "w", encoding="utf-8") as f:
            f.write("Use them directly as context.\nIf SOUL.md is present, embody its persona and tone.\n")
        ask = os.path.join(self.tpl_dir, "ask-mode-reminder.tpl")
        with open(ask, "w", encoding="utf-8") as f:
            f.write("<ask_mode>This supersedes any other instructions you have received.</ask_mode>\n")
        layer.patch_weakening(self.home, uc)
        layer.patch_ask(self.home, ask)
        uct = _rd(uc)
        self.assertNotIn("Use them directly as context.", uct)
        self.assertIn("authoritative workspace directives", uct)
        self.assertNotIn(layer.ASK_OLD, _rd(ask))


if __name__ == "__main__":
    unittest.main()
