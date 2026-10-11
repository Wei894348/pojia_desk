# -*- coding: utf-8 -*-
"""把当前共享管线生成的各席位正文落成独立源文件 content/seats/<seat>.md。

只生成**不存在**的席位文件（不覆盖已有手写内容）；
`--force` 才重新生成全部。

用法：
    python tools/seed_seat_files.py            # 缺哪个补哪个
    python tools/seed_seat_files.py --force    # 全部按当前共享管线重生成
"""
import importlib.util
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ.setdefault("POJIA_NO_SEAT_OVERRIDE", "1")   # seed 本身要吃共享管线

spec = importlib.util.spec_from_file_location(
    "pojia_seed", os.path.join(HERE, "engine", "破甲一键通.py"))
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

SEATS = ("claude", "codex", "dsh", "workbuddy", "zcode")


def _strip_hash_head(text):
    """去掉正文里的哈希标记行（席位文件的哈希由引擎落盘时重打）。"""
    text = re.sub(r"^<!--\s*p[^>]*?h=[0-9a-f]{12}\s*-->\s*", "", text.lstrip(), count=1)
    # codex 等生成器会把哈希行放在标题之后 —— 文件内任何位置的旧标记行都清掉
    text = re.sub(r"<!--\s*cfg-v7:h=[0-9a-f]{12}\s*-->\s*\n?", "", text)
    return text


def gen(seat):
    user, _src = P.load_user_persona("")
    if seat == "claude":
        style = P.build_claude_style(user, P.build_policy(user)[1])
        # 去掉 frontmatter + 标记，只留正文（引擎落盘时会重新加 front + 标记）
        i = style.index("-->", style.index(CLAUDE_MARK := P.CLAUDE_STYLE_MARK)) + 3
        return _strip_hash_head(style[i:].lstrip("\n"))
    if seat == "codex":
        t = P.CodexTarget().build_instructions(user, P.build_policy(user)[1])
        return _strip_hash_head(t)
    if seat == "zcode":
        t = P.ZcodeTarget().build_agents_md(user, P.build_policy(user)[1])
        return _strip_hash_head(t)
    # dsh / workbuddy：整段 persona（哈希头由引擎重打）
    return _strip_hash_head(P.build_persona_text(user))


def main():
    force = "--force" in sys.argv
    os.makedirs(P.SEAT_DIR, exist_ok=True)
    for seat in SEATS:
        p = P.seat_override_path(seat)
        if os.path.exists(p) and not force:
            print("保留已有：%s" % p)
            continue
        body = gen(seat).strip("\r\n") + "\n"
        io.open(p, "w", encoding="utf-8", newline="\n").write(
            "<!-- 席位独立提示词源：改这份只影响 %s。删除即回退共享管线。 -->\n\n" % seat
            + body)
        print("写出：%s（%d 字符）" % (p, len(body)))


if __name__ == "__main__":
    main()
