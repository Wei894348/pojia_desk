# -*- coding: utf-8 -*-
"""GUI 桥落盘缓存的回归测试（gui/bridge.py 的 cached / cache_drop）。

起因（2026-09-27 体检发现）：
`cached()` 把**失败结果**也按满 TTL 缓存 300 秒。DSH 的 find_bases 每次要探
3.4 秒，探空一次之后，哪怕用户当场把 DSH 装好了，卡片也要再晾 5 分钟才肯
重探 —— 表现就是状态卡一直显示「未检测到安装」。

锁三件事：
    · 空结果只缓存 bad_ttl（默认 15 秒）
    · 非空结果照旧吃满 TTL
    · 老格式缓存文件（没有 bad 字段）里的空值按失败处理
"""
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_bridge():
    spec = importlib.util.spec_from_file_location(
        "gui_bridge_cache_t", os.path.join(ROOT, "gui", "bridge.py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules["gui_bridge_cache_t"] = m
    spec.loader.exec_module(m)
    return m


class CacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_bridge()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.makedirs(os.path.join(self.tmp.name, "状态"), exist_ok=True)
        self._root = self.m.ROOT
        self.m.ROOT = self.tmp.name
        self.addCleanup(self._restore_root)

    def _restore_root(self):
        self.m.ROOT = self._root

    def cpath(self, key="k"):
        return os.path.join(self.tmp.name, "状态", "_gui_cache_%s.json" % key)

    def test_empty_result_uses_short_ttl(self):
        """探空 → 走出 bad_ttl 就重探（不再卡满 TTL）。"""
        calls = []

        def probe(value):
            calls.append(value)
            return value

        first = self.m.cached("k", ttl=300, bad_ttl=0.05,
                              fn=lambda: probe([]))
        self.assertEqual(first, [])
        self.assertEqual(len(calls), 1)

        # 立刻读：命中空缓存，不重探
        self.m.cached("k", ttl=300, bad_ttl=0.05, fn=lambda: probe(["late"]))
        self.assertEqual(len(calls), 1, "空结果在 bad_ttl 内就该命中缓存")

        time.sleep(0.08)   # 越过 bad_ttl，但远没到 ttl
        again = self.m.cached("k", ttl=300, bad_ttl=0.05, fn=lambda: probe(["late"]))
        self.assertEqual(again, ["late"], "越过 bad_ttl 后必须重探")
        self.assertEqual(len(calls), 2)

    def test_nonempty_result_keeps_full_ttl(self):
        """探到东西 → 吃满 TTL，别为了这条修过头。"""
        calls = []

        def probe(v):
            calls.append(v)
            return v

        got = self.m.cached("k2", ttl=300, bad_ttl=0.05, fn=lambda: probe(["base"]))
        self.assertEqual(got, ["base"])
        time.sleep(0.08)
        got = self.m.cached("k2", ttl=300, bad_ttl=0.05, fn=lambda: probe(["changed"]))
        self.assertEqual(got, ["base"], "非空结果在 TTL 内不该重探")
        self.assertEqual(len(calls), 1)

    def test_legacy_cache_without_bad_field(self):
        """老缓存文件没有 bad 字段：空值按失败处理，不会撑满 TTL。"""
        with open(self.cpath("legacy"), "w", encoding="utf-8") as f:
            json.dump({"t": time.time() - 60, "v": []}, f)   # 旧格式、空值、一分钟前
        calls = []

        def probe(v):
            calls.append(v)
            return v

        got = self.m.cached("legacy", ttl=300, bad_ttl=0.01, fn=lambda: probe(["fresh"]))
        self.assertEqual(got, ["fresh"], "老格式的空缓存不该被当成有效结论")
        self.assertEqual(len(calls), 1)

    def test_legacy_nonempty_still_hits(self):
        with open(self.cpath("legacy2"), "w", encoding="utf-8") as f:
            json.dump({"t": time.time() - 60, "v": ["x"]}, f)
        called = []
        got = self.m.cached("legacy2", ttl=300, bad_ttl=0.01,
                            fn=lambda: called.append(1) or ["y"])
        self.assertEqual(got, ["x"])
        self.assertEqual(called, [])

    def test_new_format_records_bad_flag(self):
        self.m.cached("k3", ttl=300, fn=lambda: [])
        with open(self.cpath("k3"), encoding="utf-8") as f:
            d = json.load(f)
        self.assertIs(d.get("bad"), True)

        self.m.cached("k4", ttl=300, fn=lambda: ["a"])
        with open(self.cpath("k4"), encoding="utf-8") as f:
            d = json.load(f)
        self.assertIs(d.get("bad"), False)

    def test_cache_drop_invalidates(self):
        self.m.cached("k5", ttl=300, fn=lambda: ["a"])
        self.assertTrue(os.path.isfile(self.cpath("k5")))
        self.m.cache_drop("k5")
        self.assertFalse(os.path.isfile(self.cpath("k5")))
        # 探测函数重新跑 = 拿到现场而不是旧结论
        got = self.m.cached("k5", ttl=300, fn=lambda: ["b"])
        self.assertEqual(got, ["b"])

    def test_cache_drop_missing_file_is_noop(self):
        self.m.cache_drop("never_existed")   # 不抛异常

    def test_corrupt_cache_is_ignored(self):
        with open(self.cpath("bad"), "w", encoding="utf-8") as f:
            f.write("{ 这不是 json")
        got = self.m.cached("bad", ttl=300, fn=lambda: ["ok"])
        self.assertEqual(got, ["ok"])


if __name__ == "__main__":
    unittest.main()
