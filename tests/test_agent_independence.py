# -*- coding: utf-8 -*-
"""开关/备注按 Agent 隔离：一个 Agent 关掉叶子，另一个 Agent 仍是开启。"""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "doc_inject_independence", HERE / "engine" / "doc_inject.py")
di = importlib.util.module_from_spec(SPEC)
sys.modules["doc_inject_independence"] = di
SPEC.loader.exec_module(di)


class AgentIndependenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pojia-agent-")
        self.root = self.tmp.name
        self.state_dir = os.path.join(self.root, "状态")
        self.legacy = os.path.join(self.state_dir, "_doc_inject.json")
        os.makedirs(os.path.join(self.root, "content", "routes", "leaves"))
        self.leaf = os.path.join(self.root, "content", "routes", "leaves", "sw-unpack.md")
        with open(self.leaf, "w", encoding="utf-8") as f:
            f.write("# leaf\n")
        self.patches = [
            mock.patch.object(di, "ROOT", self.root),
            mock.patch.object(di, "STATE_DIR", self.state_dir),
            mock.patch.object(di, "LEGACY_FILE", self.legacy),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_leaf_toggle_is_per_agent(self):
        self.assertTrue(di.enabled(self.leaf, "codex"))
        di.set_table({"content/routes/leaves/sw-unpack.md": False}, "codex")
        self.assertFalse(di.enabled(self.leaf, "codex"))
        self.assertTrue(di.enabled(self.leaf, "claude"))
        self.assertTrue(di.enabled(self.leaf, "workbuddy"))

    def test_state_files_are_separate(self):
        di.set_table({"content/routes/leaves/sw-unpack.md": False}, "codex")
        di.set_table({"content/routes/leaves/sw-unpack.md": False}, "workbuddy")
        self.assertNotEqual(di.state_path("codex"), di.state_path("workbuddy"))
        self.assertTrue(di.state_path("codex").endswith("_doc_inject_codex.json"))
        self.assertTrue(di.state_path("workbuddy").endswith("_doc_inject_workbuddy.json"))
        self.assertEqual(di.state_path("wb"), di.state_path("workbuddy"))

    def test_legacy_table_migrates_once(self):
        os.makedirs(self.state_dir, exist_ok=True)
        with open(self.legacy, "w", encoding="utf-8") as f:
            f.write('{"content/routes/leaves/sw-unpack.md": false}')
        self.assertFalse(di.enabled(self.leaf, "codex"))
        self.assertFalse(di.enabled(self.leaf, "claude"))
        di.set_table({}, "codex")
        self.assertTrue(di.enabled(self.leaf, "codex"))
        self.assertFalse(di.enabled(self.leaf, "claude"))


if __name__ == "__main__":
    unittest.main()
