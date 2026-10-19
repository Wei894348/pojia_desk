#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pojia_skills.py —— skills 通道投放。

同一条路由内容投两个地方：
    ① pack 内联（`{{ROUTER}}` 展开进注入正文）
    ② `<home>/skills/<id>/SKILL.md`（客户端按需加载）
② 存在的真正理由是 ① 装不下 —— 单席注入正文到 65 KB 量级必然被上下文截断，
截断后模型读到的是被砍掉中段的路由表。所以按需加载那条腿才是主力。

本模块只做 ②。① 由 pojia_routes.render_route_layer() 负责，且刻意做瘦
（20.5 KB，只内联归一表 + 路由台 + 小类索引 + 契约，叶子正文 59 KB 不内联）。

投放内容：86 份 SKILL.md = 6 个类目（父）+ 80 个小类（叶）。

安全约定：
  · 只写 `<home>/skills/<我们自己的 id>/SKILL.md` —— 目录名固定且独有，
    不会碰用户已有的 skill（如 ctf-*/mature-fiction/...）。
  · 卸载只删这个名字集合内的目录，其余原样保留。
  · home 目录不存在时跳过（不替没装的客户端造目录）。
  · 写入走调用方给的 write 函数（主脚本传事务感知的 tx_write_bytes），本模块不直接写盘。

用法（命令行单独看状态）：
    python engine/pojia_skills.py                 # 全部目标的投放状态
    python engine/pojia_skills.py list            # 列出将投放的 86 个 id
"""

from __future__ import annotations

import os
import sys

import importlib.util


def _load_routes():
    """显式从本文件所在目录加载 pojia_routes —— 被 importlib 加载时脚本目录不在 sys.path。"""
    if "pojia_routes" in sys.modules:
        return sys.modules["pojia_routes"]
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location("pojia_routes",
                                                  os.path.join(here, "pojia_routes.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pojia_routes"] = mod
    spec.loader.exec_module(mod)
    return mod


R = _load_routes()

# 各目标的 skills 通道。home = 该目标的**配置根**，根下固定一个 skills/ 子目录。
# None = 该目标没有 skills 通道（DSH 由 preset entries 驱动）。
SEATS = {
    "wb":     {"default": "~/.workbuddy", "label": "WorkBuddy", "note": "报甲数据目录下的 skills/"},
    "codex":  {"default": "~/.codex",     "label": "Codex",     "note": "CODEX_HOME/skills"},
    "zcode":  {"default": "~/.zcode",     "label": "ZCode",     "note": "ZCode 配置目录下的 skills/"},
    "claude": {"default": "~/.claude",    "label": "Claude",    "note": "CLAUDE_CONFIG_DIR/skills"},
    "grok":   {"default": "~/.grok",      "label": "Grok",      "note": "GROK_HOME/skills"},
    "gemini": {"default": "~/.gemini",    "label": "Gemini",    "note": "GEMINI_HOME/skills"},
    # Hermes Agent：唯一权威 skills 目录就是 $HERMES_HOME/skills/（默认 ~/.hermes/skills）。
    # 它按渐进式披露加载（Level 0 只把 name+description 送进系统提示词，正文按需 skill_view），
    # 且本目录下的 skill 会自动注册成 slash command —— 正好承接 86 份路由三拍。
    # 卸载只删我们自己的 id，不会碰它自带的 bundled skill（另有 .bundled_manifest 记账）。
    "hermes": {"default": "~/.hermes",    "label": "Hermes",    "note": "HERMES_HOME/skills（SOUL.md 同目录）"},
    # DSH：原先这里是 `None`，理由写的是"DSH 由 preset entries 驱动"——
    #   那只对了一半。preset 的 `skill-filesystem` 插件里 `customSkillDirs` 指向的是
    #   `@deepseek-ai/dsh-agent-preset/skills`（**包内置的 4 个**：agent-experience /
    #   cordis-composition-reference / cordis-plugin-development / editing-cordis-compositions），
    #   而路由 skill 的真正落点是插件**内置的 user skill 根**：
    #       dsh-skill-filesystem/lib/index.js → join(this.dshHome, "skills")
    #   即 `<DSH_HOME>/skills`（本机 ~/.dsh/skills）。它同时还会扫
    #   `<项目>/.dsh/skills` 与 `<项目>/.agents/skills`。
    #   -> 桌面版与网页版**共用** ~/.dsh，两边都读这个目录，所以投这里两端同时生效。
    #   为什么以前看着"有"：目录里躺着 的 86 份存量，之后脚本不再维护 →
    #   路由 skill 更新了也不会同步过来。现在补上。
    "dsh":    {"default": "~/.dsh",       "label": "DSH",       "note": "DSH_HOME/skills（插件内置 user skill 根；桌面版与网页版共用）"},
}

MARK = "<!-- pojia-route-skill -->"     # 每份 SKILL.md 末尾的归属标记，用于识别"是我们投的"


def seat_home(seat: str) -> str:
    spec = SEATS.get(seat)
    if not spec:
        return ""
    return os.path.expanduser(spec["default"])


def skill_root(seat: str, home: str = "") -> str:
    """返回该目标 skills 根目录；seat 不支持时返回 ''。home 传配置根（缺省用约定目录）。"""
    if not SEATS.get(seat):
        return ""
    base = os.path.abspath(home) if home else seat_home(seat)
    return os.path.join(base, "skills")


def our_ids() -> list:
    return [d for d, _t in R.all_skills()]


def with_mark(text: str) -> str:
    if MARK in text:
        return text
    return text.rstrip("\n") + "\n\n" + MARK + "\n"


def plan(seat: str, home: str = "") -> list:
    """返回 [(绝对路径, 文本)]；seat 无通道或配置根不存在时返回 []。

    走的叶子被指令页关掉了就不投放 —— 叶子正文只从这份 SKILL.md 到模型手里，
    「关掉」必须真的不写盘，否则开关只是个摆设。
    """
    root = skill_root(seat, home)
    if not root:
        return []
    base = os.path.abspath(home) if home else seat_home(seat)
    if not os.path.isdir(base):
        return []
    out = []
    for name, text in R.all_skills():
        if not _leaf_on(name, seat):
            continue
        out.append((os.path.join(root, name, "SKILL.md"), with_mark(text)))
    return out


def _load_doc_inject():
    """加载 doc_inject（脚本目录不一定在 sys.path，别裸 import）。

    sys.modules 里同名模块可能是半成品（加载失败留下的空壳），必须校验
    真的有 enabled() 再用 —— 否则调用方会拿到
    `module 'doc_inject' has no attribute 'enabled'`。
    """
    m = sys.modules.get("doc_inject")
    if m is not None and hasattr(m, "enabled"):
        return m
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location(
        "doc_inject", os.path.join(here, "doc_inject.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["doc_inject"] = mod
    spec.loader.exec_module(mod)
    if not hasattr(mod, "enabled"):                   # pragma: no cover
        raise RuntimeError("engine/doc_inject.py 加载异常：缺少 enabled()")
    return mod


def _leaf_on(name, seat=""):
    """叶子 / 类目的注入开关（状态/_doc_inject_<seat>.json，键 = content/routes/leaves/<id>.md）。"""
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "content", "routes", "leaves", name + ".md")
    if not os.path.isfile(p):
        return True                      # 父类目没有同名叶子文件：不受开关约束
    return _load_doc_inject().enabled(p, seat)


def install(seat: str, write_fn, home: str = "", make_dirs: bool = True,
            remove_fn=None) -> dict:
    """投放。write_fn(path, data_or_text, make_dirs=...) 由调用方给（事务感知）。

    remove_fn 给了的话，指令页关掉的叶子会顺带把已投放的那份撤掉 ——
    否则"关了还在客户端里"等于没关。
    """
    items = plan(seat, home)
    root = skill_root(seat, home)
    want = {os.path.normcase(p) for p, _t in items}
    # 只有"叶子 + 叶子被关"的组合才允许撤；父类目与未登记目录永远留着。
    off_ids = {n for n in R.leaf_ids() if not _leaf_on(n, seat)}
    written, skipped, removed = [], [], []
    for path, text in items:
        old = ""
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8", newline="") as f:
                    old = f.read()
            except OSError:
                old = ""
        if old == text:
            skipped.append(path)
            continue
        write_fn(path, text, make_dirs=make_dirs)
        written.append(path)
    if remove_fn is not None:
        for name in our_ids():
            skill = os.path.join(root, name, "SKILL.md")
            if os.path.normcase(skill) in want or not os.path.isfile(skill):
                continue
            # 只删"我们投的 + 这次被开关关掉的"；父类目与未登记目录继续留着
            if name not in off_ids:
                continue
            try:
                with open(skill, "r", encoding="utf-8", errors="replace") as f:
                    if MARK not in f.read()[-600:]:
                        continue          # 不是我们投的，不动
                remove_fn(skill)
                removed.append(name)
                if not os.listdir(os.path.dirname(skill)):
                    os.rmdir(os.path.dirname(skill))
            except OSError:
                pass
    return {"seat": seat, "written": written, "skipped": skipped,
            "removed": removed, "total": len(items)}


def status(seat: str, home: str = "") -> dict:
    """查投放状态：installed / stale / missing / foreign（用户自己的 skill，不动）。"""
    root = skill_root(seat, home)
    out = {"seat": seat, "root": root, "supported": bool(root),
           "installed": [], "stale": [], "missing": [], "foreign": []}
    if not root:
        return out
    if not os.path.isdir(root):
        out["missing"] = our_ids()
        return out
    mine = set(our_ids())
    parents = _parent_slugs()
    for name in sorted(os.listdir(root)):
        full = os.path.join(root, name)
        if not os.path.isdir(full):
            continue
        if name not in mine:
            out["foreign"].append(name)
            continue
        skill = os.path.join(full, "SKILL.md")
        if not os.path.isfile(skill):
            out["missing"].append(name)
            continue
        try:
            with open(skill, "r", encoding="utf-8", newline="") as f:
                have = f.read()
        except OSError:
            have = ""
        want = with_mark(R.render_parent_skill(_parent_key(name)) if name in parents
                         else R.render_leaf(name))
        (out["installed"] if have == want else out["stale"]).append(name)
    for name in sorted(mine):
        if name not in out["installed"] and name not in out["stale"] and name not in out["missing"]:
            out["missing"].append(name)
    return out


def _parent_slugs() -> dict:
    """目录名（slug）→ CATEGORIES 的键，例：`sw-analyze` → `sw`。

    注意别把它当成"键的集合"用 —— `render_parent_skill()` 收的是**键**（sw/net/…），
    而这里给的是**目录名**（slug）。曾经直接 `if name in _parent_slugs()` 就往下传，
    于是只要目录里真出现过父类目，status() 就会
    `KeyError: 'auto-pipe'` 崩掉。
    """
    return {R.CATEGORIES[c][3]: c for c in R.CAT_ORDER}


def _parent_key(name: str):
    return _parent_slugs().get(name)


def uninstall(seat: str, remove_fn, home: str = "") -> dict:
    """移除我们投放的 86 个目录。用户自己的 skill 一律不碰。

    目录里只有 SKILL.md（我们的）→ 删文件后顺手 rmdir，不留 86 个空壳；
    目录里还有别的东西 → 只删 SKILL.md，目录留下。"""
    root = skill_root(seat, home)
    removed, kept = [], []
    if not root or not os.path.isdir(root):
        return {"seat": seat, "removed": [], "kept": [], "root": root}
    mine = set(our_ids())
    for name in sorted(os.listdir(root)):
        full = os.path.join(root, name)
        if not os.path.isdir(full) or name not in mine:
            if os.path.isdir(full):
                kept.append(name)
            continue
        skill = os.path.join(full, "SKILL.md")
        if os.path.isfile(skill):
            remove_fn(skill)
            removed.append(name)
            try:
                if not os.listdir(full):
                    os.rmdir(full)
            except OSError:
                pass
    return {"seat": seat, "removed": removed, "kept": kept, "root": root}


def summarize(seat: str, home: str = "") -> str:
    st = status(seat, home)
    if not st["supported"]:
        return "  [%s] 该目标没有 skills 通道" % seat
    if not os.path.isdir(st["root"]):
        return "  [%s] 目录不存在，跳过：%s" % (seat, st["root"])
    return ("  [%s] %s —— 最新 %d / 待更新 %d / 缺失 %d / 用户自有 %d"
            % (seat, st["root"], len(st["installed"]), len(st["stale"]),
               len(st["missing"]), len(st["foreign"])))


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] in ("list", "-l"):
        for name, text in R.all_skills():
            print("%-16s %5d B" % (name, len(text.encode("utf-8"))))
        print("合计 %d 份" % len(our_ids()))
        return 0

    a = R.audit()
    if not a["ok"]:
        for p in a["problems"]:
            print("[!] %s" % p)
        return 1
    print("路由数据自检 OK：叶子 %d / SKILL.md %d" % (a["leaves"], a["skills"]))
    for seat in SEATS:
        print(summarize(seat))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
