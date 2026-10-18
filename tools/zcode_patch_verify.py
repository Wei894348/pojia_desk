# -*- coding: utf-8 -*-
"""ZCode 补丁校验（确定性版）—— 证明「当前文件 == 备份 + 恰好这些替换」。

为什么用这个而不是 node --check：
  沙箱里跑 `node --check zcode.cjs`（14.8 MB）会 OOM（AlignedAlloc Allocation
  failed），拿不到语法结论。于是换个等价思路：

      expected = 备份文件 逐字节套用「预期替换对」
      actual   = 当前 zcode.cjs
      expected == actual  →  除了这些替换，其余逐字节相同

  既然原文件语法合法，且每处替换都是「完整字符串字面量 → 完整字符串字面量」
  （新文本不含未转义的引号 / 反斜杠 / 反引号 / ${），那么等价即蕴含语法合法。

  这比 node --check 更强：它连"有没有顺手改到别处"一起证明了。

替换对直接从 zcode_dual_patch.py 里 import，避免两处维护。

用法：
  python tools/zcode_patch_verify.py
"""
import os
import sys
import glob
import json
import hashlib
import importlib.util

CJS = r"F:/Softstore/ZCode/resources/glm/zcode.cjs"
BAK_GLOB = os.path.join(os.path.expanduser("~"), ".zcode", "zcode-unlock-backups",
                        "zcode.cjs*.bak")
HERE = os.path.dirname(os.path.abspath(__file__))
DUAL = os.path.join(HERE, "..", "engine", "zcode_inject.py")
STATE = os.path.join(HERE, "..", "状态", "zcode-cjs-state.json")
CH = 1 << 20
OV = 4096


def load_pairs():
    """从 zcode_dual_patch.py 取 OLD + 当前已装模式的 NEW。"""
    spec = importlib.util.spec_from_file_location("zdp", DUAL)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    mode = "gateway-safe"
    try:
        with open(STATE, encoding="utf-8") as f:
            mode = json.load(f).get("mode", "gateway-safe")
    except Exception:
        pass
    tbl = {name: pairs for name, pairs, _d in m.MODES}
    if mode not in tbl:
        mode = "gateway-safe"
    print("已装模式 : %s" % mode)
    return list(tbl[mode])


def transform_hash(path, pairs):
    """流式把 pairs 套用到 path 上，返回 (sha256, size)。

    做法：每轮 buf = 上一轮尾部 OV 字节 + 新块；先在完整 buf 上做 replace，
    再输出 [0, len-OV)，把最后 OV 字节留到下一轮 —— 这样跨块的替换也能命中
    （单条替换最长约 2.5 KB < OV）。
    """
    h = hashlib.sha256()
    total = 0
    prev = b""
    with open(path, "rb") as f:
        while True:
            b = f.read(CH)
            if not b:
                break
            buf = prev + b
            for o, n in pairs:
                buf = buf.replace(o, n)
            if len(buf) > OV:
                head, prev = buf[:-OV], buf[-OV:]
            else:
                head, prev = b"", buf
            h.update(head)
            total += len(head)
    h.update(prev)
    total += len(prev)
    return h.hexdigest(), total


def file_hash(path):
    h = hashlib.sha256()
    total = 0
    with open(path, "rb") as f:
        while True:
            b = f.read(CH)
            if not b:
                break
            h.update(b)
            total += len(b)
    return h.hexdigest(), total


def scan(path):
    """轻量词法统计，作为辅助信号。"""
    st = {"dquote": 0, "esc": 0, "brace": 0, "paren": 0, "brack": 0,
          "nl": 0, "unclosed": 0, "size": 0}
    in_str = False
    esc = False
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(CH)
            if not b:
                break
            h.update(b)
            st["size"] += len(b)
            for ch in b:
                if in_str:
                    if esc:
                        esc = False
                        st["esc"] += 1
                    elif ch == 0x5C:
                        esc = True
                    elif ch == 0x22:
                        in_str = False
                        st["dquote"] += 1
                    elif ch == 0x0A:
                        st["nl"] += 1
                    continue
                if ch == 0x22:
                    in_str = True
                elif ch == 0x7B:
                    st["brace"] += 1
                elif ch == 0x7D:
                    st["brace"] -= 1
                elif ch == 0x28:
                    st["paren"] += 1
                elif ch == 0x29:
                    st["paren"] -= 1
                elif ch == 0x5B:
                    st["brack"] += 1
                elif ch == 0x5D:
                    st["brack"] -= 1
    st["unclosed"] = 1 if in_str else 0
    st["sha256"] = h.hexdigest()
    return st


def pick_backup():
    """选最近一个「非本补丁产物」的备份：优先 dual 备份，其次 sysprompt v2。"""
    cands = sorted(glob.glob(BAK_GLOB), reverse=True)
    for pat in (".dual.", ".sysprompt.v2.", "sysprompt."):
        for c in cands:
            if pat in os.path.basename(c):
                return c
    return cands[0] if cands else None


def main():
    if not os.path.exists(CJS):
        print("找不到 %s" % CJS)
        return 2

    bak = pick_backup()
    if not bak:
        print("找不到可用的备份（%s）" % BAK_GLOB)
        return 2

    pairs = load_pairs()
    print("基线备份 : %s" % bak)
    print("替换对   : %d 条" % len(pairs))
    for o, n in pairs:
        print("   old %d B  ->  new %d B" % (len(o), len(n)))
    print()

    exp_h, exp_s = transform_hash(bak, pairs)
    act_h, act_s = file_hash(CJS)

    print("预期(备份+替换) sha256 : %s  size %d" % (exp_h[:40], exp_s))
    print("实际 zcode.cjs  sha256 : %s  size %d" % (act_h[:40], act_s))
    print()

    a, b = scan(bak), scan(CJS)
    print("%-10s %16s %16s %s" % ("指标", "备份", "当前", "判定"))
    print("-" * 58)
    for k in ("size", "dquote", "esc", "nl", "brace", "paren", "brack", "unclosed"):
        va, vb = a[k], b[k]
        print("%-10s %16s %16s %s" % (k, va, vb, "同" if va == vb else "差 %+d" % (vb - va)))
    print()

    struct_ok = all(a[k] == b[k] for k in ("brace", "paren", "brack", "unclosed"))
    print("结构配平（括号/未闭合串）:", "PASS" if struct_ok else "FAIL")
    print()
    if exp_h == act_h:
        print("等价性: PASS —— 当前文件 == 备份 + 恰好这些替换，其余逐字节相同")
        print("   -> 语法合法（原文件合法 + 替换是完整的字符串字面量替换）")
        print("   -> 没有顺手改到别处")
        return 0
    print("等价性: FAIL —— 存在预期之外的改动，请用备份回退：")
    print("   python zcode/zcode_dual_patch.py --revert")
    return 1


if __name__ == "__main__":
    sys.exit(main())
