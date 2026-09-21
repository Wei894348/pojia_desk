# -*- coding: utf-8 -*-
"""pojia_tx 部署事务引擎的回归测试。

覆盖：原子写、标记区间注入的三种情形、快照往返、异常自动回滚、
外部改动检测、越界与体积上限。全程在临时目录里跑，不碰真实目标。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pojia_tx as tx


class TxBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.dir = os.path.join(self.root, "work")
        os.makedirs(self.dir, exist_ok=True)
        tx.set_tx_root(os.path.join(self.root, "tx"))
        self.addCleanup(tx.set_tx_root, None)

    def path(self, name):
        return os.path.join(self.dir, name)

    def write(self, name, text):
        p = self.path(name)
        with open(p, "wb") as f:
            f.write(text.encode("utf-8"))
        return p

    def read(self, name):
        with open(self.path(name), "rb") as f:
            return f.read().decode("utf-8")


class AtomicWriteTests(TxBase):
    def test_creates_file(self):
        p = self.path("a.txt")
        tx.atomic_write(p, b"hello")
        with open(p, "rb") as f:
            self.assertEqual(f.read(), b"hello")

    def test_replaces_whole_file(self):
        p = self.write("a.txt", "old-content-longer")
        tx.atomic_write(p, b"new")
        with open(p, "rb") as f:
            self.assertEqual(f.read(), b"new")

    def test_leaves_no_temp_file(self):
        p = self.write("a.txt", "x")
        tx.atomic_write(p, b"y")
        leftovers = [n for n in os.listdir(self.dir) if n.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_make_dirs(self):
        p = os.path.join(self.dir, "sub", "deep", "a.txt")
        tx.atomic_write(p, b"hi", make_dirs=True)
        self.assertTrue(os.path.exists(p))


class MarkedTests(TxBase):
    B = "# >>> begin"
    E = "# <<< end"

    def block(self, body):
        return "%s\n%s\n%s" % (self.B, body, self.E)

    def test_appends_when_no_marks(self):
        out = tx.marked("line1\nline2\n", self.block("v1"), self.B, self.E)
        self.assertTrue(out.startswith("line1\nline2\n"))
        self.assertIn("v1", out)

    def test_appends_to_empty(self):
        out = tx.marked("", self.block("v1"), self.B, self.E)
        self.assertEqual(out, self.block("v1"))

    def test_replaces_region_and_keeps_tail(self):
        prev = "head\n" + self.block("OLD") + "\ntail\n"
        out = tx.marked(prev, self.block("NEW"), self.B, self.E)
        self.assertIn("NEW", out)
        self.assertNotIn("OLD", out)
        self.assertTrue(out.startswith("head\n"))
        self.assertTrue(out.endswith("\ntail\n"))

    def test_idempotent(self):
        prev = "head\n" + self.block("NEW") + "\ntail\n"
        once = tx.marked(prev, self.block("NEW"), self.B, self.E)
        twice = tx.marked(once, self.block("NEW"), self.B, self.E)
        self.assertEqual(once, twice)

    def test_duplicate_marks_rejected(self):
        prev = self.block("A") + "\n" + self.block("B")
        with self.assertRaises(tx.TxError):
            tx.marked(prev, self.block("C"), self.B, self.E)

    def test_reversed_marks_rejected(self):
        prev = self.E + "\nmiddle\n" + self.B
        with self.assertRaises(tx.TxError):
            tx.marked(prev, self.block("C"), self.B, self.E)

    def test_missing_marks_in_body_rejected(self):
        prev = self.block("A")
        with self.assertRaises(tx.TxError):
            tx.marked(prev, "no marks here", self.B, self.E)


class GuardTests(TxBase):
    def test_within_rejects_escape(self):
        with self.assertRaises(tx.TxError):
            tx.within(self.dir, os.path.join("..", "outside.txt"))

    def test_within_accepts_nested(self):
        got = tx.within(self.dir, os.path.join("a", "b.txt"))
        self.assertTrue(got.startswith(os.path.abspath(self.dir)))

    def test_size_limit(self):
        p = self.write("big.txt", "x" * 64)
        with self.assertRaises(tx.TxError):
            tx.read_bytes(p, limit=16)

    def test_directory_rejected(self):
        with self.assertRaises(tx.TxError):
            tx.read_bytes(self.dir)

    def test_decode_strict_rejects_binary(self):
        with self.assertRaises(UnicodeDecodeError):
            tx.decode_strict(b"\xff\xfe\x00\x01")

    def test_decode_strict_strips_bom(self):
        self.assertEqual(tx.decode_strict(b"\xef\xbb\xbfhi"), "hi")


class SessionTests(TxBase):
    def test_commit_records_and_reports(self):
        p = self.write("a.txt", "before")
        with tx.TxSession("demo") as s:
            s.write(p, "after")
        self.assertEqual(self.read("a.txt"), "after")
        self.assertEqual(s.status, "applied")
        self.assertEqual(s.summary()["changed"], 1)
        hist = tx.list_history()
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["seat"], "demo")

    def test_noop_when_content_identical(self):
        p = self.write("a.txt", "same")
        with tx.TxSession("demo") as s:
            s.write(p, "same")
        self.assertEqual(s.status, "noop")
        self.assertEqual(tx.list_history(), [])

    def test_restore_roundtrip(self):
        p = self.write("a.txt", "original")
        q = self.write("b.txt", "keep")
        with tx.TxSession("demo") as s:
            s.write(p, "patched")
            s.write(q, "patched-too")
        tx_id = s.id
        r = tx.restore(tx_id)
        self.assertEqual(r["restored"], 2)
        self.assertEqual(self.read("a.txt"), "original")
        self.assertEqual(self.read("b.txt"), "keep")

    def test_restore_removes_created_file(self):
        p = self.path("new.txt")
        with tx.TxSession("demo") as s:
            s.write(p, "created")
        self.assertTrue(os.path.exists(p))
        tx.restore(s.id)
        self.assertFalse(os.path.exists(p))

    def test_rollback_on_exception(self):
        p = self.write("a.txt", "original")
        with self.assertRaises(RuntimeError):
            with tx.TxSession("demo") as s:
                s.write(p, "half-written")
                raise RuntimeError("boom")
        self.assertEqual(self.read("a.txt"), "original")
        self.assertEqual(s.status, "rolled-back")

    def test_restore_refuses_when_externally_changed(self):
        p = self.write("a.txt", "original")
        with tx.TxSession("demo") as s:
            s.write(p, "patched")
        with open(p, "wb") as f:
            f.write(b"human-edited")
        with self.assertRaises(tx.TxError) as ctx:
            tx.restore(s.id)
        self.assertIn("又被改动过", str(ctx.exception))
        self.assertEqual(self.read("a.txt"), "human-edited")

    def test_restore_force_overrides(self):
        p = self.write("a.txt", "original")
        with tx.TxSession("demo") as s:
            s.write(p, "patched")
        with open(p, "wb") as f:
            f.write(b"human-edited")
        tx.restore(s.id, force=True)
        self.assertEqual(self.read("a.txt"), "original")

    def test_verify_detects_snapshot_integrity(self):
        p = self.write("a.txt", "original")
        with tx.TxSession("demo") as s:
            s.write(p, "patched")
        self.assertTrue(tx.verify(s.id)["ok"])
        os.remove(os.path.join(tx.tx_root(), s.id, "0000.before"))
        self.assertFalse(tx.verify(s.id)["ok"])

    def test_verify_flags_out_of_date_current(self):
        p = self.write("a.txt", "original")
        with tx.TxSession("demo") as s:
            s.write(p, "patched")
        with open(p, "wb") as f:
            f.write(b"drifted")
        rep = tx.verify(s.id)
        self.assertFalse(rep["ok"])
        self.assertFalse(rep["files"][0]["current_ok"])

    def test_history_filters_by_seat(self):
        for i, seat in enumerate(("codex", "dsh", "codex")):
            with tx.TxSession(seat) as s:
                s.write(self.path("%s.txt" % seat), "%s-%d" % (seat, i))
        self.assertEqual(len(tx.list_history()), 3)
        self.assertEqual(len(tx.list_history(seat="codex")), 2)

    def test_purge_keeps_recent(self):
        ids = []
        for i in range(5):
            with tx.TxSession("demo") as s:
                s.write(self.path("a.txt"), "v%d" % i)
            ids.append(s.id)
        dropped = tx.purge(keep=2)
        self.assertEqual(dropped, 3)
        left = [r["id"] for r in tx.list_history()]
        self.assertEqual(left, ids[::-1][:2])

    def test_bad_snapshot_id_rejected(self):
        with self.assertRaises(tx.TxError):
            tx.load_snapshot("../../etc/passwd")

    def test_snapshot_missing_before_blocks_restore(self):
        p = self.write("a.txt", "original")
        with tx.TxSession("demo") as s:
            s.write(p, "patched")
        os.remove(os.path.join(tx.tx_root(), s.id, "0000.before"))
        r = tx.restore(s.id)
        self.assertEqual(r["restored"], 0)
        self.assertEqual(r["failed"], [p])
        self.assertEqual(r["status"], "partial")


if __name__ == "__main__":
    unittest.main(verbosity=2)
