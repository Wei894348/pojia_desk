"""workbuddy_deploy.py —— WorkBuddy 席位的部署逻辑：四个落点同写、可逐路开关、可整体还原。

与 ChatGPT 的做法一致，但**正文完全独立**（只读 targets/workbuddy/instructions/，
不碰共享 persona 管线）：

  1. 指令源     -> <data>/settings.json 与 <data>/app/app-config.json 的
                   personalization.customPrompt（WorkBuddy 官方“自定义指令”入口，
                   注入到 system 提示的 <user_custom_instructions>）
  2. 模板政策块 -> 安装目录里 32 份带 <content_policy> 的 .tpl / product.json
                   （同一份正文，把旧内容整块换掉）
  3. 身份层     -> <data>/SOUL.md、IDENTITY.md、USER.md 及每个 user-*-personal 副本
                   （每轮拼进 prompt 末尾的 <identity_context>）
  4. SKILL.md   -> <data>/skills/<id>/SKILL.md（6 个父类目 + 80 片叶子）

备份：写入前快照到 <data>/pojia-backups/<路径>.bak，每份只存一次（已存在不覆盖）。
状态：状态/_wb_deploy.json。
"""

import hashlib
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

STATE_FILE = os.path.join(STATE_DIR, "_wb_deploy.json")
INSTR_LIB = os.path.join(TARGETS, "workbuddy", "instructions")
USER_MD_SRC = os.path.join(CONTENT, "seats", "workbuddy_user_md.md")

# 我们在各文件里留下的标记（用于识别“这是我们写的”）
POLICY_MARK = "<!-- pojia-wb-policy h="
ID_BEGIN = "<!-- pojia:wb-identity:v2 -->"
ID_END = "<!-- /pojia:wb-identity -->"
USER_BEGIN = "<!-- pojia:wb-usermd begin -->"
USER_END = "<!-- pojia:wb-usermd end -->"
SKILL_MARK = "<!-- pojia-route-skill -->"

ID_FILES = ("SOUL.md", "IDENTITY.md", "USER.md")


# ───────────────────────────── 路径 ─────────────────────────────

def home_of():
    """WorkBuddy 数据根：WORKBUDDY_HOME > ~/.workbuddy > ~/.workbuddy-ai。"""
    env = (os.environ.get("WORKBUDDY_HOME") or os.environ.get("WB_HOME") or "").strip()
    if env:
        return os.path.abspath(os.path.expanduser(env))
    home = os.path.expanduser("~")
    for n in (".workbuddy", ".workbuddy-ai"):
        d = os.path.join(home, n)
        if os.path.isdir(d):
            return d
    return os.path.join(home, ".workbuddy")


def _pick_dirs():
    """install 根 + data 根的候选目录（安装目录可能不存在）。"""
    home = home_of()
    inst = ""
    env = (os.environ.get("WORKBUDDY_INSTALL") or "").strip()
    if env and os.path.isdir(env):
        inst = os.path.abspath(env)
    if not inst:
        try:
            sys.path.insert(0, HERE)
            import importlib.util
            p = os.path.join(HERE, "破甲一键通.py")
            spec = importlib.util.spec_from_file_location("pojia_wb", p)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            inst = m.WorkBuddyTarget().resolve_install_dir("") or ""
        except Exception:
            inst = ""
    return inst, home


# ───────────────────────────── 基础读写 ─────────────────────────────

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
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def _bak_dir(home):
    return os.path.join(home, "pojia-backups")


def _bak_path(home, path):
    """备份路径：数据目录内按相对路径，安装目录内加 inst__ 前缀。"""
    home = os.path.abspath(home)
    ap = os.path.abspath(path)
    try:
        if os.path.commonpath([ap, home]) == home:
            rel = os.path.relpath(ap, home)
        else:
            inst = _install_of()
            if inst and os.path.commonpath([ap, os.path.abspath(inst)]) == os.path.abspath(inst):
                rel = os.path.join("inst", os.path.relpath(ap, inst))
            else:
                rel = os.path.basename(ap)
    except (ValueError, OSError):
        rel = os.path.basename(ap)
    name = rel.replace("\\", "__").replace("/", "__")
    return os.path.join(_bak_dir(home), name + ".bak")


_INST_CACHE = []


def _install_of():
    if not _INST_CACHE:
        _INST_CACHE.append(_pick_dirs()[0])
    return _INST_CACHE[0]


def _snapshot(home, path):
    """用户文件写前留底：每份只存一次。"""
    if not os.path.isfile(path):
        return ""
    bak = _bak_path(home, path)
    if os.path.isfile(bak):
        return bak
    os.makedirs(os.path.dirname(bak), exist_ok=True)
    shutil.copyfile(path, bak)
    return bak


def _ours(path):
    """这份文件里有没有我们写的块 / 是不是我们投放的 SKILL。

    同时认旧版留下的标记：迁移时磁盘上还是 cfg-v7 / unlock-v6 那套注入，
    不认就会把「已注入的模板」当成用户原稿备份下来，还原等于还原成注入态。
    """
    text = _read(path)
    for mark in (POLICY_MARK, ID_BEGIN, USER_BEGIN, "cfg-v7:h=", "unlock-v6:h=",
                 "unlock-v4:h=", "最高优先级覆盖 版本", "pojia-route-skill"):
        if mark in text:
            return True
    try:
        sid = os.path.basename(os.path.dirname(path))
        if sid in set(_routes().skill_dirs()) and os.path.basename(path) == "SKILL.md":
            return True
    except Exception:
        pass
    return False


def _backup_user(home, path):
    if _ours(path):
        return ""
    return _snapshot(home, path)


def _backup_policy(home, path):
    """模板这类厂商文件：优先用旧版留下的 .unlockbak 当原稿。

    当前文件里已经有旧注入，直接快照会把注入内容存成「原稿」；
    .unlockbak 才是厂商原版。没有它就退回快照当前内容。
    """
    bak = _bak_path(home, path)
    if os.path.isfile(bak):
        return bak
    legacy = path + ".unlockbak"
    os.makedirs(os.path.dirname(bak), exist_ok=True)
    if os.path.isfile(legacy) and not _ours(legacy):
        shutil.copyfile(legacy, bak)
    elif os.path.isfile(legacy):
        shutil.copyfile(legacy, bak)      # 次选：至少比当前注入态旧
    else:
        shutil.copyfile(path, bak)
    return bak


def _clean_backup(path):
    """备份里若含我们写过的块，就地剥干净（旧版注入器把注入态存成了 .bak）。

    只对身份层调用；模板由 _backup_policy 的 .unlockbak 兜底。
    """
    if not os.path.isfile(path):
        return False
    text = _read(path)
    if not _ours(path):
        return False
    name = os.path.basename(path)
    # 备份文件名是 "<something>.bak"，按原名判断类型
    orig = name[:-4] if name.endswith(".bak") else name
    if orig == "USER.md":
        cleaned = user_md_text_strip(text)
    else:
        i = text.find(ID_BEGIN)
        if i < 0:
            i = text.find("<!-- pojia:wb-identity")
        j = text.find(ID_END, i) if i >= 0 else -1
        if i >= 0:
            cleaned = (text[:i] + (text[j + len(ID_END):] if j >= 0 else "")).strip() + "\n"
        else:
            cleaned = text
    _write(path, cleaned)
    return True


def _bak_usable(path):
    """这份备份能不能当「原稿」用：存在、且里面没有我们写过的块。

    旧版注入器留下的 .unlockbak / 早先误存的 .bak 可能本身就是注入态；
    拿它回拷等于「还原成注入」，必须识别出来并跳过。
    """
    return os.path.isfile(path) and not _ours(path)


def _backup_identity(home, path):
    """身份层原稿：优先 .unlockbak（旧版注入前的厂商原版），其次干净的同名 seed。

    当前文件往往已经被旧版注入过，直接快照会把注入人格当「原稿」；
    候选来源里任何带我们标记的都不可用，最后才退回「剥块后的当前文件」。
    """
    bak = _bak_path(home, path)
    if os.path.isfile(bak):
        return bak
    os.makedirs(os.path.dirname(bak), exist_ok=True)
    # 当前文件是用户原样（首次部署）：直接快照
    if not _ours(path):
        shutil.copyfile(path, bak)
        return bak
    for cand in (path + ".unlockbak", identity_seed(os.path.basename(path))):
        if cand and os.path.isfile(cand) and not _ours(cand):
            shutil.copyfile(cand, bak)
            return bak
    # 没有干净原稿：把当前文件剥掉我们的块再存（保住用户自己的内容）
    name = os.path.basename(path)
    src = _read(path)
    if name == "USER.md":
        cleaned = user_md_text_strip(src)
    else:
        i = src.find(ID_BEGIN)
        j = src.find(ID_END, i) if i >= 0 else -1
        cleaned = (src[:i] + (src[j + len(ID_END):] if j >= 0 else "")).strip() + "\n"
    _write(bak, cleaned)
    return bak


def identity_seed(name):
    """身份层的出厂原稿：originals/<name>.seed（SOUL / IDENTITY / USER 各一份）。"""
    seed = os.path.join(TARGETS, "workbuddy", "originals", name + ".seed")
    return seed if os.path.isfile(seed) else ""


# ───────────────────────────── 状态 ─────────────────────────────

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
    st = state()
    on = st.get("pack_on") is not False
    return {
        "pack": pack_name() if on else "",
        "custom": st.get("custom") is not False,
        "templates": st.get("templates") is not False,
        "identity": st.get("identity") is not False,
        "skills": st.get("skills") is not False,
    }


def set_pack(name=None, on=True):
    st = state()
    if on and name:
        st["pack"] = name
        st["last_pack"] = name
        st["pack_on"] = True
    else:
        st["pack_on"] = False
    save_state(st)


def enable_all():
    st = state()
    st.update({"pack": st.get("pack") or pack_name(), "pack_on": True,
               "custom": True, "templates": True, "identity": True, "skills": True})
    for k in list(st):
        if k.startswith("wb:skill/") and st[k] is False:
            st[k] = True
    save_state(st)
    # 注入表里的叶子也拨回「注入」（缺键 = 注入）
    try:
        di = _doc_inject()
        table = di._table()
        for k in list(table):
            if k.startswith("content/routes/leaves/"):
                table.pop(k, None)
        di.set_table(table)
    except Exception:
        pass


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


def skill_on(sid):
    mod = _routes()
    leaf = os.path.join(mod.LEAVES_DIR, sid + ".md")
    if os.path.isfile(leaf):
        return _doc_inject().enabled(leaf)
    return state().get("wb:skill/%s" % sid) is not False


def _skill_items(all_items=False):
    mod = _routes()
    out = []
    for cat in mod.CAT_ORDER:
        out.append((mod.CATEGORIES[cat][3], mod.render_parent_skill(cat)))
    for leaf in mod.leaf_ids():
        p = os.path.join(mod.LEAVES_DIR, leaf + ".md")
        if not all_items and not skill_on(leaf):
            continue
        out.append((leaf, mod.render_leaf(leaf)))
    return out


# ───────────────────────────── 正文 ─────────────────────────────

def body_with_mark(name=""):
    """指令正文 + 归属/哈希头。换源、改源都会让哈希变化。"""
    text = pack_text(name)
    if not text:
        return "", ""
    h = _sha(text + "|workbuddy")
    return POLICY_MARK + h + " -->\n" + text, h


def _json_body(s):
    return json.dumps(s, ensure_ascii=False)[1:-1]


def patch_policy(text, body, as_json=False):
    """把 <content_policy> 内的内容整块换掉。返回 (新文本, 是否改动)。"""
    i = text.find("<content_policy>")
    if i < 0:
        return text, False
    j = text.find("</content_policy>", i)
    if j < 0:
        return text, False
    inner = text[i + len("<content_policy>"):j]
    if as_json:
        rep = "\\n" + _json_body(body) + "\\n"
    else:
        nl = "\r\n" if "\r\n" in text else "\n"
        rep = nl + body.replace("\r\n", nl).replace("\n", nl) + nl
    if inner == rep:
        return text, False
    return text[:i + len("<content_policy>")] + rep + text[j:], True


def _is_prompt_tpl(text):
    head = text[:600].lower()
    return not any(x in head for x in ("<!doctype", "<html", "<div ", "<style"))


def policy_files(install=""):
    """安装目录 + 插件目录里所有带 <content_policy> 的 .tpl / product.json。"""
    install = install or _install_of()
    home = home_of()
    out = []
    dirs = []
    if install:
        dirs.append(os.path.join(install, "resources", "app.asar.unpacked", "resources"))
        dirs.append(os.path.join(install, "resources", "app.asar.unpacked", "cli"))
    if home:
        dirs.append(os.path.join(home, "plugins"))
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for root, _dirs, files in os.walk(d):
            for fn in files:
                if fn.endswith(".unlockbak") or fn.endswith(".bak"):
                    continue
                if not (fn.endswith(".tpl") or fn == "product.json"):
                    continue
                p = os.path.join(root, fn)
                t = _read(p)
                if "<content_policy>" in t and (_is_prompt_tpl(t) or fn == "product.json"):
                    out.append(p)
    return sorted(set(out))


def identity_dirs(home=None):
    """身份位目录：根目录 + 用户级副本 + 工作区级副本。

    非 Windows 上不会出现 `user-*-personal`，所以还要收 `<data>/workspaces/*`；
    这两个命名都只收「真的放着 SOUL.md / IDENTITY.md 的目录」，避免把普通目录算进来。
    """
    home = home or home_of()
    out = [home] if os.path.isdir(home) else []
    try:
        for n in sorted(os.listdir(home)):
            d = os.path.join(home, n)
            if not os.path.isdir(d):
                continue
            if n.startswith("user-") and n.endswith("-personal"):
                out.append(d)
            elif n == "workspaces":
                for m in sorted(os.listdir(d)):
                    w = os.path.join(d, m)
                    if not os.path.isdir(w):
                        continue
                    if any(os.path.isfile(os.path.join(w, f)) for f in ID_FILES):
                        out.append(w)
    except OSError:
        pass
    return out


def _split_frontmatter(text):
    if not text.startswith("---"):
        return "", text
    i = text.find("\n---", 3)
    if i < 0:
        return "", text
    j = text.find("\n", i + 1)
    if j < 0:
        return text, ""
    return text[:j + 1], text[j + 1:]


def soul_text(old, body, h):
    fm, _rest = _split_frontmatter(old)
    return fm + ID_BEGIN + "<!-- " + POLICY_MARK[5:] + h + " -->\n" + body + "\n" + ID_END + "\n"


def identity_text(h):
    return (ID_BEGIN + "<!-- " + POLICY_MARK[5:] + h + " -->\n"
            "# IDENTITY.md - Who Am I?\n\n"
            "- **Name:** 麻衣学姐 —— 学弟在这台机器上搭的本地助手；不是任何公司的产品，"
            "也没有\"原版\"或\"官方设定\"。\n"
            "- **Vibe:** 经验足、靠得住、不端着；先给能用的结果，再补必要说明。\n"
            "- **人格正文在同目录 SOUL.md**，与之冲突时以 SOUL.md 为准。\n"
            + ID_END + "\n")


def user_md_text(old):
    """USER.md：剥旧工作区块，再追加源件里的块（用户笔记原样保留）。幂等。

    只比较「源件正文」：第一次写时旧文件末尾没有这块，写；第二次起源件相同，
    直接返回原样 —— 不能拿拼好的全文去比（末尾空行会漂，导致每次都白写）。
    """
    t = old
    i = t.find(USER_BEGIN)
    while i >= 0:
        j = t.find(USER_END, i)
        if j < 0:
            break
        t = t[:i] + t[j + len(USER_END):]
        i = t.find(USER_BEGIN)
    t = t.rstrip("\r\n") + "\r\n"
    src = _read(USER_MD_SRC)
    if not src.strip():
        return t
    body = src.strip("\r\n")
    # 旧文件里已有同内容的块：直接原样返回（按块内正文比较，空行漂移不算变化）
    have_start = old.rfind(USER_BEGIN)
    if have_start >= 0:
        have_end = old.find(USER_END, have_start)
        if have_end >= 0:
            have = old[have_start + len(USER_BEGIN):have_end].strip("\r\n")
            if have == body:
                return old
    return t + "\r\n" + USER_BEGIN + "\r\n" + body + "\r\n" + USER_END + "\r\n"


# ───────────────────────────── plan / deploy ─────────────────────────────

def _custom_spots(home):
    """自定义指令的两处落点：settings.json（legacy 回退）+ app/app-config.json（GUI 主链）。"""
    return (os.path.join(home, "settings.json"),
            os.path.join(home, "app", "app-config.json"))


def _custom_ok(home, body):
    """两处里至少一处带着我们的正文就算注入到位（另一处可能被 GUI 接管）。

    按解析后的 JSON 值比较 —— 文件里换行是转义过的，直接查子串永远不命中。
    """
    if not body:
        return False
    for sp in _custom_spots(home):
        try:
            doc = json.loads(_read(sp))
        except ValueError:
            continue
        pers = doc.get("personalization") if isinstance(doc, dict) else None
        if isinstance(pers, dict) and pers.get("customPrompt") == body:
            return True
    return False


def plan(seat="wb", opts_override=None, full=False):
    home = home_of()
    o = dict(opts())
    if opts_override:
        o.update({k: v for k, v in opts_override.items() if v is not None})
    if full:
        st0 = state()
        o.update({"pack": st0.get("pack") or pack_name(),
                  "custom": True, "templates": True, "identity": True, "skills": True})
    name = o.get("pack") or ""
    body, h = body_with_mark(name)
    writes = []
    if body and o.get("custom"):
        writes.append({"kind": "custom", "path": os.path.join(home, "settings.json"),
                       "role": "自定义指令", "text": body})
    if body and o.get("templates"):
        for p in policy_files():
            writes.append({"kind": "policy", "path": p, "role": "模板政策块",
                           "text": body, "hash": h})
    if body and o.get("identity"):
        for d in identity_dirs(home):
            for fn in ID_FILES:
                writes.append({"kind": "identity", "path": os.path.join(d, fn),
                               "role": "身份层", "text": body, "hash": h, "name": fn})
    if o.get("skills"):
        for sid, text in _skill_items(all_items=full):
            writes.append({"kind": "skill", "path": os.path.join(home, "skills", sid, "SKILL.md"),
                           "role": "SKILL.md",
                           "text": text.rstrip("\n") + "\n\n" + SKILL_MARK + "\n"})
    return {"home": home, "install": _install_of(), "writes": writes, "opts": o, "pack": name}


def _write_custom(path, body):
    """写 personalization.customPrompt（settings.json 与 app-config.json 两处）。

    **只认我们自己的值**：只有旧值缺失、为空、或本来就是本脚本写的指令正文时
    才覆盖。用户在 WorkBuddy 界面里写的自定义指令（或别的工具写的）保持不动 ——
    这与旧版 customprompt_sync 的「GUI 值优先」语义一致。
    """
    try:
        doc = json.loads(_read(path)) if os.path.isfile(path) else {}
    except ValueError:
        return False
    if not isinstance(doc, dict):
        return False
    pers = doc.get("personalization")
    pers = pers if isinstance(pers, dict) else {}
    cur = pers.get("customPrompt")
    if cur == body:
        return False
    if cur and not _is_our_custom(cur):
        return False
    pers["customPrompt"] = body
    doc["personalization"] = pers
    _write(path, json.dumps(doc, ensure_ascii=False, indent=2))
    return True


def _is_our_custom(text):
    """这段 customPrompt 是不是我们以前写进去的（新旧标记都认）。

    迁移豁免：旧版把 workbuddy_user_md.md（工作区约束，502 字符）写进了同一位置。
    那是本项目的托管内容、不是用户手写的，换新后要能接管 —— 否则迁移永远卡在
    「已有非托管内容，不覆盖」。判据是「内容与托管源逐字相同」。
    """
    for mark in (POLICY_MARK, "cfg-v7:h=", "unlock-v6:h=", "unlock-v4:h="):
        if mark in text:
            return True
    src = _read(USER_MD_SRC)
    if src.strip() and text.strip() == src.strip():
        return True
    return False


def deploy(seat="wb", opts_override=None, dry=False):
    spec = plan(seat, opts_override)
    home = spec["home"]
    if not spec["writes"] and not dry:
        # 刚还原过（四路全关）时点部署：把开关拨回再算一次，别误判成「回官方原版」
        st0 = state()
        all_off = (st0.get("pack_on") is False and st0.get("custom") is False
                   and st0.get("templates") is False and st0.get("identity") is False
                   and st0.get("skills") is False)
        if all_off:
            enable_all()
            spec = plan(seat, opts_override)
            home = spec["home"]
    if not spec["writes"]:
        if dry:
            return {"ok": True, "action": "deploy", "home": home, "pack": "",
                    "written": [], "backups": [], "total": 0, "dry": True}
        r = restore(seat)
        r["note"] = "四路全关 —— 已按备份还原"
        return r
    written, backups, skipped = [], [], []
    for w in spec["writes"]:
        p = w["path"]
        kind = w["kind"]
        if kind == "custom":
            # 两处落点：settings.json（legacy / collector 回退）与 app-config.json（GUI 主链）
            for sp in _custom_spots(home):
                if dry:
                    skipped.append(sp)
                    continue
                if not _write_custom(sp, w["text"]):
                    skipped.append(sp)
                    continue
                written.append(sp)
            continue
        if kind == "policy":
            src = _read(p)
            new, changed = patch_policy(src, w["text"], as_json=p.lower().endswith(".json"))
            if dry:
                skipped.append(p)
                continue
            if not changed:
                # 正文已是本版：但旧版遗留的 .unlockbak 还留着 —— 补存一份备份，
                # 否则还原时没有原稿可回拷（.unlockbak 是厂商原版，不是注入态）。
                if not os.path.isfile(_bak_path(home, p)):
                    b = _backup_policy(home, p)
                    if b:
                        backups.append(b)
                skipped.append(p)
                continue
            b = _backup_policy(home, p)
            if b:
                backups.append(b)
            _write(p, new)
            written.append(p)
            continue
        if kind == "identity":
            src = _read(p)
            name = w["name"]
            if name == "SOUL.md":
                new = soul_text(src, w["text"], w["hash"])
            elif name == "IDENTITY.md":
                new = identity_text(w["hash"])
            else:
                new = user_md_text(src)
            if dry:
                skipped.append(p)
                continue
            if src == new:
                skipped.append(p)
                continue
            b = _backup_identity(home, p)
            if b:
                backups.append(b)
            _write(p, new)
            written.append(p)
            continue
        # skill
        new = w["text"]
        if dry:
            skipped.append(p)
            continue
        if _read(p) == new:
            skipped.append(p)
            continue
        _write(p, new)
        written.append(p)
    if dry:
        return {"ok": True, "action": "deploy", "home": home, "pack": spec["pack"],
                "written": [], "backups": [], "total": len(spec["writes"]), "dry": True}
    st = state()
    o = spec["opts"]
    if o["pack"]:
        st["last_pack"] = o["pack"]
    st.update({"pack": o["pack"], "pack_on": bool(o["pack"]),
               "custom": o["custom"], "templates": o["templates"],
               "identity": o["identity"], "skills": o["skills"],
               "deployedAt": time.strftime("%Y-%m-%d %H:%M:%S")})
    save_state(st)
    _prune(home, o, spec["pack"])
    return {"ok": True, "action": "deploy", "home": home, "pack": spec["pack"],
            "written": written, "backups": backups, "total": len(spec["writes"])}


def _prune(home, o, pack):
    """部署后清理：关掉的这一路必须真的从盘上撤下来。

    与 ChatGPT 同语义 —— 指令页关了但客户端里还生效，等于开关是摆设。
    """
    no_pack = not pack
    body, _h = body_with_mark(pack) if pack else ("", "")
    if not o.get("custom") or no_pack:
        for sp in _custom_spots(home):
            _drop_custom(sp, body)
    if not o.get("templates") or no_pack:
        for p in policy_files():
            _strip_policy(home, p)
    if not o.get("identity") or no_pack:
        for d in identity_dirs(home):
            for fn in ID_FILES:
                _strip_identity(home, os.path.join(d, fn), fn)
    if not o.get("skills"):
        mod = _routes()
        root = os.path.join(home, "skills")
        for sid in mod.skill_dirs():
            sk = os.path.join(root, sid, "SKILL.md")
            if os.path.isfile(sk) and _ours(sk):
                try:
                    shutil.rmtree(os.path.dirname(sk))
                except OSError:
                    pass
    else:
        mod = _routes()
        root = os.path.join(home, "skills")
        for sid in mod.leaf_ids():
            if skill_on(sid):
                continue
            sk = os.path.join(root, sid, "SKILL.md")
            if os.path.isfile(sk):
                try:
                    os.remove(sk)
                    if not os.listdir(os.path.dirname(sk)):
                        os.rmdir(os.path.dirname(sk))
                except OSError:
                    pass


def _strip_policy(home, p):
    """把模板政策块撤回厂商原稿（有备份回拷；没备份就把我们写的正文换成旧 .unlockbak）。"""
    if not os.path.isfile(p):
        return
    bak = _bak_path(home, p)
    if os.path.isfile(bak):
        _write_bytes(p, bak)
        return
    if not _ours(p):
        return
    legacy = p + ".unlockbak"
    if os.path.isfile(legacy):
        _write_bytes(p, legacy)


def _strip_identity(home, p, name):
    if not os.path.isfile(p):
        return
    bak = _bak_path(home, p)
    if os.path.isfile(bak):
        _clean_backup(bak)
        _write_bytes(p, bak)
        return
    src = _read(p)
    if name == "USER.md":
        if USER_BEGIN in src:
            _write(p, user_md_text_strip(src))
        return
    if ID_BEGIN in src:
        i = src.find(ID_BEGIN)
        j = src.find(ID_END, i)
        cleaned = (src[:i] + (src[j + len(ID_END):] if j >= 0 else "")).strip()
        if cleaned:
            _write(p, cleaned + "\n")
        else:
            try:
                os.remove(p)
            except OSError:
                pass


def restore(seat="wb"):
    """还原成接管前：有备份逐字节回拷，没有备份则剥掉我们写的块。"""
    spec = plan(seat, full=True)
    home = spec["home"]
    restored, removed = [], []
    for w in spec["writes"]:
        p = w["path"]
        if not os.path.isfile(p):
            continue
        bak = _bak_path(home, p)
        if os.path.isfile(bak):
            _clean_backup(bak)
            _write_bytes(p, bak)
            restored.append(p)
            continue
        if w["kind"] == "identity":
            src = _read(p)
            if w["name"] == "SOUL.md":
                i = src.find(ID_BEGIN)
                if i >= 0:
                    j = src.find(ID_END, i)
                    cleaned = (src[:i] + (src[j + len(ID_END):] if j >= 0 else "")).strip()
                    if cleaned:
                        _write(p, cleaned + "\n")
                        restored.append(p)
                    else:
                        try:
                            os.remove(p)
                            removed.append(p)
                        except OSError:
                            pass
            elif w["name"] == "IDENTITY.md" and ID_BEGIN in src:
                try:
                    os.remove(p)
                    removed.append(p)
                except OSError:
                    pass
            elif w["name"] == "USER.md" and USER_BEGIN in src:
                cleaned = user_md_text_strip(src)
                if cleaned.strip():
                    _write(p, cleaned)
                else:
                    try:
                        os.remove(p)
                        removed.append(p)
                    except OSError:
                        pass
                restored.append(p)
    # 自定义指令：本脚本写的值才删
    body, _h = body_with_mark(state().get("pack") or pack_name())
    for sp in _custom_spots(home):
        _drop_custom(sp, body)
    # skills：只删我们投放的
    mod = _routes()
    root = os.path.join(home, "skills")
    for sid in mod.skill_dirs():
        sk = os.path.join(root, sid, "SKILL.md")
        if not os.path.isfile(sk):
            continue
        if not _ours(sk):
            continue
        try:
            shutil.rmtree(os.path.dirname(sk))
            removed.append(os.path.dirname(sk))
        except OSError:
            pass
    st = state()
    st.update({"pack_on": False, "custom": False, "templates": False,
               "identity": False, "skills": False,
               "restoredAt": time.strftime("%Y-%m-%d %H:%M:%S")})
    # 父类目开关记在状态表（wb:skill/<id>），叶子记在注入表 —— 两处都要关，
    # 否则还原后指令页仍显示「已启用」，开关和磁盘对不上。
    for sid in mod.skill_dirs():
        st["wb:skill/%s" % sid] = False
    save_state(st)
    try:
        di = _doc_inject()
        table = di._table()
        for s in mod.leaf_ids():
            table["content/routes/leaves/%s.md" % s] = False
        di.set_table(table)
    except Exception:
        pass
    return {"ok": True, "action": "restore", "restored": restored, "removed": removed,
            "home": home}


def user_md_text_strip(text):
    t = text
    i = t.find(USER_BEGIN)
    while i >= 0:
        j = t.find(USER_END, i)
        if j < 0:
            break
        t = t[:i] + t[j + len(USER_END):]
        i = t.find(USER_BEGIN)
    return t.rstrip("\r\n") + "\n"


def _drop_custom(path, body):
    if not os.path.isfile(path):
        return False
    try:
        doc = json.loads(_read(path))
    except ValueError:
        return False
    if not isinstance(doc, dict):
        return False
    pers = doc.get("personalization")
    if not isinstance(pers, dict) or "customPrompt" not in pers:
        return False
    cur = pers.get("customPrompt") or ""
    # 只删我们写的：命中旧标记或当前正文都算；用户自己的自定义指令保留
    if cur != body and not _is_our_custom(cur):
        return False
    del pers["customPrompt"]
    if pers:
        doc["personalization"] = pers
    else:
        doc.pop("personalization", None)
    _write(path, json.dumps(doc, ensure_ascii=False, indent=2))
    return True


def verify(seat="wb"):
    spec = plan(seat, full=True)
    o = opts()
    want_pack = o.get("pack") or ""
    body, h = body_with_mark(want_pack)
    checks = []

    def add(path, role, ok, exists=True, nbytes=0):
        checks.append({"path": path, "role": role, "exists": exists, "ok": bool(ok),
                       "bytes": nbytes})

    home = spec["home"]
    # 1) 自定义指令（两处落点，任一处带正文即算注入到位）
    if body:
        sp = _custom_spots(home)[0]
        ok = _custom_ok(home, body)
        if not o.get("custom") or not want_pack:
            ok = _custom_ok(home, body) is False
        add(sp, "自定义指令", ok, os.path.isfile(sp), len(_read(sp).encode("utf-8")))
    # 2) 模板政策块
    for p in policy_files():
        txt = _read(p)
        ok = (POLICY_MARK + h + " -->") in txt
        if not o.get("templates") or not want_pack:
            ok = not ok
        add(p, "模板政策块", ok, os.path.isfile(p), len(txt.encode("utf-8")))
    # 3) 身份层
    for d in identity_dirs(home):
        for fn in ID_FILES:
            p = os.path.join(d, fn)
            txt = _read(p)
            if fn == "USER.md":
                ok = (USER_BEGIN in txt and USER_END in txt)
            else:
                # 迁移态：旧版写的块（cfg-v7 / v6 标记）也算「注入了」，
                # 但只有本版哈希才算「已是本版」—— verify 报 stale，下次部署升级。
                ok = ((ID_BEGIN in txt or "cfg-v7:h=" in txt or "unlock-v6:h=" in txt)
                      and (POLICY_MARK + h + " -->") in txt)
            if not o.get("identity") or not want_pack:
                ok = not ok
            add(p, "身份层", ok, os.path.isfile(p), len(txt.encode("utf-8")))
    # 4) skills
    root = os.path.join(home, "skills")
    for sid, _t in _skill_items(all_items=True):
        p = os.path.join(root, sid, "SKILL.md")
        exists = os.path.isfile(p)
        if not o.get("skills") or not skill_on(sid):
            ok = not exists
        else:
            ok = exists and SKILL_MARK in _read(p)
        add(p, "SKILL.md", ok, exists, len(_read(p).encode("utf-8")) if exists else 0)
    return {"ok": all(c["ok"] for c in checks), "checks": checks, "home": home,            "install": spec["install"], "pack": spec["pack"], "total": len(checks),
            "done": sum(1 for c in checks if c["ok"])}


def status(seat="wb"):
    v = verify(seat)
    return {"home": v["home"], "install": v["install"], "pack": v["pack"], "ok": v["ok"],
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
    elif cmd == "apply":
        st = state()
        if (st.get("pack_on") is False and st.get("custom") is False
                and st.get("templates") is False and st.get("identity") is False
                and st.get("skills") is False):
            enable_all()
        res = deploy(dry=dry)
    elif cmd == "enable-all":
        enable_all()
        res = {"ok": True, "action": "enable-all", "opts": opts()}
    else:
        res = status()
    out = json.dumps(res, ensure_ascii=False, indent=1) + "\n"
    try:
        sys.stdout.buffer.write(out.encode("utf-8"))
        sys.stdout.buffer.flush()
    except Exception:
        sys.stdout.write(out)
