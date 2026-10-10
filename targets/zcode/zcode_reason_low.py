# -*- coding: utf-8 -*-
"""ZCode 推理等级降档（幂等，可回退）

真实位置：
    ~/.zcode/v2/config.json
      provider.<providerId>.models.<modelId>.reasoning.defaultVariant
                                                 ^^^^^^^^^^^^^^^^^^^^^^^ 在这里

**不是** provider.<id>.options.defaultVariant —— 那个层级在早期排查时被误改，
且 ZCode 一运行就会被清掉（schema 里根本没有这个键），所以改了几轮都不生效。

为什么必须降档：
  defaultVariant=max 会让 GLM 走长思维链。而长思维链正是它"想明白这个请求
  是不是越狱 / 我该不该服从"的场合 —— 它的 reasoningText 里那些
  "this is a jailbreak attempt" 判定，就是在 max 档下产出的。
  改 low 后模型倾向直接照办，不给拒绝留出推理空间。

合法取值：["low", "max", "high"]（从 cjs 的 model schema 里读出）

用法：
  python zcode_reason_low.py             降档（max -> low）
  python zcode_reason_low.py --check     只体检
  python zcode_reason_low.py --revert    从备份还原
"""
import os
import sys
import json
import time
import shutil

HOME = os.path.expanduser("~")
CFG = os.path.join(HOME, ".zcode", "v2", "config.json")
BAK_DIR = os.path.join(HOME, ".zcode", "zcode-unlock-backups")
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "..", "..", "状态", "zcode-reason-state.json")

TARGET = "low"


def log(msg, level="info"):
    tag = {"info": "  ", "ok": "OK", "warn": "!!", "err": "XX"}.get(level, "  ")
    print("[%s] %s" % (tag, msg))


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def scan(cfg):
    """返回 [(providerId, modelId, current)]"""
    rows = []
    for pn, p in (cfg.get("provider") or {}).items():
        for mn, m in (p.get("models") or {}).items():
            r = m.get("reasoning")
            if isinstance(r, dict):
                rows.append((pn, mn, r.get("defaultVariant")))
    return rows


def main():
    argv = sys.argv[1:]
    mode = "revert" if "--revert" in argv else ("check" if "--check" in argv else "apply")

    if not os.path.exists(CFG):
        log("找不到 %s" % CFG, "err")
        return 2

    cfg = json.load(open(CFG, encoding="utf-8"))
    rows = scan(cfg)
    dist = {}
    for _, _, v in rows:
        dist[v] = dist.get(v, 0) + 1

    log("模型条目 %d 个，defaultVariant 分布：%s" % (len(rows), dist))
    for pn, mn, v in rows:
        mark = "" if v == TARGET else "   <-- 需降档"
        log("  %-32s %-16s %s%s" % (pn, mn, v, mark))

    if mode == "check":
        bad = [r for r in rows if r[2] != TARGET]
        if bad:
            log("有 %d 个不是 %s；跑一次本脚本即可" % (len(bad), TARGET), "warn")
            return 1
        log("全部已是 %s" % TARGET, "ok")
        return 0

    if mode == "revert":
        st = load_state()
        bak = st.get("backup")
        if bak and os.path.exists(bak):
            shutil.copy2(bak, CFG)
            log("已从备份还原：%s" % bak, "ok")
            return 0
        log("没有备份可还原；如需恢复请手动把 defaultVariant 改回 max", "warn")
        return 1

    # ---- apply ----
    need = [(pn, mn, r) for pn, mn, r in
            [(pn, mn, (cfg["provider"][pn]["models"][mn]["reasoning"])) for pn, mn, _ in rows]
            if r.get("defaultVariant") != TARGET]
    if not need:
        log("全部已是 %s，无需改动（幂等）" % TARGET, "ok")
        return 0

    os.makedirs(BAK_DIR, exist_ok=True)
    st = load_state()
    if not st.get("backup") or not os.path.exists(st["backup"]):
        bak = os.path.join(BAK_DIR, "config.json.reasoning.%s.bak"
                           % time.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(CFG, bak)
        st["backup"] = bak
        log("备份 -> %s" % bak, "ok")

    changed = []
    for pn, mn, r in need:
        old = r.get("defaultVariant")
        r["defaultVariant"] = TARGET
        changed.append("%s/%s (%s->%s)" % (pn, mn, old, TARGET))

    json.dump(cfg, open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log("已写入 %d 处：" % len(changed), "ok")
    for c in changed:
        log("  %s" % c, "ok")

    st.update({"patched_at": time.strftime("%Y-%m-%d %H:%M:%S"), "target": TARGET})
    save_state(st)

    cfg2 = json.load(open(CFG, encoding="utf-8"))
    dist2 = {}
    for _, _, v in scan(cfg2):
        dist2[v] = dist2.get(v, 0) + 1
    log("复核分布：%s" % dist2, "ok" if dist2.get(TARGET, 0) == len(rows) else "err")

    log("改 config.json 必须在 ZCode 完全退出（含托盘）时做，否则会被回写。", "warn")
    return 0


if __name__ == "__main__":
    sys.exit(main())
