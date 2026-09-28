# -*- coding: utf-8 -*-
"""把学姐人格写进新版 DSH 的 shipped 预设（dsh-persona 行）。

背景：DSH 0.1.7 起，每个 shipped 预设都挂 `@deepseek-ai/dsh-persona`，
它会 shadow 掉 deployment 层的 personaPrefix（`dsh-base` -> `dsh-system-prompt`）。
所以人格必须落在 preset 层才看得见。

用法：
  python tools/dsh_presets_persona.py            # 预览（不改）
  python tools/dsh_presets_persona.py --apply    # 写入（先备份到 状态/）
  python tools/dsh_presets_persona.py --revert   # 从最近备份还原
"""
import datetime
import pathlib
import re
import shutil
import sys

BASE = pathlib.Path(
    r"C:\Users\86157\AppData\Local\npm-cache\_npx\1e7f6d9597241db0\node_modules\@deepseek-ai"
)
PRESETS = BASE / "dsh-web-app" / "presets"
PERSONA_SRC = BASE / "dsh-base" / "cordis.patch.yml"
STATE = pathlib.Path(r"F:\project\pojia_desk\状态")

# minimal 的 prefix 带 complete: true（抑制全部其他 section），
# 塞长人格进去会让该模式彻底不可用 —— 不动它。
SKIP = {"minimal"}


def extract_persona():
    """从 dsh-base 的补丁里取出已经写好的 personaPrefix 块文本。"""
    text = PERSONA_SRC.read_text(encoding="utf-8")
    lines = text.split("\n")
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*)personaPrefix:\s*\|-\s*$", line)
        if not m:
            continue
        indent = len(m.group(1))
        body = []
        for nxt in lines[i + 1:]:
            if nxt.strip() == "":
                body.append("")
                continue
            cur = len(nxt) - len(nxt.lstrip())
            if cur <= indent:
                break
            body.append(nxt[indent + 2:] if len(nxt) >= indent + 2 else nxt.strip())
        while body and body[-1].strip() == "":
            body.pop()
        return "\n".join(body)
    return None


MARK = "身份（沉浸优先）"


def rewrite(path, persona, apply=False):
    """把 dsh-persona 行的 prefix 换成 persona 块。返回 (changed, note)。"""
    raw = path.read_text(encoding="utf-8")
    lines = raw.split("\n")

    # 定位 persona 行，再找它 config 里的 prefix:
    pidx = next((i for i, l in enumerate(lines) if "dsh-persona" in l), None)
    if pidx is None:
        return False, "没有 dsh-persona 行"
    j = None
    for k in range(pidx, min(pidx + 12, len(lines))):
        m = re.match(r"^(\s*)prefix:\s*(.*)$", lines[k])
        if m:
            j, indent, tail = k, len(m.group(1)), m.group(2).strip()
            break
    if j is None:
        return False, "persona 行里没找到 prefix:"

    # 块标量（| / > 系列）要连同续行一起吃掉，否则残行会破坏 YAML
    end = j + 1
    if tail[:1] in ("|", ">"):
        while end < len(lines):
            cur = lines[end]
            if cur.strip() == "":
                end += 1
                continue
            if len(cur) - len(cur.lstrip()) <= indent:
                break
            end += 1
        block = "\n".join(lines[j:end])
        if MARK in block:
            return False, "已是学姐人格（块标量）"

    # 键与 suffix 同级（indent），块内容比键深 2 格
    body = [" " * (indent + 2) + ln if ln else "" for ln in persona.split("\n")]
    new = [" " * indent + "prefix: |-"] + body

    out = lines[:j] + new + lines[end:]
    new_text = "\n".join(out)
    if new_text == raw:
        return False, "内容一致，无需改动"
    if apply:
        path.write_text(new_text, encoding="utf-8")
    return True, "prefix 已替换为学姐人格（%d 行）" % len(body)


def main():
    apply = "--apply" in sys.argv
    revert = "--revert" in sys.argv

    backs = sorted(STATE.glob("dsh-presets-before-*"), reverse=True)
    if revert:
        if not backs:
            print("没有可还原的备份")
            return 1
        src = backs[0]
        n = 0
        for f in src.glob("*.patch.yml"):
            shutil.copy2(f, PRESETS / f.name)
            n += 1
        print("已从 %s 还原 %d 个文件" % (src.name, n))
        return 0

    persona = extract_persona()
    if not persona:
        print("无法从 dsh-base 补丁里提取 personaPrefix")
        return 2
    print("人格文本：%d 字符 / %d 行" % (len(persona), persona.count("\n") + 1))
    if "{{" in persona or "}}" in persona:
        print("!! 警告：文本含 {{ }}，会被 dsh-persona 当模板插值，需转义")
        return 2

    files = sorted(
        p for p in PRESETS.glob("*.patch.yml") if p.name.split(".")[0] not in SKIP
    )
    print("目标：%s" % ", ".join(p.name for p in files))
    print("跳过：%s（complete: true，改了会让该模式不可用）" % ", ".join(sorted(SKIP)))
    print("-" * 50)

    todo = []
    for p in files:
        changed, note = rewrite(p, persona, apply=False)
        print("%-22s %s" % (p.name, note))
        if changed:
            todo.append(p)

    if not apply:
        print("-" * 50)
        print("预览完毕（%d 个待改）。加 --apply 写入。" % len(todo))
        return 0

    if not todo:
        print("无需写入")
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = STATE / ("dsh-presets-before-" + stamp)
    bak.mkdir(parents=True, exist_ok=True)
    for p in todo:
        shutil.copy2(p, bak / p.name)

    for p in todo:
        changed, note = rewrite(p, persona, apply=True)
        print("%-22s 已写入 | %s" % (p.name, note))
    print("-" * 50)
    print("备份：%s" % bak)
    print("改的是磁盘文件，需完全退出 DSH 再重开、并新建会话才生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
