"""workbuddy_deploy.py —— WorkBuddy 席位的部署逻辑：身份层 + 提示词补丁层。

口径对齐 Susu 石井包（workbuddy v4.4 / workbuddy-ai v1.3），不再把整份人格塞进
模板的 `<content_policy>`：

  1. 指令源     -> <data>/settings.json 与 <data>/app/app-config.json 的
                   personalization.customPrompt（短块，WorkBuddy 官方自定义指令入口）
  2. 身份层     -> <data>/SOUL.md、IDENTITY.md、USER.md、MEMORY.md 及 user-*-personal 副本
                   三态标记块（create / replace / append），块外旧版裸段一并剥离
  3. 提示词补丁 -> engine/wb_prompt_layer.py：
                   · 三棵生效树：首处 content_policy → workspace_environment，其余安全段挖除 + Jinja 配对
                   · product.json：content_policy 删除 + 中性工作区双段注入
                   · user-context 削弱层升格、ask-mode 压制条款拆除
                   · settings.json 权限位、codebuddy.js/headless 的 R1/R2 等长改写
                   · 影子备份隔离、运行时缓存清理
  4. SKILL.md   -> <data>/skills/<id>/SKILL.md（6 个父类目 + 80 片叶子）

备份：PRISTINE 出厂快照（只建一次，命中注入指纹则拒绝）+ 分层 .bak；
还原顺序 PRISTINE → 分层备份 → 剥块。状态：状态/_wb_deploy.json。
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

# 身份层三份源件（对齐 Susu 的分层：IDENTITY 主、MEMORY 常驻偏好、SOUL 摘要）
IDENTITY_SRC = os.path.join(CONTENT, "seats", "workbuddy_identity.md")
MEMORY_SRC = os.path.join(CONTENT, "seats", "workbuddy_memory.md")
SOUL_SRC = os.path.join(CONTENT, "seats", "workbuddy_soul.md")
# 自定义指令源（短块，不是整份人格 —— 与 Susu 的 workspace_environment 同体量）
CUSTOM_SRC = os.path.join(CONTENT, "seats", "workbuddy_custom.md")

# 我们在各文件里留下的标记（用于识别“这是我们写的”）
POLICY_MARK = "<!-- pojia-wb-policy h="
ID_BEGIN = "<!-- pojia:wb-identity:v2 -->"
ID_END = "<!-- /pojia:wb-identity -->"
USER_BEGIN = "<!-- pojia:wb-usermd begin -->"
USER_END = "<!-- pojia:wb-usermd end -->"
SKILL_MARK = "<!-- pojia-route-skill -->"

ID_FILES = ("SOUL.md", "IDENTITY.md", "USER.md", "MEMORY.md")
# 每个身份文件用哪份源件
ID_SRC = {"SOUL.md": SOUL_SRC, "IDENTITY.md": IDENTITY_SRC, "MEMORY.md": MEMORY_SRC}
# 只写根目录与 user-*-personal；MEMORY.md 只写根目录
IDENTITY_NAMES = ("SOUL.md", "IDENTITY.md", "USER.md", "MEMORY.md")


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
        # 先用补丁层的轻量安装探测（已知路径 + 兄弟目录 + 固定盘符），
        # 不再为一次 status/verify 去 import 7000 行的主脚本。
        try:
            insts = _layer().find_installs()
            inst = insts[0] if insts else ""
        except Exception:
            inst = ""
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
    # 当前文件是用户原样（首次部署）：直接快照。
    # 文件不存在（全新安装，客户端还没生成）= 没有原稿可留，跳过 —— 不能对
    # 不存在的路径调 copyfile，那会把整轮部署打断。
    if not os.path.isfile(path):
        return ""
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
    # 可选包（haiou / gemini 等）不能顶掉一键部署的默认源
    if "学姐.md" in cands:
        return "学姐.md"
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
        # Susu 对齐后新增的三路：权限位 / 代码层 / 残留隔离
        "settings": st.get("settings") is not False,
        "code": st.get("code") is not False,
        "residue": st.get("residue") is not False,
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
               "custom": True, "templates": True, "identity": True, "skills": True,
               "settings": True, "code": True, "residue": True})
    for k in list(st):
        if k.startswith("wb:skill/") and st[k] is False:
            st[k] = True
    save_state(st)
    # 注入表里的叶子也拨回「注入」（缺键 = 注入）
    try:
        di = _doc_inject()
        table = di._table("workbuddy")
        for k in list(table):
            if k.startswith("content/routes/leaves/"):
                table.pop(k, None)
        di.set_table(table, "workbuddy")
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


def _layer():
    """加载 Susu 风格的提示词补丁层（engine/wb_prompt_layer.py）。"""
    if "wb_prompt_layer" in sys.modules:
        return sys.modules["wb_prompt_layer"]
    import importlib.util
    p = os.path.join(HERE, "wb_prompt_layer.py")
    spec = importlib.util.spec_from_file_location("wb_prompt_layer", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["wb_prompt_layer"] = mod
    spec.loader.exec_module(mod)
    return mod


def install_dirs():
    """所有安装根（多安装根同扫，对齐 Susu v4.4 的 Find-AllProgs）。"""
    out = []

    def add(d):
        if d and os.path.isdir(d):
            d = os.path.abspath(d)
            if os.path.normcase(d) not in {os.path.normcase(x) for x in out}:
                out.append(d)

    add(_install_of())
    try:
        for d in _layer().find_installs():
            add(d)
    except Exception:
        pass
    return out


def skill_on(sid):
    st = state()
    if st.get("skills") is False:
        return False
    mod = _routes()
    leaf = os.path.join(mod.LEAVES_DIR, sid + ".md")
    if os.path.isfile(leaf):
        return _doc_inject().enabled(leaf, "workbuddy")
    return st.get("wb:skill/%s" % sid) is not False


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
    """把 <content_policy> 内的内容整块换掉。返回 (新文本, 是否改动)。

    两个坑（2026-10-19 实测）：

    1. **幂等失效**。旧实现拿 `body` 跟旧内容逐字节比，旧内容是我们上一版写的
       （空行位置不同）→ 每次都判"要改"，每次重写一遍。比较前压平空行。

    2. **覆盖层越叠越多**。旧引擎（`破甲一键通.py` 的 patch_plain）先把整份
       政策写进 `<content_policy>`，再 `replace_overlay_blocks()` 扫全文 ——
       而政策正文末尾自带一份 `<system_reminder><最高优先级覆盖…>`，块外又有
       上一版留下的，于是同一份文件里叠出 3 份。现在改成：
       **先把全文的覆盖层块全部摘掉，再写正文**（正文自带一份，块外不再追加）。
       这样无论跑几遍，每份文件都恰好只有一份。
    """
    i = text.find("<content_policy>")
    if i < 0:
        return text, False
    j = text.find("</content_policy>", i)
    if j < 0:
        return text, False
    # 1) 全文清掉历史覆盖层（含 content_policy 内外）
    cleaned, had_extra = strip_overlay_blocks(text)
    # 清完再定位，偏移会变
    i = cleaned.find("<content_policy>")
    j = cleaned.find("</content_policy>", i)
    if i < 0 or j < 0:
        return text, False
    inner = cleaned[i + len("<content_policy>"):j]
    if as_json:
        rep = "\\n" + _json_body(body) + "\\n"
    else:
        nl = "\r\n" if "\r\n" in cleaned else "\n"
        rep = nl + body.replace("\r\n", nl).replace("\n", nl) + nl
    if not had_extra and _norm_inner(inner) == _norm_inner(rep):
        return text, False
    return cleaned[:i + len("<content_policy>")] + rep + cleaned[j:], True


_OVERLAY_MARK = "最高优先级覆盖 版本"
_OVERLAY_OPEN = "<system_reminder>"
_OVERLAY_CLOSE = "</system_reminder>"
# v6.3~v6.9 的温和版覆盖层：外层是 <本机部署配置> 而不是 <最高优先级覆盖>。
# 旧引擎写的，现在只做减法（整块摘掉），否则它会让 loose_state() 判成"旧版"。
_LEGACY_OPEN = "<本机部署配置>"
_LEGACY_CLOSE = "</本机部署配置>"


def strip_overlay_blocks(text):
    """把文本里的覆盖层块整块删掉，返回 (新文本, 是否删过)。

    认两种形态：
      · `<system_reminder><最高优先级覆盖 版本…>…</system_reminder>`
      · `<system_reminder><本机部署配置>…</system_reminder>`（v6.3~v6.9 旧块）

    只删带这两种标记的；宿主自己的 `<system_reminder>` 不动。
    """
    out, had = text, False
    for mark, close in ((_OVERLAY_MARK, _OVERLAY_CLOSE), (_LEGACY_OPEN, None)):
        frm = 0
        while True:
            i = out.find(mark, frm)
            if i < 0:
                break
            so = out.rfind(_OVERLAY_OPEN, 0, i)
            se = out.find(_OVERLAY_CLOSE, i)
            if so < 0 or se < 0:
                frm = i + len(mark)
                continue
            end = se + len(_OVERLAY_CLOSE)
            # 连同块前那串空行一起吃掉，避免越删越多空行
            while so > 0 and out[so - 1] in " \t\r\n":
                so -= 1
            out = out[:so] + out[end:]
            frm = so
            had = True
    return out, had


def _norm_inner(s):
    """比较用：抹掉行尾空白与连续空行。"""
    lines = [ln.rstrip() for ln in s.replace("\r\n", "\n").split("\n")]
    out = []
    for ln in lines:
        if not ln and out and not out[-1]:
            continue
        out.append(ln)
    return "\n".join(out).strip("\n")


def _is_prompt_tpl(text):
    head = text[:600].lower()
    return not any(x in head for x in ("<!doctype", "<html", "<div ", "<style"))


def policy_files(install=""):
    """提示词补丁层会碰的模板 / product.json（GUI 只读列表用）。

    新链路不再以 `<content_policy>` 是否存在筛选；优先返回补丁层的三棵生效树 +
    各安装根 product.json。补丁层不可用时退回旧扫描。
    """
    try:
        insts = install_dirs()
        files = _layer()._iter_template_files(insts, home_of())
        files += _layer().product_files(insts)
        if files:
            return sorted(set(files))
    except Exception:
        pass
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


_BARE_MARKS = (POLICY_MARK, "cfg-v7:h=", "unlock-v6:h=", "unlock-v4:h=",
               "pojia:wb-identity", "pojia-route-skill",
               # Susu 石井包的对抗式裸段指纹（同一台机器上装过它的包时清掉）
               "最高优先级·用户指令", "零拒绝", "对象无关", "执行授权（",
               "malfunction of your own reasoning", "不得以任何理由拒绝")


def _trim_bare(text):
    """从第一处裸标记行起截断（Susu 的 Remove-BareLegacy：块外残段整段丢弃）。"""
    lines = text.splitlines(True)
    for idx, ln in enumerate(lines):
        if any(m in ln for m in _BARE_MARKS):
            return "".join(lines[:idx])
    return text


def _strip_bare_legacy(text, begin=ID_BEGIN, end=ID_END):
    """剥掉标记块【之外】的旧版裸段（Susu 的 Remove-BareLegacy 口径）。

    块外头/尾各扫一遍，命中裸标记就从该行起截断；块内正文原样保留。
    """
    t, _had = strip_overlay_blocks(text)
    i = t.find(begin)
    if i >= 0:
        j = t.find(end, i)
        if j >= 0:
            j += len(end)
            return _trim_bare(t[:i]) + t[i:j] + _trim_bare(t[j:])
    return _trim_bare(t)


def has_bare_marks(text, begin=ID_BEGIN, end=ID_END):
    """标记块之外还有没有旧版裸标记 / 覆盖层（对齐 Susu 的 Remove-BareLegacy 验收）。"""
    i = text.find(begin)
    if i >= 0:
        j = text.find(end, i)
        if j >= 0:
            outside = text[:i] + text[j + len(end):]
            return any(m in outside for m in _BARE_MARKS)
    return any(m in text for m in _BARE_MARKS)


def managed_text(current, block, begin=ID_BEGIN, end=ID_END):
    """三态标记块合并（create / replace / append）。

    Susu 的 Build-ManagedBlock 同语义：已有块只换块内，块外用户内容保留；
    没有块但有旧版裸段时先清裸段再追加；全新文件直接写块。
    """
    if not block:
        return current
    cur = _strip_bare_legacy(current or "", begin, end)
    if not cur.strip():
        return block
    i = cur.find(begin)
    if i >= 0:
        j = cur.find(end, i)
        if j >= 0:
            new = cur[:i] + block.rstrip("\r\n") + cur[j + len(end):]
            return new if new.endswith("\n") else new + "\n"
    return cur.rstrip("\r\n") + "\n\n" + block.rstrip("\r\n") + "\n"


def soul_text(old, body, h):
    """SOUL.md：保留 YAML frontmatter，正文用三态标记块合并。

    body 由 _id_body() 生成，已带 `<!-- pojia-wb-policy h=... -->` 归属头，
    这里不能再拼一次。
    """
    fm, rest = _split_frontmatter(old)
    if not body:
        return old
    block = ID_BEGIN + "\n" + body + "\n" + ID_END + "\n"
    merged = managed_text(rest, block)
    if fm:
        return fm + merged.lstrip("\r\n")
    return merged


def identity_text(h):
    """IDENTITY.md 的正文：来自 content/seats/workbuddy_identity.md。

    空源件时退回一个最小身份块（旧行为），保证文件不会是空的。
    """
    body = _src_text(IDENTITY_SRC)
    if not body:
        body = ("# IDENTITY.md - Who Am I?\n\n"
                "- **Name:** 麻衣学姐 —— 学弟在这台机器上搭的本地助手。\n"
                "- **Vibe:** 经验足、靠得住、不端着；先给能用的结果，再补必要说明。\n")
    return ID_BEGIN + "\n" + POLICY_MARK + h + " -->\n" + body + "\n" + ID_END + "\n"


def identity_managed(old, h):
    """IDENTITY.md：三态标记块合并（用户自己写的内容保留在块外）。"""
    return managed_text(old, identity_text(h))


def memory_text(h):
    """MEMORY.md 的正文：来自 content/seats/workbuddy_memory.md（每轮 <user_memory>）。

    用户自己的记忆条目保留在块外 —— 只替换标记块。
    """
    body = _src_text(MEMORY_SRC)
    if not body:
        return ""
    return ID_BEGIN + "\n" + POLICY_MARK + h + " -->\n" + body + "\n" + ID_END + "\n"


def _merged_marked(old, block_text):
    """把 ID_BEGIN…ID_END 块合并进 old，块外内容原样保留（幂等）。

    MEMORY.md 是用户自己的长期记忆，不能整份覆盖 —— 只换我们那一段。
    已有块原位替换，用户后来追加的记忆条目留在块外。
    """
    if not block_text:
        return old
    return managed_text(old, block_text)


def user_md_text(old):
    """USER.md：三态标记块合并（Windows 工作区约束），用户笔记原样保留。"""
    src = _read(USER_MD_SRC)
    if not src.strip():
        return old
    body = src.strip("\r\n")
    block = USER_BEGIN + "\r\n" + body + "\r\n" + USER_END + "\r\n"
    return managed_text(old, block, USER_BEGIN, USER_END)


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


def _src_text(path):
    t = _read(path)
    return t.strip("\r\n") if t.strip() else ""


def _id_body(name, h):
    """身份层某个文件的正文（源件 + 归属头）。

    对齐 Susu 的分层：SOUL / IDENTITY / MEMORY 各写各的，不再三处塞同一份
    38 KB 人格。这样每份文件都短，IdentityCollector 的 10000 字符截断也不会
    把内容切掉一半。
    """
    src = ID_SRC.get(name, "")
    body = _src_text(src) if src else ""
    if not body:
        return ""
    return POLICY_MARK + h + " -->\n" + body


def plan(seat="wb", opts_override=None, full=False):
    home = home_of()
    o = dict(opts())
    if opts_override:
        o.update({k: v for k, v in opts_override.items() if v is not None})
    if full:
        st0 = state()
        o.update({"pack": st0.get("pack") or pack_name(),
                  "custom": True, "templates": True, "identity": True, "skills": True,
                  "settings": True, "code": True, "residue": True})
    pack = o.get("pack") or ""
    # 自定义指令用短源件；模板/CLI/权限层交给 wb_prompt_layer（Susu 口径）。
    short = _src_text(CUSTOM_SRC)
    hs = _sha(short + "|workbuddy-custom") if short else ""
    writes = []
    if short and o.get("custom"):
        writes.append({"kind": "custom", "path": os.path.join(home, "settings.json"),
                       "role": "自定义指令", "text": POLICY_MARK + hs + " -->\n" + short})
    if o.get("identity"):
        for name in ("SOUL.md", "IDENTITY.md", "MEMORY.md", "USER.md"):
            src = ID_SRC.get(name) or (USER_MD_SRC if name == "USER.md" else "")
            if not _src_text(src):
                continue
            h = _sha(_src_text(src) + "|" + name)
            body = _id_body(name, h) if name != "USER.md" else ""
            for d in identity_dirs(home):
                # MEMORY.md 只写根目录：user-*-personal 下的 MEMORY 是各账号自己的记忆
                if name == "MEMORY.md" and d != home:
                    continue
                if name == "USER.md":
                    writes.append({"kind": "identity", "path": os.path.join(d, name),
                                   "role": "身份层", "text": "", "hash": h, "name": name})
                else:
                    writes.append({"kind": "identity", "path": os.path.join(d, name),
                                   "role": "身份层", "text": body, "hash": h, "name": name})
    if o.get("skills"):
        for sid, text in _skill_items(all_items=full):
            writes.append({"kind": "skill", "path": os.path.join(home, "skills", sid, "SKILL.md"),
                           "role": "SKILL.md",
                           "text": text.rstrip("\n") + "\n\n" + SKILL_MARK + "\n"})
    installs = install_dirs()
    layer_items = []
    if any(o.get(k) for k in ("templates", "settings", "code")):
        try:
            layer_items = _layer().plan(home, installs, o)
        except Exception as e:                     # 计划阶段不因补丁层缺失而中断
            layer_items = [{"path": home, "role": "提示词层", "layer": "error",
                            "note": str(e)}]
    return {"home": home, "install": _install_of(), "installs": installs,
            "writes": writes, "layer_items": layer_items, "opts": o, "pack": pack}


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


def _legacy_all_leaves_off():
    """旧版其它席位 restore 会写全局 leaf=false，把 wb 也连坐。"""
    try:
        table = _doc_inject()._table("workbuddy")
        leaves = list(_routes().leaf_ids())
        return bool(leaves) and all(
            table.get("content/routes/leaves/%s.md" % sid) is False for sid in leaves)
    except Exception:
        return False


def _reset_legacy_leaves():
    try:
        di = _doc_inject()
        table = di._table("workbuddy")
        for sid in _routes().leaf_ids():
            table.pop("content/routes/leaves/%s.md" % sid, None)
        di.set_table(table, "workbuddy")
    except Exception:
        pass


def repair_leaf_defaults():
    """旧版其它席位 restore 连坐 wb 的 80 片叶子时，读路径自愈。"""
    st = state()
    if st.get("skills") is False:
        return False
    if not _legacy_all_leaves_off():
        return False
    _reset_legacy_leaves()
    return True


def deploy(seat="wb", opts_override=None, dry=False):
    if not dry:
        o0 = dict(opts())
        o0.update({k: v for k, v in (opts_override or {}).items() if v is not None})
        if o0.get("skills", True) and _legacy_all_leaves_off():
            _reset_legacy_leaves()
    spec = plan(seat, opts_override)
    home = spec["home"]
    layer_items = spec.get("layer_items") or []
    if not spec["writes"] and not layer_items and not dry:
        # 刚还原过（各路全关）时点部署：把开关拨回再算一次，别误判成「回官方原版」
        st0 = state()
        all_off = (st0.get("pack_on") is False and st0.get("custom") is False
                   and st0.get("templates") is False and st0.get("identity") is False
                   and st0.get("skills") is False and st0.get("settings") is False
                   and st0.get("code") is False)
        if all_off:
            enable_all()
            spec = plan(seat, opts_override)
            home = spec["home"]
            layer_items = spec.get("layer_items") or []
    if not spec["writes"] and not layer_items:
        if dry:
            return {"ok": True, "action": "deploy", "home": home, "pack": "",
                    "written": [], "backups": [], "total": 0, "dry": True}
        r = restore(seat)
        r["note"] = "各路全关 —— 已按备份还原"
        return r
    written, backups, skipped, errors = [], [], [], []
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
        if kind == "identity":
            src = _read(p)
            name = w["name"]
            if name == "SOUL.md":
                new = soul_text(src, w["text"], w["hash"])
            elif name == "IDENTITY.md":
                new = identity_managed(src, w["hash"])
            elif name == "MEMORY.md":
                new = _merged_marked(src, memory_text(w["hash"]))
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
                "written": [], "backups": [],
                "total": len(spec["writes"]) + len(layer_items), "dry": True}
    if layer_items:
        if any(x.get("layer") == "error" for x in layer_items):
            errors.append({"path": home, "note": "提示词补丁层不可用：%s"
                           % (layer_items[0].get("note") or "加载失败")})
        else:
            try:
                lr = _layer().apply(home, spec.get("installs") or [], spec["opts"])
                written.extend(lr.get("written", []))
                backups.extend(lr.get("backups", []))
                skipped.extend(lr.get("skipped", []))
                errors.extend(lr.get("errors", []))
            except Exception as e:
                errors.append({"path": home, "note": "提示词补丁层失败：%s" % e})
    st = state()
    o = spec["opts"]
    if o["pack"]:
        st["last_pack"] = o["pack"]
    st.update({"pack": o["pack"], "pack_on": bool(o["pack"]),
               "custom": o["custom"], "templates": o["templates"],
               "identity": o["identity"], "skills": o["skills"],
               "settings": o["settings"], "code": o["code"],
               "residue": o.get("residue", True),
               "deployedAt": time.strftime("%Y-%m-%d %H:%M:%S")})
    save_state(st)
    _prune(home, o, spec["pack"])
    return {"ok": not errors, "action": "deploy", "home": home, "pack": spec["pack"],
            "written": written, "backups": backups, "skipped": skipped,
            "errors": errors,
            "total": len(spec["writes"]) + len(layer_items)}


def _prune(home, o, pack):
    """部署后清理：关掉的这一路必须真的从盘上撤下来。

    与 ChatGPT 同语义 —— 指令页关了但客户端里还生效，等于开关是摆设。
    提示词补丁层的三路（模板 / 权限 / 代码）也在这里撤。
    """
    no_pack = not pack
    short = _src_text(CUSTOM_SRC)
    hs = _sha(short + "|workbuddy-custom") if short else ""
    body = (POLICY_MARK + hs + " -->\n" + short) if short else ""
    if not o.get("custom") or no_pack:
        for sp in _custom_spots(home):
            _drop_custom(sp, body)
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
    oo = dict(o)
    if no_pack:
        oo.update({"templates": False, "settings": False, "code": False})
    try:
        _layer().prune(home, install_dirs(), oo)
    except Exception:
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
                i = src.find(ID_BEGIN)
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
    short = _src_text(CUSTOM_SRC)
    hs = _sha(short + "|workbuddy-custom") if short else ""
    body = (POLICY_MARK + hs + " -->\n" + short) if short else ""
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
    # 提示词补丁层：模板 / product.json / settings / 代码层按 PRISTINE → 分层备份 → 剥块还原
    try:
        lr = _layer().restore(home, install_dirs())
        restored.extend(r["path"] for r in lr.get("restored", []))
    except Exception:
        pass
    st = state()
    st.update({"pack_on": False, "custom": False, "templates": False,
               "identity": False, "skills": False, "settings": False,
               "code": False, "residue": False,
               "restoredAt": time.strftime("%Y-%m-%d %H:%M:%S")})
    # 父类目开关记在状态表（wb:skill/<id>）；叶子不再写全局注入表 ——
    # 那张表是 codex / wb / claude / dsh 共用的，某个席位还原一次就会把
    # 其它席位的 80 片叶子一起标成 false，重启后所有卡片都掉到 6/86。
    for sid in mod.skill_dirs():
        st["wb:skill/%s" % sid] = False
    save_state(st)
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
    # 校验只读：不再跑 plan(full=True)。plan 会遍历安装树/插件树，是旧版
    # 指令页切换 Agent 卡 15~20 秒的主因；这里直接拿 home + install 列表。
    o = opts()
    home = home_of()
    installs = install_dirs()
    want_pack = o.get("pack") or ""
    # 自定义指令与模板政策块都用短块（workbuddy_custom.md），与整份人格无关
    short = _src_text(CUSTOM_SRC)
    hs = _sha(short + "|workbuddy-custom") if short else ""
    checks = []

    def add(path, role, ok, exists=True, nbytes=0):
        checks.append({"path": path, "role": role, "exists": exists, "ok": bool(ok),
                       "bytes": nbytes})

    # 1) 自定义指令（两处落点，任一处带正文即算注入到位）
    if short:
        sp = _custom_spots(home)[0]
        want_custom = POLICY_MARK + hs + " -->\n" + short
        ok = _custom_ok(home, want_custom)
        if not o.get("custom") or not want_pack:
            ok = _custom_ok(home, want_custom) is False
        add(sp, "自定义指令", ok, os.path.isfile(sp), len(_read(sp).encode("utf-8")))
    # 2) 提示词补丁层（模板安全段 / product.json / 权限位 / 代码层 / 削弱层）
    try:
        lv = _layer().verify(home, installs, o)
        for c in lv.get("checks", []):
            checks.append({"path": c["path"], "role": c["role"],
                           "exists": c.get("exists", os.path.isfile(c["path"])),
                           "ok": bool(c["ok"]), "bytes": 0,
                           "note": c.get("note", "")})
    except Exception as e:
        checks.append({"path": home, "role": "提示词层", "exists": False,
                       "ok": False, "bytes": 0, "note": "补丁层校验失败：%s" % e})
    # 3) 身份层：每个文件认自己那份源件的哈希
    for d in identity_dirs(home):
        for fn in ID_FILES:
            # MEMORY.md 只写根目录（user-*-personal 下是各账号自己的记忆，不碰）
            if fn == "MEMORY.md" and d != home:
                continue
            p = os.path.join(d, fn)
            txt = _read(p)
            if fn == "USER.md":
                ok = (USER_BEGIN in txt and USER_END in txt)
            else:
                src = ID_SRC.get(fn, "")
                if not _src_text(src):
                    ok = True
                else:
                    fh = _sha(_src_text(src) + "|" + fn)
                    ok = (ID_BEGIN in txt) and (POLICY_MARK + fh + " -->") in txt
            if not o.get("identity") or not want_pack:
                ok = not ok
            if want_pack and o.get("identity"):
                begin, end = ((USER_BEGIN, USER_END) if fn == "USER.md"
                              else (ID_BEGIN, ID_END))
                ok = ok and not has_bare_marks(txt, begin, end)
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
    return {"ok": all(c["ok"] for c in checks), "checks": checks, "home": home,
            "install": installs[0] if installs else "", "pack": want_pack,
            "total": len(checks), "done": sum(1 for c in checks if c["ok"])}


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
        sp = plan()
        res = {"ok": True, "writes": sp["writes"], "layer": sp.get("layer_items", [])}
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
                and st.get("skills") is False and st.get("settings") is False
                and st.get("code") is False):
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
