#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render_routes.py —— 薄壳。真正的实现在 engine/pojia_routes.py。

    python tools/render_routes.py           装配并写 route-layer.md
    python tools/render_routes.py --check    只比较，不写
    python tools/render_routes.py --audit    一致性自检
"""

from __future__ import annotations

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load():
    """显式从 engine/ 加载 —— 被 importlib 加载时脚本目录不在 sys.path。"""
    spec = importlib.util.spec_from_file_location("pojia_routes",
                                                  os.path.join(ROOT, "engine", "pojia_routes.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pojia_routes"] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    R = load()

    if "--audit" in sys.argv:
        a = R.audit()
        print("自检：%s" % ("OK" if a["ok"] else "有问题"))
        for p in a["problems"]:
            print("  [!] %s" % p)
        print("  叶子 %d 个 / SKILL.md %d 份（父类目 %d + 小类 %d）"
              % (a["leaves"], a["skills"], len(R.CAT_ORDER), a["leaves"]))
        return 0 if a["ok"] else 1

    text = R.render_route_layer()
    data = text.encode("utf-8")

    if "--check" in sys.argv:
        old = ""
        if os.path.isfile(R.LAYER_FILE):
            with open(R.LAYER_FILE, "r", encoding="utf-8", newline="") as f:
                old = f.read().replace("\r\n", "\n")
        same = old == text
        print("check: %s（新 %d B / 旧 %d B）"
              % ("一致" if same else "有差异", len(data), len(old.encode("utf-8"))))
        return 0 if same else 1

    with open(R.LAYER_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

    ids = R.leaf_ids()
    per_cat = {}
    for i in ids:
        c = R.category_of(i)
        per_cat[c] = per_cat.get(c, 0) + 1
    print("route-layer.md 已写：%d B" % len(data))
    print("  组成：NORMALIZER %d + ROUTER %d + INDEX %d + CONTRACT %d"
          % (len(R.read_source("NORMALIZER.md").encode("utf-8")),
             len(R.read_source("ROUTER.md").encode("utf-8")),
             len(R.render_index().encode("utf-8")),
             len(R.read_source("CONTRACT.md").encode("utf-8"))))
    print("  叶子 %d 个（内联 0 个，只给索引）：%s"
          % (len(ids), " ".join("%s=%d" % (c, per_cat.get(c, 0)) for c in R.CAT_ORDER)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
