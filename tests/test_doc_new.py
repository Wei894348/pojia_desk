# -*- coding: utf-8 -*-
"""指令页「新建」必须落在当前 Agent 自己的 instructions/ 下，不能写共享目录。"""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("bridge_doc_test", HERE / "gui" / "bridge.py")
bridge = importlib.util.module_from_spec(SPEC)
sys.modules["bridge_doc_test"] = bridge
SPEC.loader.exec_module(bridge)


class DocNewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pojia-doc-")
        self.root = os.path.join(self.tmp, "root")
        os.makedirs(os.path.join(self.root, "content", "seats"))
        os.makedirs(os.path.join(self.root, "targets"))
        self.patches = [
            mock.patch.object(bridge, "ROOT", self.root),
            mock.patch.object(bridge, "CONTENT", os.path.join(self.root, "content")),
            mock.patch.object(bridge, "TARGETS", os.path.join(self.root, "targets")),
            mock.patch.object(bridge, "DOCS_DIRTY_FILE",
                              os.path.join(self.root, "状态", "_docs_dirty")),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_instruction_goes_to_current_agent_dir(self):
        r = bridge.cmd_doc_new("codex", "instruction", "trial")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["path"],
                         os.path.join(self.root, "targets", "codex",
                                      "instructions", "trial.md"))
        self.assertTrue(os.path.isfile(r["path"]))
        with open(r["path"], encoding="utf-8") as f:
            self.assertIn("<!-- pojia:user-doc agent=codex cat=instruction -->", f.read())

    def test_custom_goes_to_current_agent_dir(self):
        r = bridge.cmd_doc_new("claude", "custom", "mine")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["path"],
                         os.path.join(self.root, "targets", "claude",
                                      "instructions", "custom_mine.md"))
        self.assertTrue(os.path.isfile(r["path"]))

    def test_fixed_category_is_rejected(self):
        r = bridge.cmd_doc_new("codex", "agents", "nope")
        self.assertFalse(r["ok"])
        self.assertIn("固定落点", r["error"])

    def test_user_doc_can_be_deleted(self):
        r = bridge.cmd_doc_new("codex", "instruction", "trial")
        d = bridge.cmd_doc_delete(r["id"])
        self.assertTrue(d["ok"], d)
        self.assertFalse(os.path.exists(r["path"]))

    def test_import_md_goes_to_current_agent_dir(self):
        src = os.path.join(self.tmp, "outside.md")
        with open(src, "w", encoding="utf-8") as f:
            f.write("# imported\n\nhello\n")
        r = bridge.cmd_doc_import("codex", src)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["path"],
                         os.path.join(self.root, "targets", "codex",
                                      "instructions", "outside.md"))
        with open(r["path"], encoding="utf-8") as f:
            text = f.read()
        self.assertIn("# imported", text)
        self.assertIn("<!-- pojia:user-doc agent=codex", text)

    def test_imported_file_can_be_deleted(self):
        src = os.path.join(self.tmp, "outside.md")
        with open(src, "w", encoding="utf-8") as f:
            f.write("# imported\n")
        r = bridge.cmd_doc_import("codex", src)
        self.assertTrue(r["ok"], r)
        d = bridge.cmd_doc_delete(r["id"])
        self.assertTrue(d["ok"], d)
        self.assertFalse(os.path.exists(r["path"]))

    def test_import_name_collision_gets_suffix(self):
        src = os.path.join(self.tmp, "same.md")
        for i in range(2):
            with open(src, "w", encoding="utf-8") as f:
                f.write("# same %d\n" % i)
            r = bridge.cmd_doc_import("claude", src)
            self.assertTrue(r["ok"], r)
            self.assertTrue(r["path"].endswith(("same.md", "same-2.md")))

    def test_builtin_file_cannot_be_deleted(self):
        lib = os.path.join(self.root, "targets", "codex", "instructions")
        os.makedirs(lib)
        p = os.path.join(lib, "unlock.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("# built in\n")
        d = bridge.cmd_doc_delete("targets/codex/instructions/unlock.md")
        self.assertFalse(d["ok"])
        self.assertTrue(os.path.isfile(p))


if __name__ == "__main__":
    unittest.main()
