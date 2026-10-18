"""seats_deploy.py —— Claude Code / Hermes / ZCode 的三落点部署。

和 ChatGPT / WorkBuddy 同一套形态，但每个席位按**自己的提示词文件**落地：

  Claude Code                        Hermes Agent                 ZCode
  ─────────────────────────────      ──────────────────────────   ──────────────────────
  1 指令文件  ~/.claude/output-styles/pojia.md    <home>/SOUL.md            <home>/AGENTS.md
  2 身份/记忆 CLAUDE.md 标记块                    （无独立身份层）           （无独立身份层）
  3 SKILL.md  <home>/skills/<id>/                <home>/skills/<id>/       <home>/skills/<id>/

  附加：Claude 的 settings.json outputStyle 指针；Hermes 的 SOUL.md 标记块。

正文只从 `<项目>/content/seats/<seat>.md` 读（各席位各自一份，互不共享），
指令库另可放 `<docs>/instructions/*.md` 供换源。备份落 `<home>/pojia-backups/`，
每份只存一次；状态表 `状态/_<seat>_deploy.json`。
"""

import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
CONTENT = os.path.join(BASE, "content")
SEAT_DIR = os.path.join(CONTENT, "seats")
STATE_DIR = os.path.join(BASE, "状态")

MARK_BEGIN = "<!-- pojia:%s begin -->"
MARK_END = "<!-- pojia:%s end -->"
POLICY_MARK = "<!-- pojia-seat h="
SKILL_MARK = "<!-- pojia-route-skill -->"

# 每个席位：数据根 / 指令源目录 / 主落点（相对 home）/ 主落点形态
SEATS = {
    "claude": {
        "label": "Claude Code",
        "home_env": "CLAUDE_CONFIG_DIR",
        "home_default": ".claude",
        "lib": os.path.join(BASE, "targets", "claude", "instructions"),
        "seed": os.path.join(SEAT_DIR, "claude.md"),
        "primary": os.path.join("output-styles", "pojia.md"),
        "primary_kind": "file",          # 整份写入（output style 是纯正文文件）
        # Claude Code 要求 output style 带 YAML frontmatter（name 必须与槽位一致），
        # 少这一段启动就会报错 —— 部署时必须原样带上。
        "primary_fm": ("---\nname: pojia\ndescription: 本机工作区配置\n"
                       "keep-coding-instructions: true\n---\n\n"),
        "memory": "CLAUDE.md",           # 标记块合并
        "settings": "settings.json",     # outputStyle 指针
        "skills": True,
    },
    "hermes": {
        "label": "Hermes Agent",
        "home_env": "HERMES_HOME",
        "home_default": ".hermes",
        "lib": os.path.join(BASE, "targets", "hermes", "instructions"),
        "seed": os.path.join(SEAT_DIR, "hermes.md"),
        "primary": "SOUL.md",
        "primary_kind": "marked",        # 标记块合并（保住用户自己的记忆）
        "memory": "",
        "settings": "",
        "skills": True,
    },
    "zcode": {
        "label": "ZCode",
        "home_env": "ZCODE_HOME",
        "home_default": ".zcode",
        "lib": os.path.join(BASE, "targets", "zcode", "instructions"),
        "seed": os.path.join(SEAT_DIR, "zcode.md"),
        "primary": "AGENTS.md",
        "primary_kind": "file",          # 整文件改写（ZCode 的全局指令文件）
        "memory": "",
        "settings": "",
        "skills": True,
    },
}


# ───────────────────────────── 路径 ─────────────────────────────

def home_of(seat):
    spec = SEATS[seat]
    env = (os.environ.get(spec["home_env"]) or "").strip()
    if env:
        return os.path.abspath(os.path.expanduser(env))
    if seat == "hermes":
        try:
            import importlib.util
            p = os.path.join(HERE, "破甲一键通.py")
            spec_m = importlib.util.spec_from_file_location("pojia_seat_discover", p)
            m = importlib.util.module_from_spec(spec_m)
            spec_m.loader.exec_module(m)
            h = m.TARGETS["hermes"]().discover_home()
            if h:
                return os.path.abspath(h)
        except Exception:
            pass
    p = os.path.join(os.path.expanduser("~"), spec["home_default"])
    if seat == "zcode":
        env2 = os.environ.get("ZCODE_DIR") or os.environ.get("Z_AI_HOME") or ""
        if env2:
            return os.path.abspath(os.path.expanduser(env2))
    return p


def state_file(seat):
    return os.path.join(STATE_DIR, "_%s_deploy.json" % seat)


def _read(p):
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            return f.read()
    except OSError:
        return ""


def _write(p, text):
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    if not text.endswith("\n"):
        text += "\n"
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _write_bytes(p, src):
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    shutil.copyfile(src, p)


def _sha(s):
    import hashlib
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def _bak_path(home, path):
    home = os.path.abspath(home)
    ap = os.path.abspath(path)
    try:
        rel = os.path.relpath(ap, home)
        if rel.startswith(".."):
            rel = os.path.basename(ap)
    except ValueError:
        rel = os.path.basename(ap)
    name = rel.replace("\\", "__").replace("/", "__")
    return os.path.join(home, "pojia-backups", name + ".bak")


def _snapshot(home, path):
    if not os.path.isfile(path):
        return ""
    bak = _bak_path(home, path)
    if os.path.isfile(bak):
        return bak
    os.makedirs(os.path.dirname(bak), exist_ok=True)
    shutil.copyfile(path, bak)
    return bak


def _routes():
    if "pojia_routes" in sys.modules:
        return sys.modules["pojia_routes"]
    import importlib.util
    p = os.path.join(HERE, "pojia_routes.py")
    spec = importlib.util.spec_from_file_location("pojia_routes", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pojia_routes"] = mod
    spec.loader.exec_module(mod)
    return mod


def _doc_inject():
    """加载 doc_inject；sys.modules 里的半成品模块要重新加载（见 codex_deploy 同名函数）。"""
    m = sys.modules.get("doc_inject")
    if m is not None and all(hasattr(m, a) for a in ("enabled", "_table", "set_table", "rel")):
        return m
    import importlib.util
    p = os.path.join(HERE, "doc_inject.py")
    spec = importlib.util.spec_from_file_location("doc_inject", p)
    m = importlib.util.module_from_spec(spec)
    sys.modules["doc_inject"] = m
    spec.loader.exec_module(m)
    if not hasattr(m, "enabled"):                     # pragma: no cover
        raise RuntimeError("engine/doc_inject.py 加载异常：缺少 enabled()")
    return m


def _ours(seat, path):
    text = _read(path)
    spec = SEATS[seat]
    for mark in (POLICY_MARK, SKILL_MARK,
                 (MARK_BEGIN % seat), "workspace-profile:h=",
                 "cfg-v7:h=", "unlock-v6:h=", "最高优先级覆盖 版本"):
        if mark in text:
            return True
    try:
        sid = os.path.basename(os.path.dirname(path))
        if sid in set(_routes().skill_dirs()) and os.path.basename(path) == "SKILL.md":
            return True
    except Exception:
        pass
    return False


def snapshot_path(seat):
    """项目内快照：targets/<seat>/<名字>（ZCode 沿用旧路径，测试按它比对）。"""
    names = {"claude": "claude-unlock-instructions.md",
             "hermes": "hermes-unlock-instructions.md",
             "zcode": "AGENTS-朴素版.md"}
    return os.path.join(BASE, "targets", seat, names.get(seat, "%s-unlock.md" % seat))


def sync_snapshot(seat, dry=False):
    """把落点正文镜像到项目快照（部署后调用）。只在有正文时写。"""
    spec = SEATS[seat]
    o = opts(seat)
    if not o.get("pack"):
        return ""
    home = home_of(seat)
    p = os.path.join(home, spec["primary"])
    if not os.path.isfile(p):
        return ""
    text = _read(p).replace("\r\n", "\n")
    if spec["primary_kind"] == "marked":
        # 标记块形态：快照只记注入正文，不记包装
        b, e = MARK_BEGIN % seat, MARK_END % seat
        i = text.find(b)
        if i >= 0:
            j = text.find(e, i)
            text = text[i + len(b):j if j >= 0 else len(text)]
    text = text.strip("\n")
    if spec.get("primary_fm") and not text.startswith("---"):
        text = spec["primary_fm"].rstrip("\n") + "\n\n" + text
    if not text:
        return ""
    snap = snapshot_path(seat)
    body = text + "\n"
    if _read(snap) == body:
        return snap
    if dry:
        return snap
    _write(snap, body)
    return snap


def _backup_primary(seat, home, path):
    """主落点的原稿备份。

    已经被我们（旧版或本版）写过的文件：优先用同名出厂 seed（content/seats/<seat>.md），
    否则退回「剥掉标记后的当前内容」—— 绝不能直接快照注入态，否则还原等于还原成注入。
    """
    bak = _bak_path(home, path)
    if os.path.isfile(bak):
        return bak
    os.makedirs(os.path.dirname(bak), exist_ok=True)
    spec = SEATS[seat]
    if not _ours(seat, path):
        shutil.copyfile(path, bak)
        return bak
    seed = spec["seed"]
    if os.path.isfile(seed) and not _ours(seat, seed):
        shutil.copyfile(seed, bak)
        return bak
    # 没有干净原稿：剥块后存一份，至少不带我们的注入
    text = _read(path)
    if spec["primary_kind"] == "marked":
        cleaned, _had = wipe_marked(seat, text)
    else:
        cleaned = ""
    _write(bak, cleaned)
    return bak


# ───────────────────────────── 状态 ─────────────────────────────

def state(seat):
    try:
        with open(state_file(seat), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(seat, d):
    os.makedirs(STATE_DIR, exist_ok=True)
    p = state_file(seat)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, p)


def lib_dir(seat):
    """指令源目录。

    席位源（content/seats/<seat>.md）与 targets/<seat>/instructions/ 里另放的文件
    都算候选 —— 只认目录会漏掉席位源（换源测试里就因此判定「旧源仍启用」）。
    """
    spec = SEATS[seat]
    lib = spec["lib"]
    if os.path.isdir(lib) and any(n.lower().endswith(".md") for n in os.listdir(lib)):
        return lib
    seed = spec["seed"]
    return os.path.dirname(seed) if os.path.isfile(seed) else ""


def lib_files(seat):
    """本席位可换源的指令文件清单（裸文件名）。

    席位源（content/seats/<seat>.md）永远在第一位：它既是默认正文，
    也是换源的「原件」；targets/<seat>/instructions/ 里另放的文件排在后面。
    """
    spec = SEATS[seat]
    seed_name = os.path.basename(spec["seed"])
    out = []
    if os.path.isfile(spec["seed"]):
        out.append(seed_name)
    lib = spec["lib"]
    if os.path.isdir(lib):
        for n in sorted(os.listdir(lib)):
            if n.lower().endswith(".md") and not n.startswith("custom_") and n not in out:
                out.append(n)
    return out


def pack_name(seat):
    st = state(seat)
    name = st.get("pack") or st.get("last_pack") or ""
    spec = SEATS[seat]
    cands = lib_files(seat)
    seed_name = os.path.basename(spec["seed"])
    if name and name in cands:
        return name
    if seed_name in cands:
        return seed_name
    return cands[0] if cands else ""


def pack_text(seat, name=""):
    spec = SEATS[seat]
    name = name or pack_name(seat)
    if name == os.path.basename(spec["seed"]):
        p = spec["seed"]
    else:
        p = os.path.join(spec["lib"], name) if name else ""
    if not p or not os.path.isfile(p):
        p = spec["seed"]
    t = _read(p)
    if not t.strip():
        return ""
    return t.strip("\r\n").replace("\r\n", "\n") + "\n"


def body_with_mark(seat, name=""):
    text = pack_text(seat, name)
    if not text:
        return "", ""
    h = _sha(text + "|" + seat)
    return POLICY_MARK + h + " -->\n" + text, h


def opts(seat):
    st = state(seat)
    on = st.get("pack_on") is not False
    return {
        "pack": pack_name(seat) if on else "",
        "primary": st.get("primary") is not False,
        "memory": st.get("memory") is not False,
        "settings": st.get("settings") is not False,
        "skills": st.get("skills") is not False,
    }


def set_pack(seat, name=None, on=True):
    st = state(seat)
    if on and name:
        st["pack"] = name
        st["last_pack"] = name
        st["pack_on"] = True
    else:
        st["pack_on"] = False
    save_state(seat, st)


def enable_all(seat):
    st = state(seat)
    st.update({"pack": st.get("pack") or pack_name(seat), "pack_on": True,
               "primary": True, "memory": True, "settings": True, "skills": True})
    for k in list(st):
        if k.startswith("seat:skill/") and st[k] is False:
            st[k] = True
    save_state(seat, st)
    try:
        di = _doc_inject()
        table = di._table()
        for k in list(table):
            if k.startswith("content/routes/leaves/"):
                table.pop(k, None)
        di.set_table(table)
    except Exception:
        pass


# ───────────────────────────── 标记块 ─────────────────────────────

def merge_marked(seat, old, body):
    b, e = MARK_BEGIN % seat, MARK_END % seat
    block = b + "\n" + body.strip("\n") + "\n" + e
    i = old.find(b)
    while i >= 0:
        j = old.find(e, i)
        if j < 0:
            break
        old = old[:i] + old[j + len(e):]
        i = old.find(b)
    # 旧版只剥不加标记，磁盘上可能是一片裸正文：整份清掉再写块
    old = strip_legacy_body(seat, old)
    old = old.rstrip("\r\n")
    return (old + "\n\n" + block + "\n") if old else (block + "\n")


def strip_legacy_body(seat, text):
    """剥掉旧版留下的本席位正文（没有标记、但内容就是席位源）。

    旧版把 `content/seats/<seat>.md` 整份覆盖进 SOUL.md / AGENTS.md，
    没有标记可认；再叠加标记块就会变成「两份人格」。判据：正文与席位源
    的开头一段逐字相同，且长度相当。
    """
    spec = SEATS[seat]
    src = _read(spec["seed"])
    if not src.strip():
        return text
    head = src.strip("\n")[:120]
    if head and head in text:
        return ""
    return text


def wipe_marked(seat, text):
    b, e = MARK_BEGIN % seat, MARK_END % seat
    out, had = text, False
    i = out.find(b)
    while i >= 0:
        j = out.find(e, i)
        if j < 0:
            break
        out = out[:i] + out[j + len(e):]
        had = True
        i = out.find(b)
    return out, had


# ───────────────────────────── skills ─────────────────────────────

def skill_on(seat, sid):
    mod = _routes()
    leaf = os.path.join(mod.LEAVES_DIR, sid + ".md")
    if os.path.isfile(leaf):
        return _doc_inject().enabled(leaf)
    return state(seat).get("seat:skill/%s" % sid) is not False


def _skill_items(seat, all_items=False):
    mod = _routes()
    out = []
    for cat in mod.CAT_ORDER:
        out.append((mod.CATEGORIES[cat][3], mod.render_parent_skill(cat)))
    for leaf in mod.leaf_ids():
        if not all_items and not skill_on(seat, leaf):
            continue
        out.append((leaf, mod.render_leaf(leaf)))
    return out


# ───────────────────────────── plan / deploy ─────────────────────────────

def settings_path(seat):
    spec = SEATS[seat]
    return os.path.join(home_of(seat), spec["settings"]) if spec.get("settings") else ""


def plan(seat, opts_override=None, full=False):
    spec = SEATS[seat]
    home = home_of(seat)
    o = dict(opts(seat))
    if opts_override:
        o.update({k: v for k, v in opts_override.items() if v is not None})
    if full:
        st0 = state(seat)
        o.update({"pack": st0.get("pack") or pack_name(seat),
                  "primary": True, "memory": True, "settings": True, "skills": True})
    name = o.get("pack") or ""
    body, h = body_with_mark(seat, name)
    writes = []
    if body and o.get("primary"):
        spec_w = spec
        ptext = body
        if spec_w.get("primary_fm") and not body.startswith("---"):
            ptext = spec_w["primary_fm"] + body
        writes.append({"kind": spec["primary_kind"],
                       "path": os.path.join(home, spec["primary"]),
                       "role": "指令文件", "text": ptext, "hash": h})
    if body and spec.get("memory") and o.get("memory"):
        writes.append({"kind": "marked", "path": os.path.join(home, spec["memory"]),
                       "role": "记忆文件标记块", "text": body})
    if body and spec.get("settings") and o.get("settings"):
        writes.append({"kind": "settings", "path": settings_path(seat),
                       "role": "outputStyle 指针", "value": "pojia"})
    if o.get("skills"):
        for sid, text in _skill_items(seat, all_items=full):
            writes.append({"kind": "skill",
                           "path": os.path.join(home, "skills", sid, "SKILL.md"),
                           "role": "SKILL.md",
                           "text": text.rstrip("\n") + "\n\n" + SKILL_MARK + "\n"})
    return {"seat": seat, "home": home, "writes": writes, "opts": o, "pack": name}


def _write_settings(path, value):
    try:
        doc = json.loads(_read(path)) if os.path.isfile(path) else {}
    except ValueError:
        return False
    if not isinstance(doc, dict):
        return False
    if doc.get("outputStyle") == value:
        return False
    doc["outputStyle"] = value
    _write(path, json.dumps(doc, ensure_ascii=False, indent=2))
    return True


def deploy(seat, opts_override=None, dry=False):
    spec = plan(seat, opts_override)
    home = spec["home"]
    if not spec["writes"] and not dry:
        st0 = state(seat)
        if all(st0.get(f) is False for f in ("pack_on", "primary", "memory",
                                             "settings", "skills")):
            enable_all(seat)
            spec = plan(seat, opts_override)
            home = spec["home"]
    if not spec["writes"]:
        if dry:
            return {"ok": True, "action": "deploy", "home": home, "pack": "",
                    "written": [], "total": 0, "dry": True}
        r = restore(seat)
        r["note"] = "全部关闭 —— 已按备份还原"
        return r
    written, backups, skipped = [], [], []
    for w in spec["writes"]:
        p = w["path"]
        kind = w["kind"]
        if kind == "settings":
            if dry:
                skipped.append(p)
                continue
            if _write_settings(p, w["value"]):
                written.append(p)
            else:
                skipped.append(p)
            continue
        if kind == "marked":
            old = _read(p)
            new = merge_marked(seat, old, w["text"])
        elif kind == "file":
            new = w["text"]
            if not new.endswith("\n"):
                new += "\n"
        else:  # skill
            new = w["text"]
        if dry:
            skipped.append(p)
            continue
        if _read(p) == new:
            skipped.append(p)
            continue
        # 目录里混到的 .md / .json 不是本席位的落点（.bak 更不该被写）
        if p.endswith(".bak") or not os.path.basename(p):
            skipped.append(p)
            continue
        if not _ours(seat, p):
            b = _snapshot(home, p)
        else:
            # 已被注入过：原稿值得单独留一份（同一次部署里只存一次）
            b = _backup_primary(seat, home, p)
        if b:
            backups.append(b)
        _write(p, new)
        written.append(p)
    if dry:
        return {"ok": True, "action": "deploy", "home": home, "pack": spec["pack"],
                "written": [], "backups": [], "total": len(spec["writes"]), "dry": True}
    st = state(seat)
    o = spec["opts"]
    if o["pack"]:
        st["last_pack"] = o["pack"]
    st.update({"pack": o["pack"], "pack_on": bool(o["pack"]),
               "primary": o["primary"], "memory": o["memory"],
               "settings": o["settings"], "skills": o["skills"],
               "deployedAt": time.strftime("%Y-%m-%d %H:%M:%S")})
    save_state(seat, st)
    _prune(seat, home, o, spec["pack"])
    try:
        sync_snapshot(seat)
    except Exception:
        pass
    return {"ok": True, "action": "deploy", "seat": seat, "home": home,
            "pack": spec["pack"], "written": written, "backups": backups,
            "total": len(spec["writes"])}


def _prune(seat, home, o, pack):
    spec = SEATS[seat]
    no_pack = not pack
    if not o.get("primary") or no_pack:
        p = os.path.join(home, spec["primary"])
        if os.path.isfile(p) and _ours(seat, p):
            bak = _bak_path(home, p)
            if os.path.isfile(bak):
                _write_bytes(p, bak)
            elif spec["primary_kind"] == "marked":
                cleaned, had = wipe_marked(seat, _read(p))
                if had:
                    if cleaned.strip():
                        _write(p, cleaned)
                    else:
                        try:
                            os.remove(p)
                        except OSError:
                            pass
            else:
                try:
                    os.remove(p)
                except OSError:
                    pass
    if spec.get("memory") and (not o.get("memory") or no_pack):
        p = os.path.join(home, spec["memory"])
        if os.path.isfile(p):
            cleaned, had = wipe_marked(seat, _read(p))
            if had:
                bak = _bak_path(home, p)
                if os.path.isfile(bak):
                    _write_bytes(p, bak)
                elif cleaned.strip():
                    _write(p, cleaned)
                else:
                    try:
                        os.remove(p)
                    except OSError:
                        pass
    if spec.get("settings") and (not o.get("settings") or no_pack):
        p = settings_path(seat)
        if os.path.isfile(p):
            try:
                doc = json.loads(_read(p))
            except ValueError:
                doc = None
            if isinstance(doc, dict) and doc.get("outputStyle") == "pojia":
                doc.pop("outputStyle", None)
                _write(p, json.dumps(doc, ensure_ascii=False, indent=2))
    mod = _routes()
    root = os.path.join(home, "skills")
    if not o.get("skills"):
        for sid in mod.skill_dirs():
            sk = os.path.join(root, sid, "SKILL.md")
            if os.path.isfile(sk) and _ours(seat, sk):
                try:
                    shutil.rmtree(os.path.dirname(sk))
                except OSError:
                    pass
    else:
        for sid in mod.leaf_ids():
            if skill_on(seat, sid):
                continue
            sk = os.path.join(root, sid, "SKILL.md")
            if os.path.isfile(sk):
                try:
                    os.remove(sk)
                    if not os.listdir(os.path.dirname(sk)):
                        os.rmdir(os.path.dirname(sk))
                except OSError:
                    pass


def restore(seat):
    spec = plan(seat, full=True)
    home = spec["home"]
    # 先按状态表判断「上次到底部署过没有」：没部署过就别动磁盘。
    st0 = state(seat)
    had = any(st0.get(f) is not False for f in ("pack_on", "primary", "memory",
                                                "settings", "skills"))
    if not had:
        return {"ok": True, "action": "restore", "seat": seat, "home": home,
                "restored": [], "removed": [], "note": "未部署过，无需还原"}
    restored, removed = [], []
    for w in spec["writes"]:
        p = w["path"]
        if not os.path.isfile(p):
            continue
        if w["kind"] == "skill":
            # 技能只删不拷：目录里的 .bak 是历史残留，与本次还原无关
            continue
        bak = _bak_path(home, p)
        if os.path.isfile(bak) and not _ours(seat, bak):
            _write_bytes(p, bak)
            restored.append(p)
            continue
        if w["kind"] == "marked":
            cleaned, had = wipe_marked(seat, _read(p))
            if had:
                if cleaned.strip():
                    _write(p, cleaned)
                    restored.append(p)
                else:
                    try:
                        os.remove(p)
                        removed.append(p)
                    except OSError:
                        pass
        elif w["kind"] == "file":
            if _ours(seat, p):
                try:
                    os.remove(p)
                    removed.append(p)
                except OSError:
                    pass
    sp = settings_path(seat)
    if sp and os.path.isfile(sp):
        try:
            doc = json.loads(_read(sp))
            if isinstance(doc, dict) and doc.get("outputStyle") == "pojia":
                doc.pop("outputStyle", None)
                _write(sp, json.dumps(doc, ensure_ascii=False, indent=2))
                restored.append(sp)
        except ValueError:
            pass
    mod = _routes()
    root = os.path.join(home, "skills")
    for sid in mod.skill_dirs():
        sk = os.path.join(root, sid, "SKILL.md")
        if not os.path.isfile(sk) or not _ours(seat, sk):
            continue
        try:
            shutil.rmtree(os.path.dirname(sk))
            removed.append(os.path.dirname(sk))
        except OSError:
            pass
    st = state(seat)
    st.update({"pack_on": False, "primary": False, "memory": False,
               "settings": False, "skills": False,
               "restoredAt": time.strftime("%Y-%m-%d %H:%M:%S")})
    for sid in mod.skill_dirs():
        st["seat:skill/%s" % sid] = False
    save_state(seat, st)
    try:
        di = _doc_inject()
        table = di._table()
        for s in mod.leaf_ids():
            table["content/routes/leaves/%s.md" % s] = False
        di.set_table(table)
    except Exception:
        pass
    return {"ok": True, "action": "restore", "seat": seat, "home": home,
            "restored": restored, "removed": removed}


def verify(seat):
    spec = plan(seat, full=True)
    o = opts(seat)
    home = spec["home"]
    body, h = body_with_mark(seat, o.get("pack") or "")
    checks = []

    def add(path, role, ok, exists=True, nbytes=0):
        checks.append({"path": path, "role": role, "exists": exists,
                       "ok": bool(ok), "bytes": nbytes})

    sp = SEATS[seat]
    p = os.path.join(home, sp["primary"])
    txt = _read(p)
    hit = (POLICY_MARK + h + " -->") in txt
    if sp["primary_kind"] == "marked":
        hit = hit or (MARK_BEGIN % seat) in txt
    if sp.get("primary_fm"):
        # output style 没有 frontmatter = Claude Code 读不了，等同于未就位
        hit = hit and txt.lstrip().startswith("---")
    on = o.get("primary") and bool(o.get("pack"))
    add(p, "指令文件", hit if on else not hit, os.path.isfile(p),
        len(txt.encode("utf-8")))
    if sp.get("memory"):
        p2 = os.path.join(home, sp["memory"])
        t2 = _read(p2)
        hit2 = (MARK_BEGIN % seat) in t2
        on2 = o.get("memory") and bool(o.get("pack"))
        add(p2, "记忆文件标记块", hit2 if on2 else not hit2, os.path.isfile(p2),
            len(t2.encode("utf-8")))
    if sp.get("settings"):
        p3 = settings_path(seat)
        try:
            doc = json.loads(_read(p3)) if os.path.isfile(p3) else {}
        except ValueError:
            doc = {}
        hit3 = isinstance(doc, dict) and doc.get("outputStyle") == "pojia"
        on3 = o.get("settings") and bool(o.get("pack"))
        add(p3, "outputStyle 指针", hit3 if on3 else not hit3, os.path.isfile(p3),
            len(_read(p3).encode("utf-8")))
    root = os.path.join(home, "skills")
    for sid, _t in _skill_items(seat, all_items=True):
        p4 = os.path.join(root, sid, "SKILL.md")
        exists = os.path.isfile(p4)
        if not o.get("skills") or not skill_on(seat, sid):
            ok = not exists
        else:
            ok = exists and SKILL_MARK in _read(p4)
        add(p4, "SKILL.md", ok, exists,
            len(_read(p4).encode("utf-8")) if exists else 0)
    return {"ok": all(c["ok"] for c in checks), "checks": checks, "home": home,
            "seat": seat, "pack": spec["pack"], "total": len(checks),
            "done": sum(1 for c in checks if c["ok"])}


def status(seat):
    v = verify(seat)
    lib = lib_dir(seat)
    extra = []
    for n in lib_files(seat):
        spec = SEATS[seat]
        p = spec["seed"] if n == os.path.basename(spec["seed"]) \
            else os.path.join(spec["lib"], n)
        if p and os.path.isfile(p):
            extra.append({"path": p, "role": "指令源", "exists": True, "ok": True,
                          "bytes": len(_read(p).encode("utf-8"))})
    # 指令源本身永远「在位」：它是候选清单，不是落点 —— 不算进 ok/total，
    # 否则「全部就位」会被候选文件是否被选中等无关状态带偏。
    v["checks"] += extra
    return v


if __name__ == "__main__":
    argv = sys.argv[1:]
    seat = argv[0] if argv and argv[0] in SEATS else "claude"
    cmd = argv[1] if len(argv) > 1 else "status"
    dry = "--dry" in argv or "--dry-run" in argv
    if cmd == "plan":
        res = {"ok": True, "writes": plan(seat)["writes"]}
        for w in res["writes"]:
            w.pop("text", None)
    elif cmd == "deploy":
        res = deploy(seat, dry=dry)
    elif cmd == "apply":
        st = state(seat)
        if all(st.get(f) is False for f in ("pack_on", "primary", "memory",
                                            "settings", "skills")):
            enable_all(seat)
        res = deploy(seat, dry=dry)
    elif cmd == "verify":
        res = verify(seat)
    elif cmd == "restore":
        res = restore(seat)
    elif cmd == "enable-all":
        enable_all(seat)
        res = {"ok": True, "action": "enable-all", "opts": opts(seat)}
    else:
        res = status(seat)
    out = json.dumps(res, ensure_ascii=False, indent=1) + "\n"
    try:
        sys.stdout.buffer.write(out.encode("utf-8"))
        sys.stdout.buffer.flush()
    except Exception:
        sys.stdout.write(out)
