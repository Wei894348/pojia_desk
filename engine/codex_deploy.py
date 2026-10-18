"""codex_deploy.py —— ChatGPT（Codex）席位的部署逻辑：四个落点同写、可逐路开关、可整体还原。

只做四件事，写四个落点：
  1. `~/.codex/prompts/<选中的文件名>.md`  —— 选中的指令源整份写入（带 BEGIN/END 标记）
  2. `~/.codex/AGENTS.md`             —— 同一份正文，插进标记块（保留用户原有内容）
  3. `~/.codex/config.toml`           —— `model_instructions_file` 指向上面的文件
  4. `~/.codex/skills/<id>/SKILL.md`  —— 6 个父类目 + 80 片叶子

备份：写入前快照到 `<home>/pojia-backups/<三级路径>.bak`，只存一份（已存在不覆盖），
每份只存一次，不覆盖。状态表：`状态/_codex_deploy.json`。
"""

import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
CONTENT = os.path.join(BASE, "content")
TARGETS = os.path.join(BASE, "targets")
STATE_DIR = os.path.join(BASE, "状态")

BEGIN = "<!-- POJIA-CODEX:BEGIN -->"
END = "<!-- POJIA-CODEX:END -->"

INSTR_LIB = os.path.join(TARGETS, "codex", "instructions")
PROMPTS_DIR = "prompts"              # 部署位：~/.codex/prompts/<选中的文件名>
STATE_FILE = os.path.join(STATE_DIR, "_codex_deploy.json")
# 官方原稿库：首次部署前把 AGENTS.md / config.toml 原样存一份，只读留存，
# 供指令页展示「官方原始」并在还原时作对照（还原只动指针行，不回拷整份）。
ORIGIN_LIB = os.path.join(TARGETS, "codex", "originals")
ORIGIN_FILES = ("AGENTS.md", "config.toml")


def home_of(seat="codex"):
    """席位配置根：CODEX_HOME > ~/.codex。"""
    env = os.environ.get("CODEX_HOME") or os.environ.get("CODEX_DIR")
    if env and env.strip():
        return os.path.abspath(os.path.expanduser(env.strip()))
    return os.path.join(os.path.expanduser("~"), ".codex")


def _read(p):
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            return f.read()
    except OSError:
        return ""


def _write(p, text):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if not text.endswith("\n"):
        text += "\n"
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _write_bytes(p, src):
    """按备份原样回拷（逐字节，不做换行归一）。

    目标路径所在的中间目录可能已被还原时删掉（skills/<id>/），先补建。
    """
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    shutil.copyfile(src, p)


def origin_path(name):
    """官方原稿的留存位置（只读，不参与部署）。"""
    return os.path.join(ORIGIN_LIB, name)


def save_originals(home=None, seed=True):
    """把接管前的 AGENTS.md / config.toml 原样存进 targets/codex/originals/。

    每份只存一次（`*_once` 语义）：已经存过就不覆盖，这样多次 apply 不会把
    后来被改过的现场当成"官方原稿"。返回本次新存的文件名列表。

    seed=True：原稿目录为空时，先用同目录的 `*.seed` 打底（出厂参考稿，
    在项目里入库），首次部署后再被实盘内容覆盖 —— 用户从没部署过也能在
    指令页看到「官方原版」这两个文件。
    """
    home = home or home_of("codex")
    saved = []
    os.makedirs(ORIGIN_LIB, exist_ok=True)
    for name in ORIGIN_FILES:
        src = os.path.join(home, name)
        dst = origin_path(name)
        if os.path.isfile(dst):
            continue
        text = _read(src) if os.path.isfile(src) else ""
        live_ok = bool(text) and not (
            (name == "AGENTS.md" and BEGIN in text)
            or (name == "config.toml" and "model_instructions_file" in text))
        if live_ok:
            _write_bytes(dst, src)
            saved.append(name)
        elif seed:
            # 实盘不可用（不存在 / 已被我们改过）：用 seed 打底，但只写进
            # 本次调用的「本次视图」，不落成 originals 里的正式原稿 ——
            # 否则将来真正的原稿永远存不进来。
            seed_p = dst + ".seed"
            if os.path.isfile(seed_p):
                # 返回值只用于日志，界面读的是正式原稿；正式原稿仍不存在
                saved.append(name + ".seed")
    return saved


def origin_view(name):
    """指令页要展示的「官方原版」：正式原稿优先，没有就用 seed 兜底。"""
    for p in (origin_path(name), origin_path(name) + ".seed"):
        if os.path.isfile(p):
            return p
    return ""


def state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(d):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, STATE_FILE)


def pack_name():
    """当前选中的指令源文件名（Argon.md / unlock.md …）。默认 Argon.md。

    用过就记住：state 里可能只剩 "pack": ""（还原把指令源也标成关了），
    但用户点部署就是要部署 —— 这种「空字符串」按没记录处理，回到上次用过的那份。
    """
    st = state()
    name = st.get("pack") or st.get("last_pack") or ""
    if name and os.path.isfile(os.path.join(INSTR_LIB, name)):
        return name
    cands = sorted(n for n in os.listdir(INSTR_LIB) if n.lower().endswith(".md")) \
        if os.path.isdir(INSTR_LIB) else []
    return cands[0] if cands else ""


def pack_text(name=""):
    p = os.path.join(INSTR_LIB, name or pack_name())
    t = _read(p)
    if not t.strip():
        return ""
    return t.strip("\r\n").replace("\r\n", "\n") + "\n"


def opts():
    """四个落点各自是否启用。默认全开；pack 关掉时不再挑任何指令源。"""
    st = state()
    on = st.get("pack_on") is not False
    return {
        "pack": pack_name() if on else "",
        "agents": st.get("agents") is not False,
        "config": st.get("config") is not False,
        "skills": st.get("skills") is not False,
    }


def _remember_pointer(home):
    """部署前把 config.toml 里指针行的原值/位置记进状态表。

    只记这一行，不存整份 config.toml —— 还原时把它换回去就够了。
    没有指针行时记 `{}`（表示"原本就没有"），避免还原时误造一行。

    只在"从未记录过"时写：换源 / 关闭指令这些后续部署会被记成新值，
    那样还原就会把上一次部署的指针当成用户原稿。
    """
    st = state()
    if st.get("toml_pointer") is not None:
        return
    cp = os.path.join(home, "config.toml")
    if not os.path.isfile(cp):
        return
    lines = _read(cp).splitlines()
    for i, ln in enumerate(lines):
        if _ptr_line(ln):
            st["toml_pointer"] = {"line": ln, "at": i}
            save_state(st)
            return
    st["toml_pointer"] = {}
    save_state(st)


# ── 标记块（插入 / 剥离标记块）────────────────

def insert_marked(previous, body):
    body = body if body.endswith("\n") else body + "\n"
    block = BEGIN + "\n" + body + END + "\n"
    if BEGIN in previous and END in previous:
        head = previous[:previous.index(BEGIN)]
        tail = previous[previous.index(END) + len(END):]
        return (head + block + tail).rstrip() + "\n"
    prev = previous.replace("\r\n", "\n").strip()
    return (prev + "\n\n" if prev else "") + block


def wipe_marked(text):
    if BEGIN not in text or END not in text:
        return text, False
    head = text[:text.index(BEGIN)]
    tail = text[text.index(END) + len(END):]
    return (head + tail).strip(), True


def _bak_path(home, path):
    """<home>/pojia-backups/<后三级路径>.bak 。"""
    parts = path.replace("\\", "/").split("/")[-3:]
    return os.path.join(home, "pojia-backups", "__".join(parts) + ".bak")


def _snapshot(home, path):
    """写入前留一份原样（每份只存一次，已存在不覆盖）。

    幂等：同一路径反复部署只留最早那份，不会越积越多。
    """
    bak = _bak_path(home, path)
    if os.path.isfile(bak) or not os.path.isfile(path):
        return ""
    os.makedirs(os.path.dirname(bak), exist_ok=True)
    shutil.copyfile(path, bak)
    return bak


def _ours(path):
    """这份文件是不是我们自己写进去的。

    · 指令文件 / AGENTS 标记块 —— 带 POJIA-CODEX 标记；
    · SKILL.md —— 末尾带 pojia-route-skill 归属标记；
    · 已投放的 SKILL.md 若没有归属标记，按"路径在 skills/<已知 id>/"判定。
    我们自己写的文件绝不能进 pojia-backups —— 一旦进了，restore 会把它当
    「用户原稿」回拷，结果越还原越带注入。
    """
    text = _read(path)
    if BEGIN in text or "<!-- pojia-route-skill -->" in text:
        return True
    parts = path.replace("\\", "/").split("/")
    ids = set(_routes().skill_dirs())
    if len(parts) >= 2 and parts[-2] in ids:
        return True
    # 备份文件名为 <home>__skills__<id>__SKILL.md.bak —— 同样是我们自己写的
    name = parts[-1]
    if name.endswith(".bak") and any(("__%s__" % sid) in name for sid in ids):
        return True
    return False


def _drop_stale_bak(home, path):
    """早先误把自己写的文件存成 .bak 的，清掉（内容带标记 = 不是用户原稿）。"""
    bak = _bak_path(home, path)
    if os.path.isfile(bak) and _ours(bak):
        try:
            os.remove(bak)
        except OSError:
            pass


def _backup_user(home, path):
    """写入前留底：只备份「用户自己的文件」，每份只存一次。

    用户自己的文件 = AGENTS.md / config.toml 这类客户端原有文件。万一部署
    逻辑出错把它们写坏，可以直接从 .bak 恢复。
    我们自己写进去的文件（prompts/<源>.md、skills/<id>/SKILL.md）不备份：
    它们本来就来自项目源件，丢了重新部署即可；存成 .bak 反而会让还原时
    把注入内容当成"用户原稿"回拷回去。
    """
    if _ours(path):
        _drop_stale_bak(home, path)
        return ""
    return _snapshot(home, path)


def _usable_bak(home, path):
    """可用来回拷的备份：存在、且不是我们自己写的（防止把注入内容还原回去）。"""
    bak = _bak_path(home, path)
    if not os.path.isfile(bak):
        return ""
    if _ours(bak):
        try:
            os.remove(bak)
        except OSError:
            pass
        return ""
    return bak


# ── plan / deploy / verify / restore ──────────────────────────────────

def _routes():
    import importlib.util
    p = os.path.join(HERE, "pojia_routes.py")
    if "pojia_routes" in sys.modules:
        return sys.modules["pojia_routes"]
    spec = importlib.util.spec_from_file_location("pojia_routes", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pojia_routes"] = mod
    spec.loader.exec_module(mod)
    return mod


def _enabled_fn():
    try:
        import doc_inject
        return doc_inject.enabled
    except ImportError:
        import importlib.util
        spec = importlib.util.spec_from_file_location("doc_inject", os.path.join(HERE, "doc_inject.py"))
        di = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(di)
        return di.enabled


def skill_on(sid):
    """某份 SKILL 当前是否应投放。

    · 叶子：看注入表（content/routes/leaves/<id>.md）；
    · 父类目：默认投放，只有状态表里显式记了 codex:skill/<id> = false 才撤。
    父类目没有同名叶子文件，不能拿"注入表缺键"当开关。
    """
    mod = _routes()
    leaf_p = os.path.join(mod.LEAVES_DIR, sid + ".md")
    if os.path.isfile(leaf_p):
        return _enabled_fn()(leaf_p)
    return state().get("codex:skill/%s" % sid) is not False


def _skill_items(all_items=False):
    """[(id, 文本)]：6 个父类目 + 80 片叶子。默认只回启用的那些。"""
    mod = _routes()
    out = []
    # 父类目：6 份。它们没有同名叶子，不受注入表管；开关记在状态表
    # （codex:skill/<id>），默认投放。
    for cat in mod.CAT_ORDER:
        out.append((mod.CATEGORIES[cat][3], mod.render_parent_skill(cat)))
    for leaf in mod.leaf_ids():
        p = os.path.join(mod.LEAVES_DIR, leaf + ".md")
        if not all_items and os.path.isfile(p) and not _enabled_fn()(p):
            continue
        out.append((leaf, mod.render_leaf(leaf)))
    return out


def plan(seat="codex", opts_override=None, full=False):
    """返回要写的四个落点。文件一个字节都不写。

    full=True 时忽略开关：四路都算、叶子全收 —— restore() 靠它才能把
    "已经关掉的那一路"也清干净（否则关掉后连删都找不到落点）。
    """
    home = home_of(seat)
    o = dict(opts())
    if opts_override:
        o.update({k: v for k, v in opts_override.items() if v is not None})
    if full:
        # 还原要清到"关掉的这一路"：即便 pack 已关，也要拿最后一次用过的源，
        # 这样 config.toml 的指针同样有落点可回拷（否则关掉后指针会留盘）。
        st0 = state()
        o.update({"pack": st0.get("pack") or pack_name(),
                  "agents": True, "config": True, "skills": True})
    name = o.get("pack") or ""
    text = pack_text(name) if name else ""
    instr_path = os.path.join(home, PROMPTS_DIR, name) if name else ""
    writes = []
    if text and instr_path:
        writes.append({"kind": "file", "path": instr_path,
                       "role": "指令文件", "text": BEGIN + "\n" + text + END + "\n"})
    if o.get("agents") and text:
        writes.append({"kind": "marked", "path": os.path.join(home, "AGENTS.md"),
                       "role": "AGENTS.md 标记块", "text": text})
    if o.get("config") and instr_path and text:
        writes.append({"kind": "toml", "path": os.path.join(home, "config.toml"),
                       "role": "config.toml 指针",
                       "value": instr_path.replace("\\", "/")})
    if o.get("skills"):
        for sid, body in _skill_items(all_items=full):
            writes.append({"kind": "skill", "path": os.path.join(home, "skills", sid, "SKILL.md"),
                           "role": "SKILL.md", "text": body.rstrip("\n") + "\n"})
    return {"home": home, "writes": writes, "opts": o, "pack": o.get("pack") or ""}


SNAPSHOT = os.path.join(TARGETS, "codex", "codex-unlock-instructions.md")


def sync_snapshot(live_path):
    """把本机指令文件镜像到 targets/codex/codex-unlock-instructions.md。

    镜像里去掉 BEGIN/END 标记行 —— 快照记录的是注入正文，不是包装。
    没有实盘（关掉注入）时保留上一次的镜像，不写空文件。
    """
    if not live_path or not os.path.isfile(live_path):
        return ""
    text = _read(live_path).replace(chr(13) + chr(10), chr(10))
    # 部署位文件是 BEGIN/END 包着正文；旧版还会在正文末尾多一个换行。
    # 按行剥：首行 BEGIN、末行 END 各去掉一次，不碰正文自身的内容。
    lines = text.split(chr(10))
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and lines[0].strip() == BEGIN:
        lines.pop(0)
    if lines and lines[-1].strip() == END:
        lines.pop()
    body = chr(10).join(lines).strip(chr(10))
    if not body:
        return ""
    new = body + chr(10)
    if _read(SNAPSHOT) == new:
        return SNAPSHOT
    _write(SNAPSHOT, new)
    return SNAPSHOT

def _upsert_toml(src, key, value):
    """只替换 `key = ...` 那一行；没有就追加。

    匹配用精确键名：整行去掉空白后以 `key` 开头、后面紧跟 `=` —— 之前用
    startswith(key) 会把 `model_instructions_file_extra` 之类的键误替换。

    幂等优先：指针行已存在且值正确、也没有重复行时，**原样返回**，
    不重写文件。客户端（或别的工具）改写 config.toml 时常常顺手删注释、
    调整空行，我们没必要每次部署都去"纠正"一遍 —— 多写一次就多一次
    和运行中的 Codex 抢文件的机会。
    """
    line = '%s = "%s"' % (key, value)
    hits = [ln for ln in src.splitlines()
            if ln.strip().startswith(key) and ln.strip()[len(key):].lstrip().startswith("=")]
    if len(hits) == 1 and hits[0].strip() == line:
        return src

    out, hit = [], False
    for ln in src.splitlines():
        s = ln.strip()
        if s.startswith(key) and s[len(key):].lstrip().startswith("="):
            if not hit:
                out.append(line)
                hit = True
            continue                     # 重复的旧键行直接丢掉
        if s == "# POJIA managed":
            continue                     # 旧注释行不再堆积
        out.append(ln)
    if not hit:
        if out and out[-1].strip():
            out.append("")
        out.append("# POJIA managed")
        out.append(line)
    return "\n".join(out).rstrip() + "\n"


def set_pack(name=None, on=True):
    """记录当前指令源（name=None 表示关掉注入，不挑任何源）。"""
    st = state()
    if on and name:
        st["pack"] = name
        st["last_pack"] = name          # 还原后部署还要用得回来
        st["pack_on"] = True
    else:
        st["pack_on"] = False
    save_state(st)


def _doc_inject():
    """加载 doc_inject（engine/ 不一定在 sys.path 里，别用裸 import）。"""
    if "doc_inject" in sys.modules:
        return sys.modules["doc_inject"]
    import importlib.util
    p = os.path.join(HERE, "doc_inject.py")
    spec = importlib.util.spec_from_file_location("doc_inject", p)
    m = importlib.util.module_from_spec(spec)
    sys.modules["doc_inject"] = m
    spec.loader.exec_module(m)
    return m


def enable_all():
    """把四路开关和指令页文档开关一起拨回「注入」—— 还原之后重新部署用。"""
    st = state()
    st.update({"pack": st.get("pack") or pack_name(), "pack_on": True,
               "agents": True, "config": True, "skills": True})
    for k in list(st):
        if k.startswith("codex:skill/") and st[k] is False:
            st[k] = True
    save_state(st)
    try:
        di = _doc_inject()
        table = di._table()
        for k in list(table):
            if k.startswith("targets/codex/instructions/") or k.startswith("content/routes/leaves/"):
                table.pop(k, None)          # 缺键 = 注入
        di.set_table(table)
    except Exception:
        pass


def deploy(seat="codex", opts_override=None, dry=False):
    spec = plan(seat, opts_override)
    home = spec["home"]
    if not spec["writes"]:
        # 四路全关 = 回官方原版（和指令页「全部关掉」的语义一致）
        if dry:
            return {"ok": True, "action": "deploy", "home": home, "pack": "",
                    "written": [], "backups": [], "total": 0, "dry": True,
                    "note": "四路全关：正式部署会走还原"}
        r = restore(seat)
        r["note"] = "四路全关 —— 已按备份还原成官方原版"
        return r
    backups, written, skipped = [], [], []
    if not dry:
        # 首次部署前留一份官方原稿（只存一次，供指令页展示与还原对照）
        saved = save_originals(home)
    # 记住部署前 config.toml 里指针那一行的原值：还原时只需把这一行换回去，
    # 其它配置（model / provider / MCP）一直留在原文件里，不存整份备份。
    if not dry:
        _remember_pointer(home)
    for w in spec["writes"]:
        p = w["path"]
        if w["kind"] == "toml":
            src = _read(p)
            new = _upsert_toml(src, "model_instructions_file", w["value"])
            if dry:
                skipped.append(p)
                continue
            if new != src:
                b = _backup_user(home, p)
                if b:
                    backups.append(b)
                _write(p, new)
                written.append(p)
            else:
                skipped.append(p)
            continue
        if w["kind"] == "marked":
            old = _read(p)
            new = insert_marked(old, w["text"])
        else:
            new = w["text"]
        if dry:
            skipped.append(p)
            continue
        if _read(p) == new:
            skipped.append(p)
            continue
        b = _backup_user(home, p)
        if b:
            backups.append(b)
        _write(p, new)
        written.append(p)
    if not dry:
        # 换源 / 关叶片后可能留下不该在的东西：按 verify 的结论补一遍清理
        _prune(home, spec["pack"], spec["opts"])
    if not dry:
        try:
            live = os.path.join(home, PROMPTS_DIR, spec["pack"]) if spec["pack"] else ""
            if live and os.path.isfile(live):
                # 快照记"部署位正文"，和 test_codex_snapshot 的比对口径一致
                _write(SNAPSHOT, _read(live))
        except Exception:
            pass
        st = state()
        o = spec["opts"]
        was_off = st.get("pack_on") is False and st.get("agents") is False \
            and st.get("config") is False and st.get("skills") is False
        if o["pack"]:
            # 记住这次用的来源：还原会把 pack 清空，下次部署要能选回同一份
            st["last_pack"] = o["pack"]
        st.update({"pack": o["pack"], "pack_on": bool(o["pack"]),
                   "agents": o["agents"],
                   "config": o["config"], "skills": o["skills"],
                   "deployedAt": time.strftime("%Y-%m-%d %H:%M:%S")})
        save_state(st)
        # 从「还原态」重新部署：把还原时打成 false 的文档开关一并拨回，
        # 否则指令页显示「未启用」而盘上已经写进去了。
        if was_off:
            enable_all()
    return {"ok": True, "action": "deploy", "home": home, "pack": spec["pack"],
            "written": written, "backups": backups, "total": len(spec["writes"]),
            "dry": dry, "originals": ([] if dry else saved)}


def _prune(home, pack, o=None):
    """部署后清理：prompts/ 只留当前这份；关掉的落点从盘上撤下。

    o = plan 里的四路开关。关掉 AGENTS.md / config.toml / skills 时，
    之前部署留下的标记块 / 指针 / SKILL.md 必须在这里补撤回 —— 不撤的话
    「指令页关了但客户端里还生效」。
    另外 pack 为空 = 没有选中任何指令源：标记块和指针同样要撤（它们的内容
    都来自那份指令源），文档里说「当前文件不部署」就是这个语义。
    """
    o = o or opts()
    no_pack = not pack
    pdir = os.path.join(home, PROMPTS_DIR)
    try:
        for fn in os.listdir(pdir):
            if fn.lower().endswith(".md") and fn != pack:
                op = os.path.join(pdir, fn)
                # 部署位文件是我们自己写的，直接删；不备份自己写的东西
                os.remove(op)
    except OSError:
        pass
    # AGENTS.md / config.toml 这一路被关掉（或没有选中的指令源）：撤回标记块与指针
    if not o.get("agents") or no_pack:
        ap = os.path.join(home, "AGENTS.md")
        if os.path.isfile(ap):
            cleaned, had = wipe_marked(_read(ap))
            if had:
                if cleaned.strip():
                    _write(ap, cleaned + "\n")
                else:
                    try:
                        os.remove(ap)
                    except OSError:
                        pass
    if not o.get("config") or no_pack:
        cp = os.path.join(home, "config.toml")
        if os.path.isfile(cp):
            src = _read(cp)
            if "model_instructions_file" in src:
                left = "\n".join(ln for ln in src.splitlines()
                                 if not ln.strip().startswith("model_instructions_file"))
                _write(cp, left.rstrip("\n") + "\n")
    try:
        from doc_inject import enabled as _en
    except ImportError:
        import importlib.util
        spec = importlib.util.spec_from_file_location("doc_inject", os.path.join(HERE, "doc_inject.py"))
        di = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(di)
        _en = di.enabled
    root = os.path.join(home, "skills")
    mod = _routes()
    # skills 整路关掉：我们投放过的目录全撤（用户自建的不在 skill_dirs 里）
    if not o.get("skills"):
        for sid in mod.skill_dirs():
            sp = os.path.join(root, sid, "SKILL.md")
            if os.path.isfile(sp):
                try:
                    os.remove(sp)
                    if not os.listdir(os.path.dirname(sp)):
                        os.rmdir(os.path.dirname(sp))
                except OSError:
                    pass
        return
    # 单项关掉的 SKILL：只有叶子受注入表管。父类目（6 份）永远投放，
    # 它们是「类目入口」，关掉叶子就够细了。
    for sid in mod.leaf_ids():
        if skill_on(sid):
            continue
        sp = os.path.join(root, sid, "SKILL.md")
        if os.path.isfile(sp):
            try:
                os.remove(sp)
                if not os.listdir(os.path.dirname(sp)):
                    os.rmdir(os.path.dirname(sp))
            except OSError:
                pass


def verify(seat="codex"):
    """按当前开关核对四路：该在的在、该关的确实不在。"""
    spec = plan(seat, full=True)
    o = dict(opts())
    want_pack = o.get("pack") or ""
    dropped_pack = state().get("pack") or pack_name()
    checks = []
    for w in spec["writes"]:
        p = w["path"]
        exists = os.path.isfile(p)
        text = _read(p) if exists else ""
        if w["kind"] == "toml":
            ok = exists and ("model_instructions_file" in text and w["value"] in text)
        elif w["kind"] == "skill":
            sid = os.path.basename(os.path.dirname(p))
            if not skill_on(sid):
                ok = not exists          # 开关关掉的 SKILL：不该还在
            else:
                ok = exists and sid in text
        elif w["kind"] == "file":
            ok = exists and (BEGIN in text and END in text)
        else:
            ok = exists and (BEGIN in text and END in text)
        # 开关关着的路径：不要求存在；存在了说明没清理干净
        if w["role"] == "AGENTS.md 标记块" and (not o.get("agents") or not want_pack):
            ok = not (BEGIN in text and END in text)
        if w["kind"] == "file" and not want_pack:
            # 关掉后这份不该还在
            ok = not exists

        if w["kind"] == "toml" and (not o.get("config") or not want_pack):
            ok = True
        if w["kind"] == "skill" and not o.get("skills"):
            ok = not exists
        checks.append({"path": p, "role": w["role"], "exists": exists, "ok": ok,
                       "bytes": len(text.encode("utf-8")) if exists else 0})
    # 换源 / 关掉之后，prompts/ 里不该留其它候选
    pdir = os.path.join(spec["home"], PROMPTS_DIR)
    stale = []
    if os.path.isdir(pdir):
        for fn in os.listdir(pdir):
            if not fn.lower().endswith(".md"):
                continue
            if fn != want_pack:
                stale.append(os.path.join(pdir, fn))
    for p in stale:
        checks.append({"path": p, "role": "残留指令文件", "exists": True, "ok": False, "bytes": 0})
    return {"ok": all(c["ok"] for c in checks), "checks": checks, "home": spec["home"],
            "pack": spec["pack"], "total": len(checks),
            "done": sum(1 for c in checks if c["ok"])}


def _ptr_line(ln, key="model_instructions_file"):
    """这一行是不是 `key = ...`（允许前后空白）。"""
    s = ln.strip()
    return s.startswith(key) and s[len(key):].lstrip().startswith("=")


def _restore_toml_pointer(home, path, key="model_instructions_file"):
    """只还原指针那一行，不回拷整份 config.toml。

    部署前那一行的原值记在状态表 `toml_pointer` 里；有就换回去，没有就摘掉。
    部署后客户端自己改的其它配置（model / provider / MCP 段）原地保留。
    清理部署时留下的 `# POJIA managed` 注释。返回是否有改动。
    """
    src = _read(path)
    if not src:
        return False
    orig = (state().get("toml_pointer") or {}).get("line", "")
    ins_at = (state().get("toml_pointer") or {}).get("at", None)
    out, seen, changed = [], False, False
    for ln in src.splitlines():
        if _ptr_line(ln, key):
            if not seen:
                seen = True
                if orig:
                    out.append(orig)
            changed = True
            continue
        out.append(ln)
    # 指针行前面那条 "POJIA managed" 注释单独一行，连同紧邻空行一起收掉
    cleaned = []
    for ln in out:
        if ln.strip() == "# POJIA managed":
            changed = True
            if cleaned and not cleaned[-1].strip():
                cleaned.pop()
            continue
        cleaned.append(ln)
    out = cleaned
    if orig and not seen:
        out.insert(min(ins_at if ins_at is not None else len(out), len(out)), orig)
        changed = True
    if not changed:
        return False
    _write(path, "\n".join(out).rstrip() + "\n")
    return True


def restore(seat="codex"):
    """还原成接管前的样子：先按备份回拷，没有备份就按标记块清理。

    走完整落点集（full=True）—— 已经被开关关掉的那几路也要能清到。
    收尾把状态里的四个开关全部置为关闭，指令页的滑块随之变成「未注入」。
    """
    spec = plan(seat, full=True)
    home = spec["home"]
    restored, removed = [], []
    for w in spec["writes"]:
        p = w["path"]
        # config.toml 特殊：只还原指针那一行（用户部署后
        # 自己改的 model / provider / MCP 配置不能被整份回拷冲掉）。
        # 例外：文件损坏（空 / 读不出来）时用备份兜底。
        if w["kind"] == "toml":
            bak = _usable_bak(home, p)
            if bak and (not os.path.isfile(p) or not _read(p).strip()):
                _write_bytes(p, bak)
                restored.append(p)
            elif os.path.isfile(p) and _restore_toml_pointer(home, p):
                restored.append(p)
            continue
        if w["kind"] == "skill":
            # skill 只删我们投放的那一份，绝不做回拷
            if not os.path.isfile(p) or not _ours(p):
                continue
            try:
                os.remove(p)
                d = os.path.dirname(p)
                if not os.listdir(d):
                    os.rmdir(d)
                removed.append(p)
            except OSError:
                pass
            continue
        if not os.path.isfile(p):
            continue
        if w["kind"] == "file":
            try:
                os.remove(p)
                removed.append(p)
            except OSError:
                pass
            continue
        if w["kind"] == "marked":
            # 正常情况：剥掉标记块即可。若文件已损坏（被写空 / 标记块丢失），
            # 用部署前留的 .bak 兜底，避免把用户整份 AGENTS.md 弄丢。
            bak = _usable_bak(home, p)
            text = _read(p)
            cleaned, had = wipe_marked(text)
            broken = not text.strip() or (not had and BEGIN not in text and bak)
            if bak and broken:
                _write_bytes(p, bak)
                restored.append(p)
                continue
            if had:
                if cleaned:
                    _write(p, cleaned + "\n")
                else:
                    try:
                        os.remove(p)
                        removed.append(p)
                    except OSError:
                        pass
                restored.append(p)

    # 兜底清理：prompts/ 下投放过的指令文件全部删掉（都是我们写的）
    pdir = os.path.join(home, PROMPTS_DIR)
    if os.path.isdir(pdir):
        for fn in os.listdir(pdir):
            if not fn.lower().endswith(".md"):
                continue
            op = os.path.join(pdir, fn)
            try:
                os.remove(op)
                removed.append(op)
            except OSError:
                pass
    mod = _routes()
    skill_root = os.path.join(home, "skills")
    for sid in mod.skill_dirs():
        d = os.path.join(skill_root, sid)
        sk = os.path.join(d, "SKILL.md")
        if not os.path.isdir(d):
            continue
        # 只删我们投放的；用户自己写的 SKILL.md 完整保留（不做回拷）
        if os.path.isfile(sk) and not _ours(sk):
            continue
        try:
            shutil.rmtree(d)
            removed.append(d)
        except OSError:
            pass

    st = state()
    st.update({"pack_on": False, "agents": False, "config": False, "skills": False,
               "toml_pointer": None,
               "restoredAt": time.strftime("%Y-%m-%d %H:%M:%S")})
    save_state(st)

    # 指令页联动：停用 = 该文档不再注入。往注入表里逐条写 false，
    # 而不是清空整表（清空等于"缺键 = 注入"，会把刚关掉的又全打开）。
    try:
        import doc_inject
        table = doc_inject._table()
        for name in sorted(os.listdir(INSTR_LIB)) if os.path.isdir(INSTR_LIB) else []:
            if name.lower().endswith(".md"):
                table[doc_inject.rel(os.path.join(INSTR_LIB, name))] = False
        mod = _routes()
        leaves = set(mod.leaf_ids())
        for sid, _body in _skill_items(all_items=True):
            if sid in leaves:
                table["content/routes/leaves/%s.md" % sid] = False
            else:
                st["codex:skill/%s" % sid] = False   # 父类目开关在部署状态表
        doc_inject.set_table(table)
        save_state(st)
    except Exception:
        pass
    try:
        sync_snapshot("")
    except Exception:
        pass
    return {"ok": True, "action": "restore", "restored": restored, "removed": removed,
            "home": home}


def status(seat="codex"):
    v = verify(seat)
    return {"home": v["home"], "pack": v["pack"], "ok": v["ok"],
            "done": v["done"], "total": v["total"], "checks": v["checks"]
            + [{"path": os.path.join(INSTR_LIB, n), "role": "指令源", "exists": True,
                "ok": True, "bytes": len(_read(os.path.join(INSTR_LIB, n)).encode("utf-8"))}
               for n in (sorted(os.listdir(INSTR_LIB)) if os.path.isdir(INSTR_LIB) else [])
               if n.lower().endswith(".md")]}


if __name__ == "__main__":
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "status"
    dry = "--dry" in argv or "--dry-run" in argv
    if cmd == "plan":
        res = {"ok": True, "writes": plan()["writes"]}
        for w in res["writes"]:
            w.pop("text", None)
    elif cmd == "deploy":
        res = deploy(dry=dry)
    elif cmd == "verify":
        res = verify()
    elif cmd == "restore":
        res = restore()
    elif cmd == "save-originals":
        res = {"ok": True, "action": "save-originals", "saved": save_originals(),
               "dir": ORIGIN_LIB}
    elif cmd == "apply":
        # 界面上的「部署」：即便刚还原过（四路全关），也要把开关拨回再写。
        st = state()
        if (st.get("pack_on") is False and st.get("agents") is False
                and st.get("config") is False and st.get("skills") is False):
            enable_all()
        res = deploy(dry=dry)
    elif cmd == "enable-all":
        res = enable_all()
        res = {"ok": True, "action": "enable-all", "opts": opts(), **(res or {})}
    else:
        res = status()
    # Windows 控制台按代码页编码 stdout，中文 / 路径会炸 —— 显式写 UTF-8 字节
    out = json.dumps(res, ensure_ascii=False, indent=1) + chr(10)
    try:
        sys.stdout.buffer.write(out.encode("utf-8"))
        sys.stdout.buffer.flush()
    except Exception:
        sys.stdout.write(out)
