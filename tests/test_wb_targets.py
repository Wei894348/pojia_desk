"""WorkBuddy 靶点收集的回归测试。

锁住一件事：`find_targets()` 把运行时缓存
`<data>/cache/acc-product-config-v3.json` 也当成了政策靶点。

它其实是 WorkBuddy 自管的派生文件（apply 的语义是**删除**让程序下次启动重建，
从不改写），于是：
  · 状态统计永远多出「旧人格宽松 1 个」；
  · GUI 的 wb 卡（bridge 用 loose == pol_n 判 current）**恒判 stale**；
  · console 侧却判 OK —— 同一事实两个相反结论。
缓存哈希永远追不上本版，所以那个 stale 是个**永远消不掉的假警报**；
用户会以为破甲没落盘，进而重复 apply。

判定拆两条：① 缓存**不收**；② 普通 .tpl 照旧收（防止改过头）。
"""
import importlib.util
import os

# 席位独立源会让单测读到仓库里的 content/seats/ —— 测试一律走共享管线
os.environ.setdefault("POJIA_NO_SEAT_OVERRIDE", "1")
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "engine" / "破甲一键通.py"
SPEC = importlib.util.spec_from_file_location("pojia", SOURCE)
pojia = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pojia)

CACHE_REL = os.path.join("cache", "acc-product-config-v3.json")


def _norm(paths):
    return {os.path.abspath(p) for p in paths}


class WbTargetCollectTests(unittest.TestCase):
    def _target(self):
        return pojia.TARGETS["wb"]()

    def test_runtime_cache_not_collected(self):
        """派生缓存不得进靶点 —— 否则 wb 卡恒 stale。"""
        with tempfile.TemporaryDirectory() as td:
            cache = os.path.join(td, CACHE_REL)
            os.makedirs(os.path.dirname(cache))
            with open(cache, "w", encoding="utf-8") as fh:
                fh.write("{}")

            got = _norm(self._target().find_targets(td, ""))
            self.assertNotIn(os.path.abspath(cache), got)

    def test_cache_under_real_layout_ignored(self):
        """缓存与 plugins 同时存在时，收 plugins 的 tpl、放过缓存。"""
        with tempfile.TemporaryDirectory() as td:
            cache = os.path.join(td, CACHE_REL)
            os.makedirs(os.path.dirname(cache))
            with open(cache, "w", encoding="utf-8") as fh:
                fh.write("{}")

            pdir = os.path.join(td, "plugins", "welcomemode")
            os.makedirs(pdir)
            tpl = os.path.join(pdir, "prompt.tpl")
            with open(tpl, "w", encoding="utf-8") as fh:
                fh.write("<content_policy>\nx\n</content_policy>")

            got = _norm(self._target().find_targets(td, ""))
            self.assertIn(os.path.abspath(tpl), got)
            self.assertNotIn(os.path.abspath(cache), got)

    def test_backup_tpl_still_skipped(self):
        """.unlockbak 形式的 tpl 依旧被跳过（行为不回退）。"""
        with tempfile.TemporaryDirectory() as td:
            pdir = os.path.join(td, "plugins", "x")
            os.makedirs(pdir)
            bak = os.path.join(pdir, "prompt.tpl" + pojia.BAK_WB)
            with open(bak, "w", encoding="utf-8") as fh:
                fh.write("x")

            got = _norm(self._target().find_targets(td, ""))
            self.assertNotIn(os.path.abspath(bak), got)


if __name__ == "__main__":
    unittest.main()
