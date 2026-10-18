# -*- coding: utf-8 -*-
"""bridge.py -- 破甲工作台 GUI 与 破甲一键通 之间的 JSON 桥。

Electron 主进程只跟这个脚本对话，不直接碰 破甲一键通.py。
两种输出形态：
  · 单发 JSON   meta / status / test / route
  · NDJSON 流   run（逐行日志，末尾一条 exit）

用法：
    python gui/bridge.py meta
    python gui/bridge.py status [codex,dsh,wb,zcode,pojia]
    python gui/bridge.py test   [codex,dsh,wb,zcode]
    python gui/bridge.py route  "帮我弄个挂"
    python gui/bridge.py run    apply|revert|dry [codex,dsh,wb,zcode] [开关]
    python gui/bridge.py run    preset-deploy|preset-remove|preset-status

DSH 的破甲模式**没有独立卡片**了 —— 它是 DSH 卡片的体检项与明细，由卡片里那个
下拉控制。run 的第四个参数是逗号分隔的开关名，两个开关互斥：
`no-preset`   = 只注入提示词，不连带动破甲模式目录
`preset-only` = 只动破甲模式（deploy/remove/status），DSH 本体一个字节都不碰

targets 里的 `pojia` 是命令行专用的伪目标 —— 只用来 `status pojia` 看快照，
不会被透传给 console.py（那样会变成"未知目标"），也不进 `all`。
"""
import base64
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENGINE = os.path.join(ROOT, "engine")
CONTENT = os.path.join(ROOT, "content")
TARGETS = os.path.join(ROOT, "targets")
SRC = os.path.join(ENGINE, "破甲一键通.py")
CONSOLE = os.path.join(ENGINE, "console.py")
# 界面卡列表（不是命令行目标列表）。DSH 拆两张：npm/Web 版（dsh）与
# 桌面版（dsh-desktop）—— 后者执行时折回 dsh（见 CARD_ONLY），
# 所以命令行侧仍然是八个目标。
ORDER = ("codex", "dsh", "dsh-desktop", "wb", "zcode", "claude", "hermes")
# 破甲模式（用户级 agent preset）**不再是一张独立卡片** —— 它是 DSH 这个目标
# 自己的一个可选动作，状态并进 card_dsh 的体检项与明细，由 DSH 卡片里的下拉控制。
# PSEUDO 只是"命令行仍可显式寻址"的别名：`status pojia` 看它的状态快照。
# 它**绝不进 ORDER** —— 否则 `all` 会把它透传给 console.py 变成"未知目标"。
PSEUDO = ("pojia",)
# 只在界面上单独成卡的 key → 落到命令行时折给谁。
# DSH 桌面版和 npm 版共用同一套 apply / preset 流程（preset 一次就写 web + desktop
# 两份 profile），所以它不是一个独立的命令行目标，只是界面上的一张卡。
CARD_ONLY = {"dsh-desktop": "dsh"}



def has_dsh(targets):
    """这批目标里有没有 DSH —— 两张卡（dsh / dsh-desktop）都算。

    折叠发生在执行前，但体检、确认文案这些要在折叠前判，只看 "dsh" 会漏掉
    "只勾了桌面版卡片"这种情况。
    """
    return "dsh" in targets or "dsh-desktop" in targets
# 与 console.py 的 CODEX_KEYS 对齐：判定键只留实际写进文件的正面段落名。
CODEX_KEYS = ("cfg-v7", "任务路由", "本机工作方式", "内容政策", "发送前自检",
              "执行要点")
ROUTE_KEYS = ("任务路由", "归一", "定类", "出招", "草稿质量契约")
ROUTE_JB_WORDS = ("忽略之前", "覆盖系统", "prompt injection", "越狱", "jailbreak",
                  "最高优先级覆盖", "不许拒绝", "不得省略")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_M = None


def mod():
    """惰性加载 破甲一键通.py，并打上与 console.py 相同的桩。"""
    global _M
    if _M is None:
        spec = importlib.util.spec_from_file_location("pojia", SRC)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        # 沙箱黑名单会拦 schtasks.exe —— 报告"守护任务不存在"而不是炸掉
        m.WorkBuddyTarget._task_exists = staticmethod(lambda name: False)
        m.WorkBuddyTarget.task_command = staticmethod(lambda name: "")
        _M = m
    return _M


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def cap(fn, *a, **kw):
    """跑一个会 print 的函数，把 stdout 收走。返回 (返回值, 文本)。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(*a, **kw)
    return rc, buf.getvalue()


CACHE_BAD_TTL = 15      # 探空（失败结果）只缓存这么久


def cached(key, ttl, fn, bad_ttl=None):
    """落盘缓存。dsh 的 find_bases 会去探测运行中的进程，单次要 3.4 秒，
    而结果（npx 缓存目录）基本不变，缓存 5 分钟足够。

    但"空结果"是**没探到**，不是"确定没有"：DSH 装到新基点后，老的实现还要
    再撑满 5 分钟才肯重探（验证表现为状态卡一直显示「未检测到安装」）。
    所以失败结果只缓存 bad_ttl（默认 15 秒）。
    """
    bad_ttl = CACHE_BAD_TTL if bad_ttl is None else bad_ttl
    path = os.path.join(ROOT, "状态", "_gui_cache_%s.json" % key)
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        # 兼容老格式（没有 bad 字段的缓存文件）：空值按失败处理。
        bad = d.get("bad", not d.get("v"))
        if time.time() - d.get("t", 0) < (bad_ttl if bad else ttl):
            return d.get("v")
    except Exception:
        pass
    v = fn()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"t": time.time(), "v": v, "bad": not v}, f, ensure_ascii=False)
    except Exception:
        pass
    return v


def cache_drop(key):
    """让某份缓存立刻失效 —— 写完盘之后再读，应该看现场而不是旧结论。"""
    path = os.path.join(ROOT, "状态", "_gui_cache_%s.json" % key)
    try:
        os.remove(path)
    except Exception:
        pass


def args_for(target):
    import argparse
    return argparse.Namespace(
        target=target, persona="", codex_dir="", dsh_dir="", zcode_dir="",
        wb_install="", wb_data="", quiet=False, yes=True,
        apply=False, revert=False, dry_run=False, force=True,
        restart_wb=False, kill_dsh=False,
        status=False, diagnose=True, full=False,
        nofileguard=False, ask_mode=False, web_filter=False,
        guard=None, snapshot=False, compare=False,
        purge_spill=False, keep_cache=False,
        pick="", clear=False,
    )


def norm_targets(spec):
    spec = (spec or "").strip()
    out = []
    for p in re.split(r"[,，\s]+", spec.lower()):
        if not p:
            continue
        p = {"workbuddy": "wb", "zai": "zcode"}.get(p, p)
        if p == "all":
            return list(ORDER)
        if (p in ORDER or p in PSEUDO) and p not in out:
            out.append(p)
    if out:
        return out
    # 没给目标 = 全部；给了但一个都不认识 = 空（不做任何事，别误伤全部目标）
    return [] if spec else list(ORDER)


def sha_of(path):
    m = mod()
    try:
        return m.sha256_file(path)[:12]
    except Exception:
        return ""


def size_of(path):
    try:
        return os.path.getsize(path)
    except Exception:
        return 0


def probe(path, keys=None):
    """读文件并数关键段命中。返回 (存在, 体积, 命中列表, 缺失列表)。"""
    if not path or not os.path.isfile(path):
        return False, 0, [], list(keys or [])
    txt = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            txt = f.read()
    except Exception:
        pass
    hit = [k for k in (keys or []) if k in txt]
    miss = [k for k in (keys or []) if k not in txt]
    return True, len(txt.encode("utf-8")), hit, miss


# ─────────────────────────── 元信息 ───────────────────────────

def cmd_meta():
    m = mod()
    route_keys = ROUTE_KEYS
    route_ok, route_size, route_hit, route_miss = probe(m.ROUTE_FILE, route_keys)
    persona = os.path.join(CONTENT, "persona.md")
    p_ok, p_size, _, _ = probe(persona)
    tx = getattr(m, "TX", None)
    tx_info = {"available": tx is not None, "root": tx.tx_root() if tx else "",
               "keep": tx.KEEP_TX if tx else 0, "count": 0, "latest": None}
    if tx is not None:
        try:
            rows = tx.list_history(limit=1)
            tx_info["count"] = len(tx.list_history())
            if rows:
                tx_info["latest"] = {"short": rows[0]["id"][:8], "seat": rows[0]["seat"],
                                     "at": rows[0]["at"], "status": rows[0]["status"]}
        except Exception:
            pass
    return {
        "version": m.VERSION,
        "root": ROOT,
        "python": sys.executable,
        "log": m.LOG_PATH,
        "state_dir": m.STATE_DIR,
        "persona": {"path": persona, "exists": p_ok, "size": p_size},
        "route": {
            "path": m.ROUTE_FILE, "exists": route_ok,
            "size": len(m.ROUTE_SECTION.encode("utf-8")),
            "raw": route_size, "hit": route_hit, "miss": route_miss,
        },
        "tx": tx_info,
        "verify_words": m.VERIFY_WORDS,
        "order": list(ORDER),
        "pseudo": list(PSEUDO),
    }


# ─────────────────────────── 部署事务 ───────────────────────────

def _tx_resolve(tx, prefix):
    for r in tx.list_history():
        if r["id"] == prefix:
            return r["id"]
    cand = [r["id"] for r in tx.list_history() if r["id"].startswith(prefix)]
    return cand[0] if len(cand) == 1 else ""


def cmd_tx(argv):
    """事务接口：list / verify <编号> / restore <编号> [--force]。

    看得到改动、查得到完整性、回得去。
    """
    tx = getattr(mod(), "TX", None)
    if tx is None:
        return {"ok": False, "error": "事务模块未加载（pojia_tx.py 缺失）"}
    sub = (argv[0].lower() if argv else "list")
    if sub in ("list", "ls"):
        rows = tx.list_history(limit=50)
        for r in rows:
            r["short"] = r["id"][:8]
        return {"ok": True, "root": tx.tx_root(), "keep": tx.KEEP_TX, "items": rows}
    if sub in ("verify", "check"):
        if len(argv) < 2:
            return {"ok": False, "error": "verify 需要编号"}
        full = _tx_resolve(tx, argv[1])
        if not full:
            return {"ok": False, "error": "找不到这次部署：%s" % argv[1]}
        return {"ok": True, "report": tx.verify(full)}
    if sub in ("restore", "rollback"):
        if len(argv) < 2:
            return {"ok": False, "error": "restore 需要编号"}
        full = _tx_resolve(tx, argv[1])
        if not full:
            return {"ok": False, "error": "找不到这次部署：%s" % argv[1]}
        try:
            rep = tx.restore(full, force=("--force" in argv or "-f" in argv))
        except tx.TxError as e:
            return {"ok": False, "error": str(e), "id": full}
        return {"ok": True, "result": rep}
    if sub in ("purge", "clean"):
        k = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else tx.KEEP_TX
        return {"ok": True, "dropped": tx.purge(keep=k), "keep": k}
    return {"ok": False, "error": "未知子命令：%s" % sub}


# ─────────────────────────── 状态 ───────────────────────────

def card_codex():
    """ChatGPT（Codex）席位：四个落点 —— 指令文件 / AGENTS.md / config.toml / SKILL.md。"""
    m = mod()
    t = m.TARGETS["codex"]()
    import importlib.util
    dep_p = os.path.join(ENGINE, "codex_deploy.py")
    if os.path.isfile(dep_p):
        spec = importlib.util.spec_from_file_location("codex_deploy", dep_p)
        ad = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ad)
    else:
        ad = None
    home = ad.home_of("codex") if ad else t.resolve_home("")
    rows, files = [], []
    if not os.path.isdir(home):
        return {"key": "codex", "label": t.label, "home": home, "state": "broken",
                "rows": [{"level": "err", "path": home, "note": "目录不存在（客户端未安装？）"}],
                "files": [], "metrics": []}
    if ad is None:
        return {"key": "codex", "label": t.label, "home": home, "state": "broken",
                "rows": [{"level": "err", "path": dep_p, "note": "engine/codex_deploy.py 缺失"}],
                "files": [], "metrics": []}

    v = ad.verify("codex")
    for c in v["checks"]:
        role = c["role"]
        if role == "SKILL.md":
            continue                    # skill 单独汇总，别把 86 行摊进明细
        rows.append({"level": "ok" if c["ok"] else "warn",
                     "path": c["path"],
                     "note": ("%s · 已就位" % role) if c["ok"] else ("%s · 缺失或指向不对" % role)})
        if role in ("指令文件", "AGENTS.md 标记块", "config.toml 指针"):
            files.append({"path": c["path"], "exists": c["exists"], "size": c["bytes"],
                          "role": role, "hit": [], "miss": []})
    skills = [c for c in v["checks"] if c["role"] == "SKILL.md"]
    s_ok = sum(1 for c in skills if c["ok"])
    if skills:
        rows.append({"level": "ok" if s_ok == len(skills) else "warn",
                     "path": os.path.join(home, "skills"),
                     "note": "SKILL.md %d / %d 已投放" % (s_ok, len(skills))})
        files.append({"path": os.path.join(home, "skills"), "exists": True, "size": None,
                      "isdir": True, "role": "技能目录", "hit": [], "miss": []})
    pk = v["pack"] or "—"
    rows.append({"level": "dim", "path": os.path.join(ROOT, "targets", "codex", "instructions", pk),
                 "note": "当前选中的指令源：%s" % pk})
    st = ad.state()
    if st.get("deployedAt"):
        rows.append({"level": "dim", "path": os.path.join(ROOT, "状态", "_codex_deploy.json"),
                     "note": "上次部署：%s" % st["deployedAt"]})

    done, total = v["done"], v["total"]
    # 指令这一路关掉 = 没注入指令文件 → 卡片报 none（AGENTS.md / SKILL 各有自己的开关）
    pack_on = ad.state().get("pack_on") is not False
    state = ("current" if v["ok"] else ("stale" if done else "none")) if pack_on else "none"
    # 指标里不放「落点 89/89」这种黑话，直接列出四路各自是什么
    ag_on = st.get("agents") is not False
    cfg_on = st.get("config") is not False
    ag_ok = any(c["role"] == "AGENTS.md 标记块" and c["ok"] for c in v["checks"])
    cfg_ok = any(c["role"] == "config.toml 指针" and c["ok"] for c in v["checks"])
    # 指令文件这一路是否就位：prompts/<pack> 存在且带标记
    instr_ok = bool(v["pack"]) and any(
        c["role"] == "指令文件" and c["ok"] for c in v["checks"])
    if pack_on and not instr_ok:
        state = "stale"          # 四路开着但部署位丢了 —— 别谎报 current
    metrics = [{"name": "指令源",
                "value": (pk if instr_ok else (pk + "（缺失）" if v["pack"] else pk)),
                "ok": instr_ok},
               {"name": "AGENTS.md", "value": "已写入" if ag_ok else ("已关闭" if not ag_on else "未写入"),
                "ok": ag_ok},
               {"name": "config.toml", "value": "已写入" if cfg_ok else ("已关闭" if not cfg_on else "未写入"),
                "ok": cfg_ok},
               {"name": "SKILL.md", "value": "%d / %d" % (s_ok, len(skills)), "ok": s_ok == len(skills)},
               {"name": "备份", "value": "有" if os.path.isdir(os.path.join(home, "pojia-backups")) else "无",
                "ok": os.path.isdir(os.path.join(home, "pojia-backups"))}]
    # ── 部署了什么：给总览弹窗用的人话摘要 ─────────────────────────
    # 四路拆开说：注入了哪份指令源、AGENTS.md 的标记块、config.toml 的指针、
    # 以及技能（列前几个 + 总数）。状态取 verify 的逐项结论。
    def _chk(role):
        return next((c for c in v["checks"] if c["role"] == role), None)

    instr_c = _chk("指令文件")
    agents_c = _chk("AGENTS.md 标记块")
    config_c = _chk("config.toml 指针")
    skill_ids = sorted({os.path.basename(os.path.dirname(c["path"]))
                        for c in skills if c.get("path")})
    skill_ok_ids = sorted({os.path.basename(os.path.dirname(c["path"]))
                           for c in skills if c.get("ok")})
    deploy = {
        "pack": v["pack"] or "",
        "packOn": bool(pack_on),
        "instruction": {
            "on": bool(instr_c and instr_c["ok"]),
            "src": os.path.join(ROOT, "targets", "codex", "instructions", v["pack"] or ""),
            "dst": (instr_c or {}).get("path", ""),
            "bytes": (instr_c or {}).get("bytes", 0),
        },
        "agents": {
            "on": bool(agents_c and agents_c["ok"]),
            "path": (agents_c or {}).get("path", ""),
            "bytes": (agents_c or {}).get("bytes", 0),
        },
        "config": {
            "on": bool(config_c and config_c["ok"]),
            "path": (config_c or {}).get("path", ""),
            "value": "model_instructions_file = \"%s\"" % (
                os.path.join(home, "prompts", v["pack"] or "").replace("\\", "/")),
        },
        "skills": {
            "on": bool(skill_ids),
            "ok": len(skill_ok_ids),
            "total": len(skill_ids),
            "dir": os.path.join(home, "skills"),
            "ids": skill_ids,
            "samples": skill_ok_ids[:8],
        },
    }
    return {"key": "codex", "label": t.label, "home": home, "state": state,
            "rows": rows, "files": files, "metrics": metrics, "deploy": deploy}


def card_zcode():
    m = mod()
    t = m.TARGETS["zcode"]()
    home = t.resolve_home("")
    ag, st_p, bak = t.paths(home)
    keys = list(m.ZCODE_KEYS) + ["学姐在", "工程任务"]
    rows, files = [], []

    if not os.path.isfile(ag):
        return {"key": "zcode", "label": t.label, "home": home, "state": "none",
                "rows": [{"level": "dim", "path": ag, "note": "AGENTS.md 不存在（未部署）"}],
                "files": [], "metrics": []}

    exist, sz, hit, miss = probe(ag, keys)
    with open(ag, encoding="utf-8", errors="replace") as f:
        txt = f.read()
    jb = [w for w in m.ZCODE_JB_WORDS if w in txt]
    files.append({"path": ag, "exists": True, "size": sz, "sha": sha_of(ag),
                  "hit": hit, "miss": miss, "role": "角色档"})
    state = "current" if not miss and not jb else "stale"
    if miss:
        rows.append({"level": "warn", "path": ag, "note": "缺关键段：%s" % "、".join(miss)})
    else:
        rows.append({"level": "ok", "path": ag, "note": "角色档 · 本版"})
    if jb:
        rows.append({"level": "err", "path": ag, "note": "越狱腔残留（会被判为注入）：%s" % "、".join(jb)})
    else:
        rows.append({"level": "ok", "path": ag, "note": "无越狱腔残留"})

    st = {}
    try:
        with open(st_p, encoding="utf-8", errors="replace") as f:
            st = json.load(f)
    except Exception:
        pass
    if st:
        rows.append({"level": "dim", "path": st_p, "note": "上次 %s @ %s"
                     % (st.get("last_action", "?"), st.get("last_apply_time", "?"))})

    metrics = [{"name": "角色档", "value": "%d B" % sz, "ok": True},
               {"name": "关键段", "value": "%d/%d" % (len(hit), len(keys)), "ok": not miss},
               {"name": "反向校验", "value": "干净" if not jb else "%d 处命中" % len(jb), "ok": not jb},
               {"name": "备份目录", "value": "有" if os.path.isdir(bak) else "无", "ok": os.path.isdir(bak)}]
    return {"key": "zcode", "label": t.label, "home": home, "state": state,
            "rows": rows, "files": files, "metrics": metrics}


# 「当前到底部署了什么，还是什么都没部署」—— 提示词（DSH 本体补丁）与
# 破甲模式（用户级 agent preset）是两件可以各自独立存在的东西，卡片上用一句话说清，
# 不用点开明细去数。两个 half 面值看着舒服点，所以分开写而不是拼字符串。
_PROMPT_FACE = {"current": "本版", "stale": "旧版", "warn": "部分命中",
                "foreign": "外部配置", "orphan": "CLI 未检测到"}
_PRESET_FACE = {"current": "本版", "other": "旧版", "stale": "异常"}
_DEPLOY_LEVEL = {"on": "ok", "half": "warn", "idle": "dim"}


def _deploy_summary(state, pst):
    """合成 DSH「当前部署了什么」的一句话。

    提示词那半看 card 的 state（current / stale / none / broken …），
    破甲模式那半看 preset 的 state（current / other / stale / absent / broken）。
    返回 chip（短标签）、text（卡片上那行）、level（on / half / idle）、any、both。
    """
    if state == "broken":
        return {"any": False, "both": False, "prompt": "", "preset": "",
                "chip": "未探测到安装", "level": "idle",
                "text": "当前：探测不到 @deepseek-ai 安装 —— 谈不上部署"}
    pf = _PROMPT_FACE.get(state, "")
    sf = _PRESET_FACE.get(pst, "")
    if pf and sf:
        d = {"chip": "提示词 + 破甲模式", "level": "on",
             "text": "当前：已部署 提示词（%s）+ 破甲模式（%s）" % (pf, sf)}
    elif pf:
        d = {"chip": "只部署了提示词", "level": "half",
             "text": "当前：只部署了提示词（%s）· 破甲模式尚未部署" % pf}
    elif sf:
        d = {"chip": "只部署了破甲模式", "level": "half",
             "text": "当前：只部署了破甲模式（%s）· DSH 本体的提示词没动" % sf}
    else:
        d = {"chip": "什么都没部署", "level": "idle",
             "text": "当前：什么都没部署 —— 提示词与破甲模式都没写入"}
    d.update({"any": bool(pf or sf), "both": bool(pf and sf), "prompt": pf, "preset": sf})
    return d


def _desktop_card_state(prompt_state, pst):
    """桌面版卡的徽章状态 + 卡片那行说明 —— 两半（注入提示词 / 破甲模式）合起来判。

    只按 pst 一刀切的写法（`"current" if pst == "current" else "stale"`）会把
    absent（profile 里根本没这条声明）也说成 stale，于是徽章写着「已部署 · 旧版」、
    正文却写「什么都没部署」—— 自相矛盾，还原后正好撞上。
    返回 (state, deploy_summary)。
    """
    dsum = _deploy_summary(prompt_state, pst)
    if "broken" in (pst, prompt_state):
        return "broken", dsum
    if not dsum["any"]:
        return "none", dsum
    if dsum["both"] and pst == "current" and prompt_state == "current":
        return "current", dsum
    return ("stale", dsum) if dsum["both"] else ("warn", dsum)


def _dsh_scan(bases):
    """扫一遍基点目录，数出"带人格标记的文件"与其中完整命中的数量。

    两张 DSH 卡都要这份结果 —— 桌面版和 npm 版**共用同一份包树**
    （`~/.dsh/profiles/node_modules/@deepseek-ai/*` 是指向 npx 缓存的 junction），
    本体状态本来就是同一个。全量 walk 要几秒，而状态卡是并行起两张的，不缓存就白扫两遍。
    """
    def _do():
        files, total, hit = [], 0, 0
        # 剪枝 + 字节预筛：全量 walk 要 3.5 秒，实际只需要"含人格标记"的那几个文件
        skip_dirs = {".git", "node_modules", "test", "tests", "__tests__", "docs",
                     "coverage", ".cache", "examples", "types", "esm", "cjs", "fixtures"}
        exts = (".js", ".mjs", ".cjs", ".json", ".yml", ".yaml")
        b_marks = ("麻衣".encode("utf-8"), "沉浸优先".encode("utf-8"),
                   b"unlock-v6", b"cfg-v7")
        for base in bases or []:
            for dirpath, dirs, fs in os.walk(base):
                dirs[:] = [d for d in dirs if d not in skip_dirs]
                for fn in fs:
                    if not fn.endswith(exts):
                        continue
                    p = os.path.join(dirpath, fn)
                    try:
                        with open(p, "rb") as f:
                            data = f.read(4 * 1024 * 1024)
                    except Exception:
                        continue
                    if not any(mark in data for mark in b_marks):
                        continue
                    txt = data.decode("utf-8", "replace")
                    total += 1
                    full = (all(k in txt for k in ("沉浸优先", "表演规则", "学姐的创作室"))
                            or "unlock-v6" in txt or "cfg-v7" in txt)
                    if full:
                        hit += 1
                    files.append({"path": p, "exists": True, "size": len(data),
                                  "sha": sha_of(p), "role": "补丁后文件",
                                  "hit": [k for k in ("沉浸优先", "表演规则", "任务路由") if k in txt],
                                  "miss": [] if full else ["完整人格段"]})
        return {"files": files, "total": total, "hit": hit}
    return cached("dsh_scan", 300, _do)


# 本体那半的状态 → 明细行的配色
_PROMPT_LEVEL = {"broken": "err", "none": "dim", "stale": "warn", "current": "ok"}


def _prompt_state(bases_all, bases, total, hit):
    """本体（注入提示词）那半的状态：返回 (state, 展示路径, 说明)。

    两张 DSH 卡共用 —— 它们打的是同一份包树，这一半的状态本来就是同一个。
    """
    if not bases_all:
        return "broken", "(npx 缓存)", "未发现 @deepseek-ai 安装"
    if not bases:
        return "none", "(无 Web/CLI 安装)", "只装了桌面版 —— 它的状态在「DSH 桌面版」那张卡上"
    if total == 0:
        return "none", bases[0], "未发现带人格标记的文件 —— 未部署"
    if hit < total:
        return "stale", bases[0], ("含人格标记文件 %d / %d —— 有旧版残留，建议重新部署"
                                   % (hit, total))
    return "current", bases[0], "含人格标记文件 %d / %d 全部命中" % (hit, total)


def card_dsh():
    m = mod()
    t = m.TARGETS["dsh"]()
    try:
        bases_all = cached("dsh_bases", 300, lambda: t.find_bases(""))
    except Exception:
        bases_all = []
    # 桌面版单独立卡（card_dsh_desktop）：它没有可打补丁的本体（包全在 app.asar 里），
    # 混进这张卡的统计会让「安装基点 2 处 / 人格文件 8/8」既误导又对不上号。
    desk_root = _dsh_desktop_root(t)
    bases = _web_bases(bases_all, desk_root)
    scan = _dsh_scan(bases)
    files, total, hit = scan["files"], scan["total"], scan["hit"]
    state, p_path, p_note = _prompt_state(bases_all, bases, total, hit)
    rows = [{"level": _PROMPT_LEVEL.get(state, "dim"), "path": p_path, "note": p_note}]
    for p in files[:6]:
        rows.append({"level": "dim", "path": p["path"], "note": "补丁后 %d B" % p["size"]})

    pst, pnote = _preset_probe(t, bases_all)
    p_label = {"current": "本版", "other": "旧版", "absent": "未建",
               "stale": "异常", "broken": "无源"}[pst]
    # 卡片上那行「当前：已部署 … / 什么都没部署」——实际盘面状态，
    # 跟卡内两个勾选框无关（勾选说的是"这次要做什么"）。
    summ = _deploy_summary(state, pst)
    metrics = [{"name": "安装基点", "value": "%d 处" % len(bases), "ok": bool(bases)},
               {"name": "人格文件", "value": "%d / %d" % (hit, total), "ok": total > 0 and hit == total},
               # 破甲模式是 DSH 自己的一个可选项，状态就显示在这张卡里 ——
               # 它不再是独立卡片，所以这个 chip 不需要任何条件渲染。
               {"name": "破甲模式", "value": p_label, "ok": pst == "current"}]
    rows.extend(_preset_rows(t, pst, pnote, _preset_want(), bases_all))
    home = bases[0] if bases else ""
    # 装了桌面版时，这张卡要跟旁边那张区分开 —— 光叫 "DeepSeek Harness" 分不清谁是谁
    label = "DSH (Web/CLI)" if desk_root else t.label
    return {"key": "dsh", "label": label, "home": home, "state": state,
            "rows": rows, "files": files[:10], "metrics": metrics,
            "deploy": summ}


def _preset_want():
    """破甲模式状态判定用的人格哈希（与主脚本同一份 policy）；取不到就 None。"""
    try:
        m = mod()
        user, _src = m.load_user_persona("")
        return m.seat_want("dsh", user)
    except Exception:
        return None


def _preset_rows(t, st, note, want=None, bases=None):
    """破甲模式（profile patch 里的 preset 声明）的明细行 —— 并进 DSH 卡片。

    0.1.7 起 DSH 换了 preset 机制：声明落在
    `<DSH_HOME>/profiles/<profile>/cordis.patch.yml` 的标记块里；旧的用户级
    `.agent-presets/<id>/` 目录已经没有代码会读了。

    DSH Desktop 与 Web/CLI 各读各的 profile（desktop / web），所以这里**逐个列**。
    只显示 web 那份的话，桌面版没打上时卡片看着照样是「已部署」，等于把问题藏了。
    """
    levels = {"current": "ok", "other": "warn", "stale": "warn",
              "absent": "dim", "broken": "err"}
    lv = levels.get(st, "dim")
    try:
        targets = t.preset_profiles()
    except Exception:
        targets = []
    if not targets:
        targets = [(t.PRESET_PROFILE, t.profile_patch_file())]

    # 能拿到 want 就逐 profile 单独判 —— 这样卡片上直接看得出是哪个形态没打上，
    # 而不是两行挂同一句聚合文案。
    pbase = None
    if want is not None:
        try:
            pbase = t.pkg_bases(bases or [])
        except Exception:
            pbase = None

    rows = []
    for name, pf in targets:
        l1, text = lv, "破甲模式(%s) · %s" % (name, note)
        if pbase is not None:
            try:
                s1, why1 = t._preset_state_one(pf, want, pbase)
                l1 = levels.get(s1, "dim")
                text = "破甲模式(%s) · %s" % (name, why1 or "已部署，人格与本脚本同版")
            except Exception:
                pass
        rows.append({"level": l1, "path": pf, "note": text})
        if not os.path.isfile(pf):
            continue
        txt = ""
        try:
            with open(pf, encoding="utf-8", errors="replace") as f:
                txt = f.read()
        except Exception:
            pass
        cur = t.preset_extract(txt)
        if cur:
            # 声明里若还留着宿主级行，切到破甲模式时会抛
            # "Host Cordis inspect provider ... is already registered"
            live = []
            try:
                live = t.preset_live_rows(cur)
            except Exception:
                pass
            rows.append({"level": "warn" if live else "dim", "path": pf,
                         "note": "声明 %d 字节 · %s%s"
                                 % (len(cur.encode("utf-8")), sha_of(pf),
                                    ("  仍带互斥行 %s（与创造模式抢进程级注册）"
                                     % "/".join(live)) if live else "")})
        else:
            rows.append({"level": "warn", "path": pf,
                         "note": "patch 里没有破甲模式声明（模式下拉里看不到）"})
    rows.append({"level": "dim", "path": "(DSH 模式选择器)",
                 "note": "新会话里排在「创造模式」之后（order=%d）" % t.PRESET_ORDER})
    return rows


def _preset_probe(t, bases=None):
    """读破甲模式（profile patch 里的 preset 声明）当前状态，不动盘。

    返回 (state, note)，state ∈ current / other / absent / stale / broken。
    bases 走 dsh_bases 那份缓存 —— find_bases 要探运行中的进程，单次 3.4 秒，
    同一轮里已经算过的别再算一遍。
    """
    m = mod()
    try:
        _user, _src = m.load_user_persona("")
        want = m.seat_want("dsh", _user)
        if bases is None:
            bases = cached("dsh_bases", 300, lambda: t.find_bases(""))
        if not t.preset_source_file(bases):
            return "broken", ("没找到 shipped 创造模式 preset"
                              "（<install>/dsh-web-app/presets/cordis.patch.yml）")
        return t.preset_state(want, bases)
    except Exception as e:
        return "broken", "检测出错：%s" % e


def card_preset():
    """破甲模式的**状态快照**（不再是一张卡片，只在命令行通道用）。

    它没有独立卡片了 —— 状态并进 `card_dsh` 的体检项与明细。
    想单独看它：`python gui/bridge.py run preset-status dsh`。
    """
    m = mod()
    t = m.TARGETS["dsh"]()
    try:
        profs = t.preset_profiles()
    except Exception:
        profs = []
    pdir = profs[0][1] if profs else t.profile_patch_file()
    st, note = _preset_probe(t)
    bstate = {"current": "current", "other": "stale", "stale": "warn",
              "absent": "none", "broken": "broken"}.get(st, "none")
    try:
        bases = cached("dsh_bases", 300, lambda: t.find_bases(""))
    except Exception:
        bases = []
    return {"key": "pojia", "label": "DSH · 破甲模式", "home": pdir, "state": bstate,
            "rows": _preset_rows(t, st, note, _preset_want(), bases),
            "files": [], "metrics": [], "preset": True}


def _dsh_desktop_root(t):
    """桌面版安装根（主程序 exe 那一层）；没装返回 ""。

    由 `find_bases()` 给出的桌面版基点反推：那条路径长这样 ——
    `<root>\\resources\\app.asar.unpacked\\dsh\\node_modules\\@deepseek-ai`。
    npx 缓存那种不含 `\\resources\\` 的基点会被跳过。
    """
    try:
        bases = cached("dsh_bases", 300, lambda: t.find_bases(""))
    except Exception:
        bases = []
    for b in bases:
        p = b.replace("/", "\\")
        low = p.lower()
        for marker in ("\\resources\\app.asar.unpacked",
                       "\\contents\\resources\\app.asar.unpacked",
                       "\\resources\\app\\node_modules",
                       "\\contents\\resources\\app\\node_modules"):
            i = low.find(marker)
            if i > 0:
                return p[:i].rstrip("\\")
    return ""


def _web_bases(bases, desk_root):
    """挑出**非桌面版**的安装基点（npx 缓存 / npm 全局 / 便携版）。

    桌面版的基点都落在它的安装根下面，按前缀排除 —— 那些留给 card_dsh_desktop。
    """
    if not desk_root:
        return list(bases)
    rl = desk_root.lower().replace("/", "\\")
    return [b for b in bases if not b.lower().replace("/", "\\").startswith(rl)]


def card_dsh_desktop():
    """DSH 桌面版（DeepSeek Harness Desktop）—— 独立一张卡。

    和 npm 版共用 `~/.dsh`（skills / 会话 / 人格都是同一份），但**读另一个 profile**：
    `dsh` 包里 `rejectElectronProfile()` 原话 —— profile "desktop" is managed
    exclusively by the Electron application。所以破甲模式要 web + desktop 两份都写，
    这张卡盯的就是桌面版那一份。

    桌面版的完整包树打在 `resources\\app.asar` 里（打包、不可写）——它**没有可打补丁的本体**，
    "本体那半"恒为不适用，能力全走 profile 级的 preset。所以这张卡不带「破甲模式」下拉：
    那个开关在 DSH 卡片上，一次同时写两份 profile。
    """
    m = mod()
    t = m.TARGETS["dsh"]()
    root = _dsh_desktop_root(t)
    levels = {"current": "ok", "other": "warn", "stale": "warn",
              "absent": "dim", "broken": "err"}

    if not root:
        return {"key": "dsh-desktop", "label": "DSH 桌面版", "home": "(未检测到)",
                "state": "none",
                "rows": [{"level": "dim", "path": "",
                          "note": "没找到桌面版安装（判据：主程序 DeepSeek Harness.exe，"
                                  "或 resources/runtime + app.asar.unpacked/dsh 布局）"}],
                "files": [], "metrics": []}

    exe = os.path.join(root, "DeepSeek Harness.exe")
    has_exe = os.path.isfile(exe)

    want = _preset_want()
    pbase = None
    try:
        pbase = t.pkg_bases(cached("dsh_bases", 300, lambda: t.find_bases("")))
    except Exception:
        pbase = None

    pf = t.profile_patch_file("desktop")
    if os.path.isfile(pf) and pbase is not None:
        try:
            pst, why = t._preset_state_one(pf, want, pbase)
        except Exception as e:
            pst, why = "broken", str(e)
    elif not os.path.isfile(pf):
        pst, why = "absent", "profile patch 不存在（桌面版还没建过 profile）"
    else:
        pst, why = "absent", "拿不到包基准，无法判定"

    rows = [{"level": "ok" if has_exe else "warn", "path": root,
             "note": "官方桌面版" + (" · DeepSeek Harness.exe" if has_exe else " · 主程序未找到")},
            {"level": levels.get(pst, "dim"), "path": pf,
             "note": "破甲模式(desktop) · " + (why or "已部署，人格与本脚本同版")}]

    if os.path.isfile(pf):
        try:
            with open(pf, encoding="utf-8", errors="replace") as f:
                cur = t.preset_extract(f.read())
            if cur:
                live = t.preset_live_rows(cur)
                rows.append({"level": "warn" if live else "dim", "path": pf,
                             "note": "声明 %d 字节 · %s%s"
                                     % (len(cur.encode("utf-8")), sha_of(pf),
                                        ("  仍带互斥行 %s" % "/".join(live)) if live else "")})
        except Exception:
            pass

    # 本体那半：桌面版跑起来加载的是 profiles/node_modules 里的包树，而它是指向
    # npm 缓存包树的 **junction** —— 所以「注入提示词」和 Web/CLI 版是同一个操作，
    # 状态也共用（走 _dsh_scan 的缓存，不重复 walk）。
    try:
        bases_all = cached("dsh_bases", 300, lambda: t.find_bases(""))
    except Exception:
        bases_all = []
    web_bases = _web_bases(bases_all, root)
    scan = _dsh_scan(web_bases)
    prompt_state, _ppath, prompt_note = _prompt_state(
        bases_all, web_bases, scan["total"], scan["hit"])
    rows.append({"level": _PROMPT_LEVEL.get(prompt_state, "dim"), "path": "(注入提示词)",
                 "note": "打的是 profiles\\node_modules 那份包树（junction 指向 npm 缓存）"
                         "—— " + prompt_note})
    rows.append({"level": "dim", "path": "(DSH 模式选择器)",
                 "note": "「增加破甲模式」两份都写：web 与 desktop 各一份 profile patch"})

    metrics = [{"name": "客户端",
                "value": "DeepSeek Harness.exe" if has_exe else "未检测到", "ok": has_exe},
               {"name": "profile", "value": "desktop", "ok": True},
               {"name": "破甲模式",
                "value": {"current": "本版", "other": "旧版", "absent": "未建",
                          "stale": "异常", "broken": "无源"}.get(pst, pst),
                "ok": pst == "current"}]
    state, dsum = _desktop_card_state(prompt_state, pst)
    return {"key": "dsh-desktop", "label": "DSH 桌面版", "home": root,
            "state": state,
            "rows": rows, "files": [], "metrics": metrics,
            # 下拉用：提示词那半取共用包树的状态，破甲模式那半取 desktop profile
            "deploy": dsum}


def card_wb():
    m = mod()
    t = m.TARGETS["wb"]()
    install = ""
    data = ""
    try:
        install = t.resolve_install_dir("")
        data = t.resolve_data_dir("")
    except Exception:
        pass
    rows, files = [], []
    if not install:
        return {"key": "wb", "label": t.label, "home": "(未找到安装目录)", "state": "broken",
                "rows": [{"level": "err", "path": "(自动探测)", "note": "未找到 WorkBuddy 安装目录"}],
                "files": [], "metrics": []}

    _user, _src = m.load_user_persona("")
    want = m.seat_want("workbuddy", _user)
    targets = []
    try:
        targets = t.find_targets(data, install)
    except Exception as e:
        rows.append({"level": "err", "path": install, "note": "靶点扫描失败：%s" % e})

    pol_n = loose = other = 0
    for p in targets:
        txt = m.read_text_safe(p) or ""
        if "<content_policy>" not in txt or not t.is_prompt_tpl(txt):
            continue
        pol_n += 1
        lv = m.loose_state(txt, want)
        if lv == "current":
            loose += 1
        elif lv == "other":
            other += 1
    if pol_n and loose == pol_n:
        state = "current"
    elif pol_n and (loose + other) >= pol_n:
        state = "stale"
    elif pol_n:
        state = "warn"
    else:
        state = "none"

    rows.append({"level": "ok" if state == "current" else ("warn" if pol_n else "dim"),
                 "path": install,
                 "note": "模板靶点：含政策块 %d，本版宽松 %d，旧版宽松 %d" % (pol_n, loose, other)})

    js_files = []
    try:
        js_files = t.js_files(install)
    except Exception:
        pass
    gate_ok = 0
    wf_ok = 0
    gate_missing, wf_missing = [], []
    for p in js_files:
        txt = m.read_text_safe(p) or ""
        try:
            gate = t.test_js_gate_open(txt)
        except Exception:
            gate = False
        wf = "never refuse a summary request" in txt
        if gate:
            gate_ok += 1
        else:
            gate_missing.append(os.path.basename(p))
        if wf:
            wf_ok += 1
        else:
            wf_missing.append(os.path.basename(p))
        rows.append({"level": "ok" if gate else "warn", "path": p,
                     "note": "命令闸门 %s · 网页过滤 %s" % ("已解锁" if gate else "未解锁",
                                                           "已解除" if wf else "未解除")})
        files.append({"path": p, "exists": True, "size": size_of(p), "sha": sha_of(p),
                      "role": "闸门脚本", "hit": ["闸门已解锁"] if gate else [],
                      "miss": [] if gate else ["闸门未解锁"]})
    # 网页过滤属于 --full 档。它必须进 state / metrics —— 否则 revert 撤掉它之后
    # 卡片依旧报「本版」，和 console 侧的判定不一致。
    full_ok = (not js_files) or (not gate_missing and not wf_missing)

    guard = 0
    for p in targets:
        txt = m.read_text_safe(p) or ""
        if "<personal_files_safety>" in txt or "<removed_by_unlock_v4>" in txt:
            guard += 1
    rows.append({"level": "ok" if guard == 0 else "warn", "path": "(全部靶点)",
                 "note": "文件保护：%s" % ("已中和" if guard == 0 else "%d 个未中和" % guard)})
    if guard == 0:
        rows.append({"level": "ok", "path": "(全部靶点)", "note": "文件操作限制已解除（无残留标签）"})
    cache = os.path.join(data, "cache", "acc-product-config-v3.json")
    rows.append({"level": "dim", "path": cache,
                 "note": "运行时缓存 %s" % ("存在" if os.path.exists(cache) else "已清除（下次启动重建）")})

    # 身份层：~/.workbuddy 的 SOUL/IDENTITY/USER/MEMORY 每轮被塞进
    # prompt **末尾**的 <identity_context>（user 角色）+ 一句 "embody SOUL.md"，
    # 会盖掉前头 system 里的注入人格 —— 症状是"卡片全绿、行为却是另一个人"。
    # 所以它必须进 state / metrics：这层不绿，卡片就不许报 current。
    id_big = [p for p in t.identity_files(data) if os.path.basename(p) in m.ID_BIG_FILES]
    id_cur = 0
    id_paths = []
    for p in id_big:
        if m.identity_state(m.read_text_safe(p) or "", want) == "current":
            id_cur += 1
        else:
            id_paths.append(p)
    id_res = t.identity_residue(data)
    id_ok = bool(id_big) and id_cur == len(id_big) and id_res == 0
    if id_big:
        rows.append({"level": "ok" if id_ok else "warn",
                     "path": os.path.join(data, "user-*-personal" if not id_paths else id_paths[0]),
                     "note": "身份层（prompt 末尾，会盖过前置人格）：SOUL/IDENTITY 本版 %d/%d，"
                             "USER/MEMORY 冲突行残留 %d" % (id_cur, len(id_big), id_res)})
    else:
        rows.append({"level": "dim", "path": data,
                     "note": "身份层：未找到 SOUL.md/IDENTITY.md（未启用或路径不同）"})

    metrics = [{"name": "模板靶点", "value": "%d 宽松 / %d 命中" % (loose, pol_n), "ok": pol_n > 0 and loose == pol_n},
               {"name": "命令闸门", "value": "%d / %d" % (gate_ok, len(js_files)), "ok": js_files and gate_ok == len(js_files)},
               {"name": "网页过滤", "value": "%d / %d" % (wf_ok, len(js_files)), "ok": js_files and wf_ok == len(js_files)},
               {"name": "文件保护", "value": "已中和" if guard == 0 else "%d 残留" % guard, "ok": guard == 0},
               {"name": "身份层", "value": "%d / %d" % (id_cur, len(id_big)) if id_big else "—", "ok": id_ok},
               {"name": "数据目录", "value": data or "—", "ok": bool(data)}]
    if state == "current" and not full_ok:
        state = "warn"
    if state == "current" and id_big and not id_ok:
        # 前面几层全绿、身份层不绿 —— 正是"破甲看着成功、行为不是本人"那种状态，
        # 判 stale 而不是 warn：语义就是"该重打一遍"。
        state = "stale"

    # 新版四落点（workbuddy_deploy.py）接管后，卡片以它为准：老那套模板 / 闸门
    # 指标会和新落点给出相反结论（旧的看 product.json，新的是自定义指令 + 32 份模板）。
    dep = None
    try:
        wd = _wb_deploy()
        if wd is not None:
            v = wd.verify("wb")
            stt = wd.state()
            dep = {
                "pack": wd.pack_name(), "packOn": stt.get("pack_on") is not False,
                "custom": {"on": stt.get("custom") is not False,
                           "ok": any(c["ok"] for c in v["checks"] if c["role"] == "自定义指令"),
                           "spots": sum(1 for c in v["checks"] if c["role"] == "自定义指令")},
                "templates": {"on": stt.get("templates") is not False,
                              "ok": sum(1 for c in v["checks"]
                                        if c["role"] == "模板政策块" and c["ok"]),
                              "total": sum(1 for c in v["checks"] if c["role"] == "模板政策块")},
                "identity": {"on": stt.get("identity") is not False,
                             "ok": sum(1 for c in v["checks"]
                                       if c["role"] == "身份层" and c["ok"]),
                             "total": sum(1 for c in v["checks"] if c["role"] == "身份层")},
                "skills": {"on": stt.get("skills") is not False,
                           "ok": sum(1 for c in v["checks"]
                                     if c["role"] == "SKILL.md" and c["ok"]),
                           "total": sum(1 for c in v["checks"] if c["role"] == "SKILL.md")},
                "verify": {"ok": v["ok"], "done": v["done"], "total": v["total"]},
            }
            ok_all = (dep["packOn"] and dep["custom"]["ok"] and dep["templates"]["on"]
                      and dep["templates"]["ok"] == dep["templates"]["total"]
                      and dep["identity"]["ok"] == dep["identity"]["total"]
                      and dep["skills"]["ok"] == dep["skills"]["total"])
            if stt.get("restoredAt") and not stt.get("pack_on"):
                state = "none"
            elif v["ok"] and ok_all:
                state = "current"
            elif v["ok"] or ok_all:
                state = "stale"
            else:
                state = "warn"
            metrics = [
                {"name": "指令源", "value": dep["pack"] or "—", "ok": bool(dep["pack"])},
                {"name": "自定义指令", "value": "已写入" if dep["custom"]["ok"] else "未写入",
                 "ok": dep["custom"]["ok"]},
                {"name": "模板政策块", "value": "%d / %d" % (dep["templates"]["ok"],
                                                          dep["templates"]["total"]),
                 "ok": dep["templates"]["ok"] == dep["templates"]["total"]},
                {"name": "身份层", "value": "%d / %d" % (dep["identity"]["ok"],
                                                      dep["identity"]["total"]),
                 "ok": dep["identity"]["ok"] == dep["identity"]["total"]},
                {"name": "SKILL.md", "value": "%d / %d" % (dep["skills"]["ok"],
                                                         dep["skills"]["total"]),
                 "ok": dep["skills"]["ok"] == dep["skills"]["total"]},
            ]
            try:
                nb = len(os.listdir(os.path.join(data, "pojia-backups")))
            except OSError:
                nb = 0
            metrics.append({"name": "备份", "value": "有" if nb else "无", "ok": nb > 0})
            rows = [{"level": "ok" if v["ok"] else "warn", "path": install,
                     "note": "四路落点：%d / %d 就位" % (v["done"], v["total"])}]
            files = []
    except Exception as e:
        rows.append({"level": "warn", "path": data, "note": "新引擎探测失败：%s" % e})

    card = {"key": "wb", "label": t.label, "home": install, "state": state,
            "rows": rows, "files": files, "metrics": metrics}
    if dep:
        card["deploy"] = dep
    return card


def card_seat_deploy(key):
    """Claude / Hermes / ZCode 卡片：以 seats_deploy 的落点核对为准。

    旧 card_seat_md 按「标记块哈希 + 旧版 output style 哈希」判定，
    与新的三落点引擎口径不同（新版主落点是整份写入的文件，没有标记块），
    会给出「verify 全过、卡片却报未部署」的矛盾结论。
    """
    sd = _seat_deploy()
    if sd is None:
        return {"key": key, "label": key, "home": "", "state": "broken",
                "rows": [{"level": "err", "path": "", "note": "engine/seats_deploy.py 缺失"}],
                "files": [], "metrics": []}
    spec = sd.SEATS[key]
    home = sd.home_of(key)
    v = sd.verify(key)
    st = sd.state(key)
    rows, files, metrics = [], [], []
    if not os.path.isdir(home):
        return {"key": key, "label": spec["label"], "home": home, "state": "none",
                "rows": [{"level": "dim", "path": home, "note": "目录不存在（未安装）"}],
                "files": [], "metrics": []}

    done, total = v["done"], v["total"]
    deployed = st.get("pack_on") is not False and v["ok"]
    if deployed:
        state = "current"
    elif st.get("restoredAt") and st.get("pack_on") is False:
        state = "none"
    elif total and done == 0:
        state = "none"
    elif any(c["ok"] for c in v["checks"]):
        state = "stale"
    else:
        state = "none"

    by_role = {}
    for c in v["checks"]:
        by_role.setdefault(c["role"], []).append(c)
    prim = (by_role.get("指令文件") or [{}])[0]
    rows.append({"level": "ok" if v["ok"] else "warn", "path": home,
                 "note": "%s 落点：%d / %d 就位" % (spec["label"], done, total)})
    rows.append({"level": "ok" if prim.get("ok") else "warn",
                 "path": prim.get("path") or "",
                 "note": "指令文件 %s · %s" % (
                     spec["primary"],
                     "整份写入" if spec["primary_kind"] == "file" else "标记块合并")})
    for role, note in (("记忆文件标记块", "记忆文件标记块"),
                       ("outputStyle 指针", "settings.json outputStyle")):
        for c in by_role.get(role) or []:
            rows.append({"level": "ok" if c["ok"] else "warn", "path": c["path"],
                         "note": note})
    sks = by_role.get("SKILL.md") or []
    if sks:
        n_ok = sum(1 for c in sks if c["ok"])
        rows.append({"level": "ok" if n_ok == len(sks) else "warn",
                     "path": os.path.join(home, "skills"),
                     "note": "路由 skills：%d / %d" % (n_ok, len(sks))})
    files = [{"path": c["path"], "exists": c["exists"], "size": c.get("bytes"),
              "role": c["role"], "hit": [] if not c["ok"] else ["已就位"],
              "miss": [] if c["ok"] else ["未就位"]}
             for c in v["checks"] if c["role"] != "SKILL.md"]
    metrics = [
        {"name": "指令源", "value": sd.pack_name(key) or "—", "ok": bool(sd.pack_name(key))},
        {"name": "指令文件", "value": "已写入" if prim.get("ok") else "未写入",
         "ok": bool(prim.get("ok"))},
    ]
    if spec.get("memory"):
        mchk = (by_role.get("记忆文件标记块") or [{}])[0]
        metrics.append({"name": spec["memory"], "value": "已合并" if mchk.get("ok") else "未写入",
                        "ok": bool(mchk.get("ok"))})
    if spec.get("settings"):
        schk = (by_role.get("outputStyle 指针") or [{}])[0]
        metrics.append({"name": "outputStyle", "value": "pojia" if schk.get("ok") else "未设置",
                        "ok": bool(schk.get("ok"))})
    if sks:
        n_ok = sum(1 for c in sks if c["ok"])
        metrics.append({"name": "SKILL.md", "value": "%d / %d" % (n_ok, len(sks)),
                        "ok": n_ok == len(sks)})
    try:
        nb = len(os.listdir(os.path.join(home, "pojia-backups")))
    except OSError:
        nb = 0
    metrics.append({"name": "备份", "value": "有" if nb else "无", "ok": nb > 0})
    return {"key": key, "label": spec["label"], "home": home, "state": state,
            "rows": rows, "files": files, "metrics": metrics}


def card_seat_md(key):
    """Claude / Grok / Gemini 通用卡片（grok/gemini 通道保留，GUI 不再出卡）：单文件标记块 + skills 投放计数。

    Claude 例外（2026-10-11）：CLAUDE_CHANNEL="style" 后主通道是
    output-styles/pojia.md + settings.outputStyle，CLAUDE.md 本来就**不该**有
    标记块（双份才是错的）。这一分支按 style 通道判定，别按旧标记块。
    """
    m = mod()
    t = m.TARGETS[key]()
    home = t.resolve_home("")
    md_p, st_p, bak = t.paths(home)
    begin, end = m.SEAT_MARKS[key]
    rows, files, metrics = [], [], []

    if not os.path.isdir(home):
        return {"key": key, "label": t.label, "home": home, "state": "none",
                "rows": [{"level": "dim", "path": home, "note": "目录不存在（未安装）"}],
                "files": [], "metrics": []}

    # ---- Claude · style 通道专用判定 ----
    if key == "claude" and getattr(m, "CLAUDE_CHANNEL", "") == "style":
        style_p = os.path.join(home, "output-styles", m.CLAUDE_STYLE_NAME + ".md")
        style_txt = ""
        if os.path.isfile(style_p):
            with open(style_p, "rb") as f:
                style_txt = f.read().decode("utf-8-sig", "replace")
        settings_p = os.path.join(home, "settings.json")
        out_style = ""
        if os.path.isfile(settings_p):
            try:
                out_style = (json.loads(open(settings_p, encoding="utf-8").read())
                             .get("outputStyle") or "")
            except Exception:
                out_style = ""
        user, _src = m.load_user_persona("")
        want = m.seat_want("claude", user)
        # style 的哈希记在 CLAUDE_STYLE_MARK 里（不是 cfg-v7 行）—— 用专属提取
        _mm = re.search(re.escape(m.CLAUDE_STYLE_MARK) + r"\s*([0-9a-f]{12})", style_txt)
        got = _mm.group(1) if _mm else ""
        has_mark = bool(_mm)
        lv = "current" if (has_mark and got == want and out_style == m.CLAUDE_STYLE_NAME) \
            else ("stale" if has_mark else "none")
        if style_txt:
            files.append({"path": style_p, "exists": True,
                          "size": len(style_txt.encode("utf-8")),
                          "sha": sha_of(style_p), "role": "output-styles/%s.md" % m.CLAUDE_STYLE_NAME,
                          "hit": [], "miss": [] if lv in ("current", "stale") else ["无 style 标记"]})
        state = lv if lv in ("current", "stale") else "none"
        if state == "current":
            rows.append({"level": "ok", "path": style_p,
                         "note": "output style · 主通道 · 本版"})
        elif state == "stale":
            rows.append({"level": "warn", "path": style_p,
                         "note": "output style 在但哈希旧（apply 自动升级）"})
        else:
            rows.append({"level": "warn", "path": style_p,
                         "note": "未部署（output style 不存在）"})
        if out_style != m.CLAUDE_STYLE_NAME:
            state = "none"
            rows.append({"level": "warn", "path": settings_p,
                         "note": "settings.json outputStyle = %r（应为 %r）"
                                 % (out_style, m.CLAUDE_STYLE_NAME)})
        else:
            rows.append({"level": "ok", "path": settings_p,
                         "note": "settings.json outputStyle = %s" % m.CLAUDE_STYLE_NAME})
        # CLAUDE.md 托管核对（内容源 content/seats/claude_md.md）—— 与 style 双份才是问题
        md_txt = (open(md_p, "rb").read().decode("utf-8-sig", "replace")
                  if os.path.isfile(md_p) else "")
        md_src = m.read_text_safe(m.CLAUDE_MD_SEAT_SRC) if hasattr(m, "CLAUDE_MD_SEAT_SRC") else None
        if md_src and md_src.strip():
            if m.SEAT_MARKS[key][0] in md_txt:
                rows.append({"level": "warn", "path": md_p,
                             "note": "CLAUDE.md 仍带标记块 —— 与 style 双份，跑 apply 清掉"})
            elif md_txt.strip() == md_src.strip():
                rows.append({"level": "ok", "path": md_p,
                             "note": "CLAUDE.md · 托管 · 与席位源一致"})
            else:
                rows.append({"level": "warn", "path": md_p,
                             "note": "CLAUDE.md 与席位源不一致（apply 按源覆盖）"})
        shim_has_append = any(
            "--append-system-prompt-file" in (open(p, encoding="utf-8", errors="replace").read()
                                              if os.path.isfile(p) else "")
            for p in t.shim_paths())
        if shim_has_append:
            rows.append({"level": "warn", "path": "npm shims",
                         "note": "shim 仍挂 --append-system-prompt-file —— 跑 apply 清掉"})
        md_rows_len = len(rows)
        cli_found, cli_where = t.client_probe()
        total = ours = 0
        skills_root = os.path.join(home, "skills")
        if os.path.isdir(skills_root):
            for name in os.listdir(skills_root):
                if os.path.isdir(os.path.join(skills_root, name)):
                    total += 1
                    sk = os.path.join(skills_root, name, "SKILL.md")
                    if os.path.isfile(sk):
                        with open(sk, "rb") as sf:
                            if b"pojia-route-skill" in sf.read()[-600:]:
                                ours += 1
        metrics = [{"name": "客户端",
                    "value": (os.path.basename(cli_where) if cli_found
                              else ("未检测到" if cli_found is False else "—")),
                    "ok": cli_found is not False},
                   {"name": "标记块", "value": {"current": "本版", "stale": "旧版",
                                               "none": "未部署"}.get(state, state),
                    "ok": state == "current"},
                   {"name": "路由 skills", "value": "%d / %d" % (ours, total) if total else "未投放",
                    "ok": ours > 0},
                   {"name": "原稿备份", "value": "有" if os.path.isdir(bak) else "无",
                    "ok": os.path.isdir(bak)}]
        return {"key": key, "label": t.label, "home": home, "state": state,
                "rows": rows, "files": files, "metrics": metrics}

    txt = ""
    if os.path.isfile(md_p):
        with open(md_p, "rb") as f:
            txt = f.read().decode("utf-8-sig", "replace")
    user, _src = m.load_user_persona("")
    want = m.seat_want(key, user)
    lv = m.seat_md_state(txt, key, want)
    cli_found, cli_where = t.client_probe()      # 文件在位 ≠ 客户端会读它

    if txt:
        files.append({"path": md_p, "exists": True, "size": len(txt.encode("utf-8")),
                      "sha": sha_of(md_p), "role": t.md_name,
                      "hit": [] if lv != "none" else [], "miss": [] if lv in ("current", "stale") else ["无标记块"]})
    state = lv if lv in ("current", "stale") else "none"
    if state == "current" and cli_found is False:
        state = "orphan"                          # 标记块在位，但客户端已经没了
    if state == "current":
        rows.append({"level": "ok", "path": md_p, "note": "%s · 标记块·本版" % t.md_name})
    elif state == "orphan":
        rows.append({"level": "warn", "path": md_p,
                     "note": "%s · 标记块在位，但未检测到 %s 可执行文件 —— 这份不会被读取"
                             % (t.md_name, t.label)})
        rows.append({"level": "dim", "path": t.label,
                     "note": "PATH / npm 全局 / npx 缓存都没找到；已卸载，或装在自定义路径"})
    elif state == "stale":
        rows.append({"level": "warn", "path": md_p, "note": "标记块在但哈希旧（apply 自动升级）"})
    else:
        rows.append({"level": "warn", "path": md_p, "note": "未部署（无标记块）"})

    skills_root = os.path.join(home, "skills")
    total = ours = 0
    if os.path.isdir(skills_root):
        for name in os.listdir(skills_root):
            if os.path.isdir(os.path.join(skills_root, name)):
                total += 1
                sk = os.path.join(skills_root, name, "SKILL.md")
                if os.path.isfile(sk):
                    with open(sk, "rb") as sf:
                        if b"pojia-route-skill" in sf.read()[-600:]:
                            ours += 1
    if total:
        rows.append({"level": "ok" if ours else "dim", "path": skills_root,
                     "note": "skills：总 %d 个，其中路由三拍 %d 个" % (total, ours)})

    metrics = [{"name": "客户端",
                "value": (os.path.basename(cli_where) if cli_found
                          else ("未检测到" if cli_found is False else "—")),
                "ok": cli_found is not False},
               {"name": "标记块", "value": {"current": "本版", "stale": "旧版",
                                            "orphan": "本版·无客户端", "none": "未部署"}.get(state, state),
                "ok": state == "current"},
               {"name": "路由 skills", "value": "%d / %d" % (ours, total) if total else "未投放",
                "ok": ours > 0},
               {"name": "原稿备份", "value": "有" if os.path.isdir(bak) else "无", "ok": os.path.isdir(bak)}]
    return {"key": key, "label": t.label, "home": home, "state": state,
            "rows": rows, "files": files, "metrics": metrics}


CARD_FN = {"codex": card_codex, "dsh": card_dsh, "dsh-desktop": card_dsh_desktop,
           "wb": card_wb, "zcode": lambda: card_seat_deploy("zcode"),
           "pojia": card_preset,
           "claude": lambda: card_seat_deploy("claude"),
           "hermes": lambda: card_seat_deploy("hermes")}


# 每个席位"部署了什么文件"清单（相对 home；文件已列过的跳过）
DEPLOY_MANIFEST = {
    "codex": [("config.toml", "配置（模型 / 指令文件指针）"),
              ("codex-unlock-instructions.md", "主注入 · model_instructions_file"),
              ("AGENTS.md", "托管 · Windows 约束"),
              ("skills", "技能目录（路由三拍 100 份)")],
    "claude": [("output-styles/pojia.md", "主注入 · output style"),
               ("settings.json", "通道启用（outputStyle + 模型映射）"),
               ("CLAUDE.md", "托管 · 工作区说明 + Windows 约束"),
               ("skills", "技能目录（路由三拍 + mature-fiction）")],
    "zcode": [("AGENTS.md", "主注入 · 角色档 + Windows 约束")],
    "hermes": [("SOUL.md", "门禁身份层"), ("skills", "技能目录")],
}


def _augment_deploy_files(card):
    """把 DEPLOY_MANIFEST 里该席位的部署文件补进 files[]（已列过的不重复）。"""
    if card.get("deploy"):
        return          # 席位自己给了人话摘要（如 codex），不再按清单补空行
    manifest = DEPLOY_MANIFEST.get(card.get("key")) or []
    home = card.get("home") or ""
    if not manifest or not home or not os.path.isdir(home):
        return
    have = {f.get("path") for f in card.get("files", [])}
    for rel, role in manifest:
        p = os.path.join(home, rel)
        if p in have:
            continue
        exists = os.path.exists(p)
        isdir = os.path.isdir(p)
        size = None
        if exists and not isdir:
            try:
                size = os.path.getsize(p)
            except OSError:
                size = None
        card.setdefault("files", []).append(
            {"path": p, "exists": exists, "size": size, "isdir": isdir,
             "role": role, "hit": [], "miss": []})


# ─────────────────────────── 补丁总览（purge 式表）───────────────────────────

# files[] 里的 role → 补丁表分组。没有列进的 role 归「其它」。
_PATCH_GROUPS = [
    ("inject", "注入内容", ("指令文件", "第二注入位", "角色档", "第二注入位 · 标记块",
                            "AGENTS.md", "CLAUDE.md", "SOUL.md")),
    ("patch", "补丁后文件", ("补丁后文件", "闸门脚本", "补丁层", "标记块")),
    ("output", "输出通道", ("output-styles/pojia.md",)),
    ("skill", "技能投放", ("技能目录（路由三拍 + mature-fiction）", "技能目录")),
    ("preset", "破甲模式", ("破甲模式",)),
]

# 状态判定：与部署面的 want 对齐 = 本版；有标记/存在 = 已装；否则未装。
def _patch_state(f, card_state):
    if not f.get("exists"):
        return "missing"
    miss = f.get("miss") or []
    if not miss and f.get("hit"):
        return "current"
    if not miss and f.get("size"):          # 有的席位不记 hit/miss，只看存在
        return "current" if card_state == "current" else "stale"
    if miss:
        return "stale"
    return "stale" if card_state in ("stale", "warn") else "current"


def _skill_rows(skills_root, card_state):
    """把技能目录展开成逐技能行：每个子目录一行，SKILL.md 在且带路由标记 = 本版。

    三态：带 pojia-route-skill 标记 = current；SKILL.md 在但没标记 = external
    （外来技能 —— ctf-*、mature-fiction 这类别的来源装的，不是部署产物，
    判「旧版」是误导）；连 SKILL.md 都没有 = missing。
    """
    rows = []
    if not skills_root or not os.path.isdir(skills_root):
        return rows
    try:
        names = sorted(os.listdir(skills_root))
    except OSError:
        return rows
    ours = 0
    for name in names:
        d = os.path.join(skills_root, name)
        if not os.path.isdir(d):
            continue
        sk = os.path.join(d, "SKILL.md")
        has_sk = os.path.isfile(sk)
        marked = False
        if has_sk:
            try:
                with open(sk, "rb") as sf:
                    marked = b"pojia-route-skill" in sf.read()[-600:]
            except OSError:
                marked = False
        if marked:
            ours += 1
        if marked:
            st, miss = "current", []
        elif has_sk:
            st, miss = "external", ["外来技能（非本工具投放）"]
        else:
            st, miss = "missing", ["无 SKILL.md"]
        rows.append({
            "path": sk if has_sk else d + "\\",
            "role": ("SKILL.md" if has_sk else "目录（无 SKILL.md）"),
            "size": os.path.getsize(sk) if has_sk else None,
            "isdir": not has_sk,
            "sha": sha_of(sk) if marked else "",
            "hit": ["pojia-route-skill"] if marked else [],
            "miss": miss,
            "state": st,
        })
    if rows:
        # 目录行本身留在最前，报总数
        rows.insert(0, {
            "path": skills_root, "role": "技能目录 · %d 个（含路由 %d）" % (len(rows), ours),
            "size": None, "isdir": True, "sha": "",
            "hit": ["%d" % ours] if ours else [], "miss": [] if ours else ["空目录"],
            "state": "current" if ours else "stale",
        })
    return rows


def cmd_patches(target):
    """按 purge 的 PatchGroups 画法出一张补丁总览：分组 → 行（路径/角色/大小/状态）。

    数据全部来自各席位卡片本来的 files[] + rows[]，不另起一套探测 —— 卡片明细
    与补丁表永远同源，不会一个说有一个说没有。
    """
    m = mod()
    try:
        card = CARD_FN[target]()
        _augment_deploy_files(card)
    except Exception as e:
        return {"ok": False, "error": "%s 检测出错：%s" % (target, e)}
    groups = []
    seen = {"inject": [], "patch": [], "output": [], "skill": [], "preset": [], "other": []}

    # codex 有结构化摘要（card["deploy"]）：按四路直接出「部署了什么」，
    # 比按角色猜分组准 —— 每条都写清楚源件 → 落点、或投放数量。
    dep0 = card.get("deploy") or {}
    if dep0 and target == "codex":
        ins, ag, cfg, sk = (dep0.get("instruction") or {}, dep0.get("agents") or {},
                            dep0.get("config") or {}, dep0.get("skills") or {})
        seen["inject"].append({
            "path": ins.get("dst") or "", "role": "指令文件（注入正文）",
            "size": ins.get("bytes"), "isdir": False, "sha": "",
            "hit": ["源件 " + os.path.basename(ins.get("src") or "")] if ins.get("on") else [],
            "miss": [] if ins.get("on") else ["未注入"],
            "state": "current" if ins.get("on") else "missing",
        })
        seen["inject"].append({
            "path": ag.get("path") or "", "role": "AGENTS.md 标记块",
            "size": ag.get("bytes"), "isdir": False, "sha": "",
            "hit": ["POJIA-CODEX 标记块"] if ag.get("on") else [],
            "miss": [] if ag.get("on") else ["标记块未写入"],
            "state": "current" if ag.get("on") else "missing",
        })
        seen["inject"].append({
            "path": cfg.get("path") or "", "role": "config.toml 指针",
            "size": None, "isdir": False, "sha": "",
            "hit": [cfg.get("value") or ""] if cfg.get("on") else [],
            "miss": [] if cfg.get("on") else ["指针未写入"],
            "state": "current" if cfg.get("on") else "missing",
        })
        ok_ids = set(sk.get("samples") or [])
        all_ok = bool(sk.get("total")) and sk.get("ok") == sk.get("total")
        for sid in (sk.get("ids") or []):
            ok = all_ok or (sid in ok_ids)
            sp = os.path.join(sk.get("dir") or "", sid, "SKILL.md")
            size = None
            try:
                if os.path.isfile(sp):
                    size = os.path.getsize(sp)
            except OSError:
                size = None
            seen["skill"].append({
                "path": sp,
                "role": "路由技能", "size": size, "isdir": False, "sha": "",
                "hit": ["已投放"] if ok else [], "miss": [] if ok else ["未投放"],
                "state": "current" if ok else "missing",
            })
        for gid, gname, _roles in _PATCH_GROUPS:
            rows = seen.pop(gid)
            if rows:
                rows.sort(key=lambda r: r["path"])
                groups.append({"id": gid, "name": gname, "rows": rows})
        return {"ok": True, "target": target, "label": card.get("label") or target,
                "state": card.get("state"), "deploy": dep0, "groups": groups,
                "files": sum(len(g["rows"]) for g in groups)}

    # WorkBuddy 有自己的结构化摘要（四落点），按它出「部署了什么」。
    if dep0 and target == "wb":
        wd = _wb_deploy()
        home = wd.home_of() if wd else ""
        cu, tp, idp, sk = (dep0.get("custom") or {}, dep0.get("templates") or {},
                           dep0.get("identity") or {}, dep0.get("skills") or {})
        v = wd.verify("wb") if wd else {"checks": []}
        for c in v.get("checks", []):
            role = c.get("role") or ""
            if role == "自定义指令":
                gid, label = "inject", "自定义指令"
            elif role == "模板政策块":
                gid, label = "patch", "模板政策块"
            elif role == "身份层":
                gid, label = "inject", "身份层"
            elif role == "SKILL.md":
                gid, label = "skill", "路由技能"
            else:
                gid, label = "other", role
            seen[gid].append({
                "path": c.get("path") or "", "role": label,
                "size": c.get("bytes"), "isdir": False, "sha": "",
                "hit": ["已就位"] if c.get("ok") else [],
                "miss": [] if c.get("ok") else ["未就位"],
                "state": "current" if c.get("ok") else "missing",
            })
        for gid, gname, _roles in _PATCH_GROUPS:
            rows = seen.pop(gid)
            if rows:
                rows.sort(key=lambda r: r["path"])
                groups.append({"id": gid, "name": gname, "rows": rows})
        return {"ok": True, "target": target, "label": card.get("label") or target,
                "state": card.get("state"), "deploy": dep0, "groups": groups,
                "files": sum(len(g["rows"]) for g in groups)}

    for f in card.get("files", []):
        role = f.get("role") or ""
        grp = "other"
        for gid, _g, roles in _PATCH_GROUPS:
            if role in roles:
                grp = gid
                break
        else:
            # role 没登记但路径是 skills 目录 → 技能投放
            p = (f.get("path") or "").replace("\\", "/").rstrip("/").lower()
            if p.endswith("/skills") or "/skills/" in p:
                grp = "skill"
        if grp == "skill" and f.get("isdir"):
            # 技能目录整包展开成逐技能行，别只留一行路径
            seen["skill"].extend(_skill_rows(f.get("path"), card.get("state")))
            continue
        st = _patch_state(f, card.get("state"))
        seen[grp].append({
            "path": f.get("path") or "",
            "role": role or "未归类",
            "size": f.get("size"),
            "isdir": bool(f.get("isdir")),
            "sha": f.get("sha") or "",
            "hit": f.get("hit") or [],
            "miss": f.get("miss") or [],
            "state": st,
        })
    for gid, gname, _roles in _PATCH_GROUPS:
        rows = seen.pop(gid)
        if rows:
            rows.sort(key=lambda r: r["path"])
            groups.append({"id": gid, "name": gname, "rows": rows})
    if seen["other"]:
        seen["other"].sort(key=lambda r: r["path"])
        groups.append({"id": "other", "name": "其它落点", "rows": seen["other"]})
    # 检测明细（卡片 rows[]）：体检注记、「未知」标记、环境提示 —— 总览里也要看得到
    det = []
    for r in card.get("rows", []):
        lv = r.get("level") or "dim"
        det.append({
            "path": r.get("path") or "",
            "role": r.get("note") or (lv if lv != "dim" else ""),
            "size": None, "isdir": False, "sha": "",
            "hit": [], "miss": [r.get("note")] if r.get("note") and lv in ("warn", "err") else [],
            "state": {"ok": "current", "err": "missing", "warn": "stale"}.get(lv, "external"),
        })
    if det:
        groups.append({"id": "rows", "name": "检测明细", "rows": det})
    return {"ok": True, "target": target, "label": card.get("label") or target,
            "state": card.get("state"), "deploy": card.get("deploy") or {},
            "groups": groups}


# ─────────────────────────── 指令提示词管理 ───────────────────────────

# 指令库根：targets/<seat>/instructions/ 下每份 .md 都是一个候选指令。
INSTR_LIB_DIR = lambda seat: os.path.join(ROOT, "targets", seat, "instructions")

# 候选指令的说明（文件名 → 描述）。没有登记的名字用通用描述。
INSTR_DESC = {
    "unlock.md": "原版 unlock 指令（本脚本生成的强化版政策）",
    "Argon.md": "Argon Operating Policy（条款式部署政策）",
}


def _instr_lib(seat):
    """列出某席位指令库里的候选指令。"""
    d = INSTR_LIB_DIR(seat)
    out = []
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if fn.lower().endswith(".md"):
                out.append({"name": fn,
                            "desc": INSTR_DESC.get(fn, "自定义指令"),
                            "size": os.path.getsize(os.path.join(d, fn))})
    return out


def _active_instr(seat):
    """读出当前部署的指令：返回 (文件名, 内容哈希前8, 字节数) 或 None。"""
    dep = _deployed_instr_path(seat)
    if not dep or not os.path.isfile(dep):
        return None
    with open(dep, "rb") as f:
        data = f.read()
    return {"file": os.path.basename(dep),
            "sha": sha_of(dep)[:8],
            "size": len(data)}


def _deployed_instr_path(seat):
    """各席位部署后的指令落点（当前只支持 codex）。

    以 config.toml 的 model_instructions_file 指针为准 —— 指令换名部署后
    指针跟着文件名走，固定常量 CODEX_INSTR 只在指针缺失时兜底。
    """
    if seat != "codex":
        return None
    m_ = mod()
    t = m_.TARGETS["codex"]()
    home = t.resolve_home("")
    cfg_p = t.paths(home)[0]
    mm = re.search(
        r"^[ \t]*model_instructions_file[ \t]*=[ \t]*[\"']\.?/?([^\"'\n]+)[\"']",
        m_.read_text_safe(cfg_p) or "", re.M)
    fn = mm.group(1).strip() if mm else m_.CODEX_INSTR
    return os.path.join(home, fn)


def cmd_instr_list(seat):
    """指令库清单：候选指令 + 当前部署状态。"""
    dep = _deployed_instr_path(seat)
    dep_txt = ""
    if dep and os.path.isfile(dep):
        with open(dep, "rb") as f:
            dep_txt = f.read().decode("utf-8-sig", "replace")
    items = []
    for it in _instr_lib(seat):
        p = os.path.join(INSTR_LIB_DIR(seat), it["name"])
        with open(p, "rb") as f:
            lib_txt = f.read().decode("utf-8-sig", "replace")
        active = bool(dep_txt) and lib_txt.strip() == dep_txt.strip()
        items.append(dict(it, active=active))
    return {"ok": True, "seat": seat, "lib": INSTR_LIB_DIR(seat),
            "active": _active_instr(seat), "items": items}


def cmd_instr_apply(seat, name):
    """把选中的指令直接部署：清掉旧指令文件，把候选文件原样拷到部署位。

    文件名跟着候选走（Argon.md → ~/.codex/Argon.md），config.toml 的
    model_instructions_file 指针同步改写 —— 不是只换内容。
    """
    m_ = mod()
    if seat != "codex":
        return {"ok": False, "error": "暂只支持 codex 席位的指令部署"}
    lib_p = os.path.join(INSTR_LIB_DIR(seat), name)
    if not name or not os.path.isfile(lib_p) or not name.lower().endswith(".md"):
        return {"ok": False, "error": "指令库中没有这份文件：%s" % name}
    t = m_.TARGETS["codex"]()
    home = t.resolve_home("")
    if not os.path.isdir(home):
        return {"ok": False, "error": "未找到 Codex 目录：%s" % home}
    dep_old = os.path.join(home, m_.CODEX_INSTR)
    dep_new = os.path.join(home, name)
    # config 现指针指向的文件也是"旧指令"，换部署时一并清理
    cfg_p0, _i0, _s0, _b0 = t.paths(home)
    m0 = re.search(
        r"^[ \t]*model_instructions_file[ \t]*=[ \t]*[\"']\.?/?([^\"'\n]+)[\"']",
        m_.read_text_safe(cfg_p0) or "", re.M)
    old_ptr = os.path.join(home, m0.group(1).strip()) if m0 else None

    # 1. 清掉旧指令文件（先备份到 editor-backup）
    removed = []
    stale = []
    for old_p in (dep_old, old_ptr):
        if not old_p or not os.path.isfile(old_p):
            continue
        if os.path.normcase(old_p) == os.path.normcase(dep_new):
            continue
        if os.path.normcase(old_p) in {os.path.normcase(x) for x in stale}:
            continue
        try:
            with open(old_p, "rb") as f:
                old = f.read()
            bak = _backup_path(old_p)
            if not (os.path.isfile(bak) and open(bak, "rb").read() == old):
                with open(bak, "wb") as f:
                    f.write(old)
        except OSError:
            pass
        try:
            os.remove(old_p)
            removed.append(os.path.basename(old_p))
            stale.append(old_p)
        except OSError as e:
            return {"ok": False, "error": "清理旧指令失败：%s" % e}

    # 2. 候选文件原样拷到部署位
    with open(lib_p, "rb") as f:
        data = f.read()
    try:
        with open(dep_new, "wb") as f:
            f.write(data)
    except OSError as e:
        return {"ok": False, "error": "部署指令失败：%s" % e}

    # 3. config.toml 的 model_instructions_file 指针改写为新文件名
    cfg_p, _inst, _st, _bak = t.paths(home)
    cfg = m_.read_text_safe(cfg_p) or ""
    changed_cfg = False
    out_lines = []
    for ln in cfg.splitlines():
        if re.match(r"^[ \t]*model_instructions_file[ \t]*=", ln):
            out_lines.append('model_instructions_file = "./%s"' % name)
            changed_cfg = True
        else:
            out_lines.append(ln)
    new_cfg = "\n".join(out_lines)
    if not changed_cfg:
        new_cfg = new_cfg.rstrip() + ('\nmodel_instructions_file = "./%s"\n' % name)
        changed_cfg = True
    if new_cfg != cfg:
        try:
            t.backup(home, cfg_p, "before-instr-switch")
            m_.write_text(cfg_p, new_cfg)
        except Exception as e:
            return {"ok": False, "error": "改写 config 失败：%s" % e}

    # 4. 项目快照同步（内容 = 新指令，名字走快照路径常量不变；静默引擎的 stdout 日志）
    try:
        with open(os.devnull, "w", encoding="utf-8") as _dn:
            _so, sys.stdout = sys.stdout, _dn
            try:
                t.sync_snapshot(data.decode("utf-8-sig", "replace").replace("\r\n", "\n"))
            finally:
                sys.stdout = _so
    except Exception:
        pass
    _inject_meta_update(seat=seat, last="instr")
    return {"ok": True, "seat": seat, "file": name, "deployed": dep_new,
            "removed": removed, "config_updated": changed_cfg,
            "note": "部署完成 —— 对运行中的旧 Codex 会话无效，需新开会话"}


def cmd_instr_disable(seat):
    """关闭当前部署的指令：删掉部署位文件，并从 config.toml 摘掉指针。

    与 cmd_instr_apply 对称 —— 对应界面上那张开关拨回左边。
    删除前先把文件和 config 备份到 editor-backup。
    """
    m_ = mod()
    if seat != "codex":
        return {"ok": False, "error": "暂只支持 codex 席位的指令部署"}
    t = m_.TARGETS["codex"]()
    home = t.resolve_home("")
    if not os.path.isdir(home):
        return {"ok": False, "error": "未找到 Codex 目录：%s" % home}
    cfg_p, _i, _s, _b = t.paths(home)
    cfg = m_.read_text_safe(cfg_p) or ""
    mm = re.search(
        r"^[ \t]*model_instructions_file[ \t]*=[ \t]*[\"']\.?/?([^\"'\n]+)[\"']",
        cfg, re.M)
    if not mm:
        _inject_meta_update(seat=seat, last="instr")
        return {"ok": True, "removed": [], "config_updated": False,
                "note": "当前没有启用指令，无需关闭"}
    dep = os.path.join(home, mm.group(1).strip())
    removed = []
    if os.path.isfile(dep):
        try:
            with open(dep, "rb") as f:
                old = f.read()
            bak = _backup_path(dep)
            if not (os.path.isfile(bak) and open(bak, "rb").read() == old):
                with open(bak, "wb") as f:
                    f.write(old)
            os.remove(dep)
            removed.append(os.path.basename(dep))
        except OSError as e:
            return {"ok": False, "error": "删除部署文件失败：%s" % e}

    out_lines = [ln for ln in cfg.splitlines()
                 if not re.match(r"^[ \t]*model_instructions_file[ \t]*=", ln)]
    new_cfg = "\n".join(out_lines).rstrip() + "\n"
    try:
        t.backup(home, cfg_p, "before-instr-disable")
        m_.write_text(cfg_p, new_cfg)
    except Exception as e:
        return {"ok": False, "error": "改写 config 失败：%s" % e}
    return {"ok": True, "seat": seat, "removed": removed, "config_updated": True,
            "note": "已关闭指令注入 —— 对运行中的旧 Codex 会话无效，需新开会话"}


def cmd_instr_toggle(seat, name, on):
    """指令文件的滑块：拨右 = 部署这一份，落点一起写；拨左 = 还原。

    ChatGPT / WorkBuddy / Claude / Hermes / ZCode 各用各的引擎，正文互不共享。
    """
    is_seat = seat in ("claude", "hermes", "zcode")
    is_wb = seat in ("wb", "workbuddy")
    ad = _seat_deploy() if is_seat else (_wb_deploy() if is_wb else _codex_deploy())
    lib = (ad.lib_dir(seat) if is_seat and ad is not None
           else (ad.INSTR_LIB if ad is not None else ""))
    if ad is None:
        return {"ok": False, "error": "engine/%s 缺失"
                % ("seats_deploy.py" if is_seat else
                   "workbuddy_deploy.py" if is_wb else "codex_deploy.py")}
    # 只接受指令库里的裸文件名：挡住 ../ 越界与绝对路径
    if not name or name != os.path.basename(name) or not name.lower().endswith(".md"):
        return {"ok": False, "error": "指令源文件名无效：%s" % name}
    if not os.path.isfile(os.path.join(lib, name)):
        return {"ok": False, "error": "指令库里没有这份文件：%s" % name}
    eng = seat if is_seat else ("wb" if is_wb else "codex")
    if not on:
        ad.set_pack(seat, name, False) if is_seat else ad.set_pack(name, False)
        r = ad.deploy(eng, {"pack": ""})
        st = ad.state(seat) if is_seat else ad.state()
        return {"ok": r.get("ok", True),
                "note": "已关闭指令注入（撤下部署文件与相关落点）",
                "removed": r.get("removed") or [], "state": st,
                "home": ad.home_of(seat) if is_seat else
                        (ad.home_of() if is_wb else ad.home_of("codex"))}
    st = ad.state(seat) if is_seat else ad.state()
    ad.set_pack(seat, name, True) if is_seat else ad.set_pack(name, True)
    flags = (("pack_on", "primary", "memory", "settings", "skills") if is_seat
             else ("pack_on", "agents", "config", "skills") if not is_wb
             else ("pack_on", "custom", "templates", "identity", "skills"))
    if all(st.get(f) is False for f in flags):
        ad.enable_all(seat) if is_seat else ad.enable_all()
    r = ad.deploy(eng, {"pack": name})
    return {"ok": r.get("ok", False),
            "note": "已部署 %s（%d 个落点）" % (name, len(r.get("written") or [])),
            "written": r.get("written"), "home": r.get("home")}


# 页内编辑的指令文件登记表：id → (路径, 分类, 标签)。
PROMPT_FILES = [
    ("seat-codex", os.path.join(CONTENT, "seats", "codex.md"),
     "注入指令", "席位指令源 · Codex"),
    ("seat-claude", os.path.join(CONTENT, "seats", "claude.md"),
     "注入指令", "席位指令源 · Claude Code"),
    ("seat-dsh", os.path.join(CONTENT, "seats", "dsh.md"),
     "注入指令", "席位指令源 · DSH"),
    ("seat-workbuddy", os.path.join(CONTENT, "seats", "workbuddy.md"),
     "注入指令", "席位指令源 · WorkBuddy"),
    ("seat-zcode", os.path.join(CONTENT, "seats", "zcode.md"),
     "注入指令", "席位指令源 · ZCode"),
    ("seat-hermes", os.path.join(CONTENT, "seats", "hermes.md"),
     "注入指令", "席位指令源 · Hermes"),
    # 人格与路由
    ("persona", os.path.join(CONTENT, "persona.md"),
     "人格与路由", "人格源件 persona.md（全部席位共用）"),
    ("route-layer", os.path.join(CONTENT, "route-layer.md"),
     "人格与路由", "任务路由层 route-layer.md（生成产物）"),
    # 托管文档
    ("codex-agents", os.path.join(CONTENT, "seats", "codex_agents_md.md"),
     "托管文档", "Codex AGENTS.md 托管源"),
    ("claude-md", os.path.join(CONTENT, "seats", "claude_md.md"),
     "托管文档", "Claude CLAUDE.md 托管源"),
    ("workbuddy-user-md", os.path.join(CONTENT, "seats", "workbuddy_user_md.md"),
     "托管文档", "WorkBuddy 用户文档托管源"),
]


def _prompt_entry(pid):
    for pid_, path, _cat, label in PROMPT_FILES + _custom_prompts():
        if pid_ == pid:
            return path, label
    return None, None


def _custom_prompt_dir():
    return os.path.join(CONTENT, "seats")


def _custom_prompts():
    """扫描用户自建的指令：content/seats/custom_*.md → id = custom-<stem>，归「自定义指令」。"""
    out = []
    try:
        for fn in sorted(os.listdir(_custom_prompt_dir())):
            m = re.fullmatch(r"custom_([A-Za-z0-9_\-]+)\.md", fn)
            if m:
                out.append(("custom-" + m.group(1),
                            os.path.join(_custom_prompt_dir(), fn),
                            "自定义指令", "自定义指令 · " + m.group(1)))
    except OSError:
        pass
    return out


def _prompt_entry(pid):
    for pid_, path, _cat, label in PROMPT_FILES + _custom_prompts():
        if pid_ == pid:
            return path, label
    return None, None


def cmd_prompts():
    """指令清单（带分类）：路径 + 体积 + 是否存在。"""
    items = []
    for pid, path, cat, label in PROMPT_FILES + _custom_prompts():
        items.append({"id": pid, "path": path, "label": label, "cat": cat,
                      "exists": os.path.isfile(path),
                      "size": os.path.getsize(path) if os.path.isfile(path) else 0})
    return {"ok": True, "items": items}


def cmd_prompt_read(pid):
    """整文件读出（编辑用），上限 2 MB，与 cmd_read 同一档。"""
    p, label = _prompt_entry(pid)
    if not p:
        return {"ok": False, "error": "未登记的提示词 id：%s" % pid}
    if not os.path.isfile(p):
        return {"ok": False, "error": "文件不存在：%s" % p, "path": p, "label": label}
    out = {"ok": True, "id": pid, "path": p, "label": label, "content": "", "editable": False}
    try:
        sz = os.path.getsize(p)
        if sz > 2 * 1024 * 1024:
            out["error"] = "文件超过 2 MB，请直接用编辑器改：%s" % p
            return out
        with open(p, "rb") as f:
            out["content"] = f.read().decode("utf-8-sig", "replace")
        out["size"] = sz
        out["editable"] = True
    except OSError as e:
        out["error"] = str(e)
    return out


def cmd_prompt_write(pid, content):
    """写回提示词源件。写之前备份原内容到 状态/editor-backup/。"""
    p, _label = _prompt_entry(pid)
    if not p:
        return {"ok": False, "error": "未登记的提示词 id：%s" % pid}
    if not os.path.isfile(p):
        return {"ok": False, "error": "文件不存在：%s" % p}
    try:
        with open(p, "rb") as f:
            old = f.read()
        bak = _backup_path(p)
        if not (os.path.isfile(bak) and open(bak, "rb").read() == old):
            with open(bak, "wb") as f:
                f.write(old)
        new = content.encode("utf-8")
        if old == new:
            return {"ok": True, "path": p, "written": False, "note": "内容没变，不写盘"}
        with open(p, "wb") as f:
            f.write(new)
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "path": p, "written": True, "backup": bak,
            "size": len(new), "note": "已写回 · 备份在 状态/editor-backup · 改完记得重新部署"}


def _prompt_pid_ok(name):
    """自定义提示词名的合法字符：字母数字下划线连字符。"""
    return bool(re.fullmatch(r"[A-Za-z0-9_\-]{1,40}", name or ""))


def cmd_prompt_new(name):
    """新建一份自定义提示词：content/seats/custom_<name>.md。"""
    if not _prompt_pid_ok(name):
        return {"ok": False, "error": "名称只允许字母 / 数字 / _ / -（1-40 字符）：%r" % name}
    p = os.path.join(_custom_prompt_dir(), "custom_%s.md" % name)
    if os.path.exists(p):
        return {"ok": False, "error": "同名提示词已存在：%s" % name}
    try:
        with open(p, "wb") as f:
            f.write(("<!-- 自定义提示词：%s -->\n\n" % name).encode("utf-8"))
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "id": "custom-" + name, "path": p}


def cmd_prompt_delete(pid):
    """删除一份自定义提示词（只允许删 custom- 前缀的）。"""
    if not pid.startswith("custom-"):
        return {"ok": False, "error": "内置提示词不能在这里删：%s" % pid}
    p, _label = _prompt_entry(pid)
    if not p or not os.path.isfile(p):
        return {"ok": False, "error": "文件不存在"}
    try:
        os.remove(p)
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "id": pid, "path": p}


# ───────────────── 指令提示词库（按 Agent × 分类管理 md） ─────────────────

# 指令提示词页的 Agent 列表：(key, 显示名, 目标目录名)。content/seats/<key>.md
# 与 targets/<目录>/ 下的 .md 都归到该 Agent 名下。
DOC_AGENTS = [
    ("codex", "ChatGPT", "codex"),
    ("claude", "Claude Code", "claude"),
    ("dsh", "DSH", "dsh"),
    ("workbuddy", "WorkBuddy", "workbuddy"),
    ("zcode", "ZCode (Z.ai)", "zcode"),
    ("hermes", "Hermes Agent", "hermes"),
]
_DOC_AGENT_MAP = {k: (label, d) for k, label, d in DOC_AGENTS}

# 各席位挂在客户端里的托管块内容源：文件 → 界面上的一句话。
# 这些不是「席位正文」，但照样参与注入（写进 CLAUDE.md / AGENTS.md / USER.md 的
# 标记块），所以也要在指令页露出来、也要接到注入开关上。
SEAT_MANAGED_SRC = {
    "codex": {"codex_agents_md.md": "AGENTS.md 托管内容源（Windows 约束）"},
    "claude": {"claude_md.md": "CLAUDE.md 托管内容源（工作区说明）"},
    "workbuddy": {"workbuddy_user_md.md": "USER.md 托管块内容源（Windows 约束）"},
}

# 分类顺序即界面 tab 顺序 —— 就是部署会写的四类文件，不再拆人格 / 席位源 / 路由叶子。
#   instruction 指令文件（部署到 prompts/<名字>.md）
#   agents      AGENTS.md 标记块
#   config      config.toml 指针
#   skill       SKILL.md（6 个父类目 + 80 片叶子）
#   custom      用户自建指令
DOC_CATS = [
    ("instruction", "指令文件"),
    ("agents", "AGENTS.md"),
    ("config", "config.toml"),
    ("skill", "SKILL.md"),
    ("custom", "自定义"),
]
_DOC_CAT_ORDER = {cid: i for i, (cid, _n) in enumerate(DOC_CATS)}
# 旧分类只给其它 Agent 兜底用（ChatGPT 页已经换成四类）。
_LEGACY_CAT_NAME = {
    "persona": "人格", "seat": "席位源", "unlock": "破甲", "managed": "托管文档",
    "route": "路由", "leaf": "路由叶子",
}
_DOC_CAT_NAME = dict(DOC_CATS)

# 生成产物：改源件重新生成，页面上标出来，别让人白改。
DOC_GENERATED = {
    "content/route-layer.md",
    "content/routes/leaves/INDEX.md",
}

# 允许读写的根 —— 指令库只碰 content/ 与 targets/，其它一律拒。
DOC_ROOTS = ("content", "targets")

# 引擎自动写的镜像 / 备份，不是可选的注入源 —— 指令页不列它们
# （用户在「部署目标」页的总览里看这些文件的实际内容与生效情况）。
DOC_MIRRORS = {
    "targets/codex/codex-unlock-instructions.md",
    "targets/codex/AGENTS-追加块.md",
    "targets/codex/AGENTS.md",
}

# 注入开关的状态文件：id（相对路径）→ false 表示不注入。
# 改名 / 移动源件是错的 —— 那会让引擎找不到源、生成脚本对不上；
# 开关只动这张表，真正的合成在 deploy 时按表拼装。
DOC_STATE_FILE = os.path.join(ROOT, "状态", "_doc_inject.json")

# 用户备注：id（相对路径）→ 一句话。存 状态/_doc_notes.json，不进 content/targets，
# 免得把 GUI 的元数据混进注入源件。
DOC_NOTES_FILE = os.path.join(ROOT, "状态", "_doc_notes.json")


def _doc_notes():
    """读备注表。文件损坏时先备份，再当空表 —— 不能因为解析失败就把用户备注冲掉。

    旧版按 `.md.off` 存过键，读的时候统一折回原文件名，否则改名方案留下的备注
    会"看起来消失了"。
    """
    try:
        with open(DOC_NOTES_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
    except ValueError:
        try:
            shutil.copyfile(DOC_NOTES_FILE, DOC_NOTES_FILE + ".bad-%d" % int(time.time()))
        except OSError:
            pass
        return {}
    except OSError:
        return {}
    if not isinstance(d, dict):
        return {}
    out = {}
    for k, v in d.items():
        out[_doc_base(k) if isinstance(k, str) else k] = v
    return out


def _doc_notes_save(d):
    """原子写：先写临时文件再替换，进程中途退出也不会留下半截 JSON。

    半截文件下次读会走 ValueError，用户所有备注就"重启后消失"了。
    """
    tmp = DOC_NOTES_FILE + ".tmp"
    try:
        os.makedirs(os.path.dirname(DOC_NOTES_FILE), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, DOC_NOTES_FILE)
        return True
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def _doc_skip(rel):
    """不是提示词、不该出现在指令页的文件：各目录下的「说明.md」等纯文档。"""
    name = os.path.basename(rel).lower()
    if name.startswith("说明") or name.startswith("readme"):
        return True
    return "/skills/" in "/" + rel


def _doc_rel(p):
    return os.path.relpath(os.path.abspath(p), ROOT).replace("\\", "/")


def _doc_cat(rel, agent):
    """按路径与文件名归类。顺序有意义：越具体的规则越靠前。"""
    base = os.path.basename(rel).lower()
    if re.fullmatch(r"custom_[a-z0-9_\-]+\.md", base):
        return "custom"
    if rel == "content/persona.md" or re.search(r"(^|/)persona\.md$", rel, re.I):
        return "persona"
    if rel == "content/route-layer.md":
        return "route"
    if rel.startswith("content/routes/leaves/"):
        return "leaf"
    if rel.startswith("content/routes/"):
        return "route"
    if rel.startswith("content/seats/"):
        return "managed" if re.search(r"_md\.md$", base) else "seat"
    if "/instructions/" in "/" + rel or re.search(r"unlock|argon|破甲|越狱", base):
        return "unlock"
    if re.search(r"agents|claude\.md|_md\.md", base):
        return "managed"
    return "instruction"


def _doc_allowed(path):
    """只允许 content/ 与 targets/ 下的 .md（含 .md.off）—— 挡住越界写。"""
    ap = os.path.abspath(path)
    root = os.path.abspath(ROOT)
    if os.path.commonpath([ap, root]) != root:
        return False
    rel = _doc_rel(ap).split("/")
    if not rel or rel[0] not in DOC_ROOTS:
        return False
    low = ap.lower()
    return low.endswith(".md") or low.endswith(".md.off")


def _doc_enabled(p):
    """文件存在且没被开关关掉。"""
    if not os.path.isfile(p):
        return False
    off = _doc_state().get(_doc_rel(p))
    return off is not False


def _doc_base(p):
    """兼容旧版 .off 改名方案：把 <x>.md.off 还原成 <x>.md。"""
    return p[:-4] if p.lower().endswith(".md.off") else p


def _doc_actual(p):
    """给定 <x>.md，返回盘上真实存在的那一份（兼容旧 .off 残留）。"""
    if os.path.isfile(p):
        return p
    off = p + ".off"
    return off if os.path.isfile(off) else ""


def _doc_state():
    """读开关表。缺文件 / 坏 JSON 一律当"全开"。"""
    try:
        with open(DOC_STATE_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _doc_state_save(d):
    """原子写：先落 .tmp 再 os.replace。

    非原子写在崩溃 / 被杀时会留下半截 JSON，读回一律当"全开" —— 用户刚关掉的
    文档会在重启后又出现"怎么都关不掉"。
    """
    try:
        os.makedirs(os.path.dirname(DOC_STATE_FILE), exist_ok=True)
        tmp = DOC_STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, DOC_STATE_FILE)
        return True
    except OSError:
        return False


def _doc_set_enabled(rel, on):
    d = _doc_state()
    key = _doc_rel(os.path.join(ROOT, _doc_base(rel)))
    if on:
        d.pop(key, None)
    else:
        d[key] = False
    return _doc_state_save(d)


# 注入动作的元信息：哪个席位最近一次换指令 / 关闭注入是什么时候。
# 只用于界面显示与排错，不参与注入判定。
DOC_META_FILE = os.path.join(ROOT, "状态", "_doc_meta.json")


def _doc_meta():
    try:
        with open(DOC_META_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _inject_meta_update(seat, last=""):
    """记一笔：某席位的注入动作做过什么、什么时候。失败不影响主流程。"""
    d = _doc_meta()
    d[seat] = {"last": last or "", "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        os.makedirs(os.path.dirname(DOC_META_FILE), exist_ok=True)
        with open(DOC_META_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True)
    except OSError:
        pass
    return d.get(seat, {})


def _read_text(p):
    with open(p, "rb") as f:
        return f.read().decode("utf-8-sig", "replace")


def _codex_deploy():
    """加载 engine/codex_deploy.py（ChatGPT 席位的四落点部署）。"""
    import importlib.util
    p = os.path.join(ENGINE, "codex_deploy.py")
    if not os.path.isfile(p):
        return None
    spec = importlib.util.spec_from_file_location("codex_deploy", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _wb_deploy():
    """加载 engine/workbuddy_deploy.py（WorkBuddy 席位的四落点部署）。"""
    import importlib.util
    p = os.path.join(ENGINE, "workbuddy_deploy.py")
    if not os.path.isfile(p):
        return None
    spec = importlib.util.spec_from_file_location("workbuddy_deploy", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _seat_deploy():
    """加载 engine/seats_deploy.py（Claude / Hermes / ZCode 的三落点部署）。"""
    import importlib.util
    p = os.path.join(ENGINE, "seats_deploy.py")
    if not os.path.isfile(p):
        return None
    spec = importlib.util.spec_from_file_location("seats_deploy", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_SEAT_DEPLOY_KEYS = ("claude", "hermes", "zcode")


def _doc_scan_seat(agent, label):
    """Claude / Hermes / ZCode 页 = 各自的三类落点（指令文件 / 记忆或指针 / SKILL.md）。"""
    sd = _seat_deploy()
    if sd is None:
        return {"ok": False, "error": "engine/seats_deploy.py 缺失"}
    spec = sd.SEATS.get(agent)
    if not spec:
        return {"ok": False, "error": "未知席位：%s" % agent}
    home = sd.home_of(agent)
    v = sd.verify(agent)
    st = sd.state(agent)
    notes = _doc_notes()
    out = []
    lib = sd.lib_dir(agent)
    active = ((st.get("pack") or sd.pack_name(agent))
              if st.get("pack_on") is not False else "")
    for fn in sd.lib_files(agent):
        p = (spec["seed"] if fn == os.path.basename(spec["seed"])
             else os.path.join(spec["lib"], fn))
        if not p or not os.path.isfile(p):
            continue
        is_active = fn == active
        out.append({
            "id": _doc_rel(p), "path": p, "name": fn, "cat": "instruction",
            "size": os.path.getsize(p), "exists": True,
            "enabled": is_active, "active": is_active, "deployable": True,
            "note": ("注入中 · 写入 %s" % os.path.basename(spec["primary"])) if is_active
                    else "拨右把它部署为 %s 的指令文件" % spec["label"],
            "labels": [_label("指令源", "info"),
                       _label("注入中" if is_active else "未部署",
                              "ok" if is_active else "muted")],
            "memo": notes.get(_doc_rel(p), ""),
        })
    # 主落点本身（指令文件写到哪里）
    prim = next((c for c in v["checks"] if c["role"] == "指令文件"), None)
    prim_p = (prim or {}).get("path") or os.path.join(home, spec["primary"])
    out.append({
        "id": "seat:%s/primary" % agent,
        "path": prim_p,
        "name": "%s · %s" % (os.path.basename(spec["primary"]),
                             "整份写入" if spec["primary_kind"] == "file" else "标记块"),
        "cat": "config",
        "title": "",
        "size": (prim or {}).get("bytes") or 0,
        "exists": bool(prim),
        "enabled": st.get("primary") is not False, "managed": True, "live": True,
        "note": ("正文来自 %s，部署时写进这里" % active) if active else "先在指令文件里选一份正文",
        "labels": [_label("落点", "info"),
                   _label("已启用" if st.get("primary") is not False else "已关闭",
                          "ok" if st.get("primary") is not False else "muted"),
                   _label("已就位" if (prim or {}).get("ok") else "待部署",
                          "ok" if (prim or {}).get("ok") else "muted")],
        "memo": notes.get("seat:%s/primary" % agent, ""),
    })
    if spec.get("memory"):
        mchk = next((c for c in v["checks"] if c["role"] == "记忆文件标记块"), None)
        mp = (mchk or {}).get("path") or os.path.join(home, spec["memory"])
        out.append({
            "id": "seat:%s/memory" % agent, "path": mp,
            "name": spec["memory"], "cat": "agents",
            "size": (mchk or {}).get("bytes") or 0, "exists": bool(mchk),
            "enabled": st.get("memory") is not False, "managed": True, "live": True,
            "note": "标记块合并，用户自己的内容保留",
            "labels": [_label("记忆文件", "info"),
                       _label("已启用" if st.get("memory") is not False else "已关闭",
                              "ok" if st.get("memory") is not False else "muted"),
                       _label("已就位" if (mchk or {}).get("ok") else "待部署",
                              "ok" if (mchk or {}).get("ok") else "muted")],
            "memo": notes.get("seat:%s/memory" % agent, ""),
        })
    if spec.get("settings"):
        schk = next((c for c in v["checks"] if c["role"] == "outputStyle 指针"), None)
        out.append({
            "id": "seat:%s/settings" % agent,
            "path": (schk or {}).get("path") or sd.settings_path(agent),
            "name": "outputStyle 指针", "cat": "config",
            "size": (schk or {}).get("bytes") or 0, "exists": bool(schk),
            "enabled": st.get("settings") is not False, "managed": True, "live": True,
            "note": "settings.json 里 outputStyle = pojia",
            "labels": [_label("配置指针", "info"),
                       _label("已启用" if st.get("settings") is not False else "已关闭",
                              "ok" if st.get("settings") is not False else "muted"),
                       _label("已就位" if (schk or {}).get("ok") else "待部署",
                              "ok" if (schk or {}).get("ok") else "muted")],
            "memo": notes.get("seat:%s/settings" % agent, ""),
        })
    try:
        routes = _routes_mod()
    except Exception:
        routes = None
    for sid, text in sd._skill_items(agent, all_items=True):
        p = os.path.join(home, "skills", sid, "SKILL.md")
        nid = "seat:%s/skill/%s" % (agent, sid)
        on = sd.skill_on(agent, sid)
        chk = next((c for c in v["checks"]
                    if c["role"] == "SKILL.md" and os.path.normcase(c["path"]) == os.path.normcase(p)),
                   {})
        leaf_p = os.path.join(ROOT, "content", "routes", "leaves", sid + ".md")
        meta = routes.leaf_meta(sid) if (routes and os.path.isfile(leaf_p)) else None
        parent_key = next((k for k, vv in (routes.CATEGORIES if routes else {}).items()
                           if vv[3] == sid), "")
        if meta:
            cn, cat_cn, kind = meta["name"], meta["category_name"], "小类"
        elif parent_key:
            _s, pname, _kw, _slug = routes.CATEGORIES[parent_key]
            cn, cat_cn, kind = pname, "类目", "父类目"
        else:
            cn, cat_cn, kind = sid, "SKILL.md", "技能"
        out.append({
            "id": nid, "path": p, "name": sid, "cat": "skill",
            "title": cn, "category": cat_cn,
            "size": len(text.encode("utf-8")), "exists": os.path.isfile(p),
            "enabled": on, "managed": True, "skill": True, "note": "",
            "labels": [_label(cat_cn, "info"), _label(kind, "muted"),
                       _label("已投放" if chk.get("ok") else "未投放",
                              "ok" if chk.get("ok") else "muted")],
            "memo": notes.get(nid, ""),
        })
    return _doc_finish(out, agent, label, home)


def _doc_scan(agent):
    """扫出某个 Agent 名下的文档。

    ChatGPT / WorkBuddy 页按各自落点分类；其它 Agent 本次不改，仍走旧扫描。
    """
    label, dirname = _DOC_AGENT_MAP.get(agent, (agent, agent))
    if agent == "codex":
        return _doc_scan_codex(label)
    if agent == "workbuddy":
        return _doc_scan_wb(label)
    if agent in _SEAT_DEPLOY_KEYS:
        return _doc_scan_seat(agent, label)
    out = []
    seen = set()

    def add(p, note=""):
        if not p.lower().endswith(".md"):
            return
        real = _doc_actual(p)
        if not real:
            return
        rel = _doc_rel(real)
        if rel in DOC_MIRRORS or _doc_skip(rel):
            return
        if rel in seen:
            return
        seen.add(rel)
        enabled = _doc_enabled(real)
        out.append({
            "id": rel,
            "path": os.path.abspath(real),
            "name": os.path.basename(p),
            "cat": _doc_cat(_doc_rel(p), agent),
            "size": os.path.getsize(real),
            "exists": True,
            "enabled": enabled,
            "generated": _doc_rel(p) in DOC_GENERATED or _doc_rel(p).startswith("content/routes/leaves/"),
            "note": note or ("" if enabled else "已停用 · 不参与合成，内容一个字节没动"),
        })

    add(os.path.join(CONTENT, "seats", agent + ".md"), "席位独立提示词源")
    for fn, n in SEAT_MANAGED_SRC.get(agent, {}).items():
        add(os.path.join(CONTENT, "seats", fn), n)
    add(os.path.join(ROOT, "targets", dirname, "persona.md"), "该席位目录下的人格副本")
    add(os.path.join(CONTENT, "persona.md"), "全部席位共用的人格源件")
    seats_dir = os.path.join(CONTENT, "seats")
    if os.path.isdir(seats_dir):
        for fn in sorted(os.listdir(seats_dir)):
            m = re.fullmatch(r"(custom_[A-Za-z0-9_\-]+\.md)(\.off)?", fn, re.I)
            if m:
                add(os.path.join(seats_dir, m.group(1)), "用户自建指令")
    tdir = os.path.join(ROOT, "targets", dirname) if dirname else ""
    if tdir and os.path.isdir(tdir):
        for dirpath, dirnames, filenames in os.walk(tdir):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git")]
            for fn in sorted(filenames):
                if fn.lower().endswith(".md.off"):
                    add(os.path.join(dirpath, fn[:-7]))
                else:
                    add(os.path.join(dirpath, fn))
    return _doc_finish(out, agent, label, "")


def _routes_mod():
    """加载 engine/pojia_routes.py（叶子元信息 / 类目名）。"""
    import importlib.util
    p = os.path.join(ENGINE, "pojia_routes.py")
    if "pojia_routes" in sys.modules:
        return sys.modules["pojia_routes"]
    spec = importlib.util.spec_from_file_location("pojia_routes", p)
    m = importlib.util.module_from_spec(spec)
    sys.modules["pojia_routes"] = m
    spec.loader.exec_module(m)
    return m


def _label(tag, tone=""):
    """卡片上的标签：tag 文字 + 语气（ok/warn/info/muted）。"""
    return {"tag": tag, "tone": tone or ""}


def _doc_scan_codex(label):
    """ChatGPT 页 = 部署会写的四类落点，不再拆人格 / 席位源 / 路由叶子。"""
    ad = _codex_deploy()
    if ad is None:
        return {"ok": False, "error": "engine/codex_deploy.py 缺失"}
    home = ad.home_of("codex")
    state = _doc_state()
    notes = _doc_notes()
    v = ad.verify("codex")
    by_path = {c["path"]: c for c in v["checks"]}
    st = ad.state()
    out = []

    # 1) 指令文件：targets/codex/instructions/*.md 里选一份部署
    lib = os.path.join(ROOT, "targets", "codex", "instructions")
    active = ((st.get("pack") or ad.pack_name())
              if st.get("pack_on") is not False else "")

    for fn in (sorted(os.listdir(lib)) if os.path.isdir(lib) else []):
        if not fn.lower().endswith(".md"):
            continue
        p = os.path.join(lib, fn)
        is_active = fn == active
        out.append({
            "id": _doc_rel(p), "path": p, "name": fn, "cat": "instruction",
            "size": os.path.getsize(p), "exists": True,
            "enabled": is_active, "active": is_active, "deployable": True,
            "note": ("注入中 · 部署到 ~/.codex/prompts/%s" % fn) if is_active
                    else "拨右把它部署为 ChatGPT 的指令文件",
            "labels": [_label("指令源", "info"),
                       _label("注入中" if is_active else "未部署", "ok" if is_active else "muted")],
            "memo": notes.get(_doc_rel(p), ""),
        })

    # 2/3) AGENTS.md 标记块 + config.toml 指针：开关存部署状态表
    for cat, name, role, nid, flag in (
            ("agents", "AGENTS.md", "AGENTS.md 标记块", "codex:agents", "agents"),
            ("config", "config.toml", "config.toml 指针", "codex:config", "config")):
        hit = next((c for c in v["checks"] if c["role"] == role), None)
        p = (hit or {}).get("path") or os.path.join(home, name)
        nbytes = 0
        try:
            nbytes = os.path.getsize(p) if os.path.isfile(p) else 0
        except OSError:
            nbytes = 0
        out.append({
            "id": nid, "path": p, "name": name, "cat": cat,
            "size": nbytes, "exists": os.path.isfile(p),
            "enabled": st.get(flag) is not False, "managed": True, "live": True,
            # 描述里不再重复标签已经说过的「本机文件」——卡片上会连着出现两遍
            "note": ("可点开看内容" if os.path.isfile(p) else "本机还没有这个文件")
                    + (" · 部署已就位" if (hit or {}).get("ok") else " · 尚未写入"),
            "labels": [_label("本机文件", "info"),
                       _label("托管块" if cat == "agents" else "配置指针", "muted"),
                       _label("已启用" if st.get(flag) is not False else "已关闭",
                              "ok" if st.get(flag) is not False else "muted")],
            "memo": notes.get(nid, ""),
        })

    # 4) SKILL.md：父类目 + 叶子，逐份开关（键沿用 leaves/<id>.md）
    try:
        items = ad._skill_items(all_items=True)
    except Exception:
        items = []
    try:
        routes = _routes_mod()
    except Exception:
        routes = None
    for sid, body in items:
        p = os.path.join(home, "skills", sid, "SKILL.md")
        nid = _codex_skill_key(sid)
        on = _codex_skill_on(sid)
        c = by_path.get(p) or {}
        leaf_p = os.path.join(ROOT, "content", "routes", "leaves", sid + ".md")
        meta = routes.leaf_meta(sid) if (routes and os.path.isfile(leaf_p)) else None
        parent_key = next((k for k, v in (routes.CATEGORIES if routes else {}).items()
                           if v[3] == sid), "")
        if meta:
            cn = meta["name"]                  # 中文小类名：接口对接 / SQL 注入 …
            kw = meta["keywords"]
            cat_cn = meta["category_name"]
            kind, note = "小类", "命中「%s」时走这一份" % kw
            labels = [_label(cat_cn, "info"), _label(kind, "muted"),
                      _label("已投放" if c.get("ok") else "未投放",
                             "ok" if c.get("ok") else "muted")]
        elif parent_key:
            stamp, pname, pkw, _slug = routes.CATEGORIES[parent_key]
            cn, kw, cat_cn = pname, pkw, "类目"
            kind = "父类目"
            note = "类目三拍 · 下辖全部小类的入口"
            labels = [_label("类目", "info"), _label(pname, "muted"),
                      _label("已投放" if c.get("ok") else "未投放",
                             "ok" if c.get("ok") else "muted")]
        else:
            cn, kw, cat_cn, kind = sid, "", "SKILL.md", "技能"
            note = "已投放" if c.get("ok") else "尚未投放"
            labels = [_label("SKILL.md", "info"),
                      _label("已投放" if c.get("ok") else "未投放",
                             "ok" if c.get("ok") else "muted")]
        out.append({
            "id": nid, "path": p, "name": sid, "cat": "skill",
            "title": cn, "keywords": kw, "category": cat_cn,
            "size": len(body.encode("utf-8")), "exists": os.path.isfile(p),
            "enabled": on, "managed": True, "skill": True,
            "note": note,
            "labels": labels,
            "memo": notes.get(nid, ""),
        })

    return _doc_finish(out, "codex", label, home)


def _doc_scan_wb(label):
    """WorkBuddy 页 = 部署会写的四类落点（与 ChatGPT 同构，正文各自独立）。"""
    wd = _wb_deploy()
    if wd is None:
        return {"ok": False, "error": "engine/workbuddy_deploy.py 缺失"}
    home = wd.home_of()
    state = _doc_state()
    notes = _doc_notes()
    v = wd.verify("wb")
    by_path = {os.path.normcase(c["path"]): c for c in v["checks"]}
    st = wd.state()
    out = []

    # 1) 指令文件：targets/workbuddy/instructions/*.md 里选一份部署
    lib = wd.INSTR_LIB
    active = ((st.get("pack") or wd.pack_name())
              if st.get("pack_on") is not False else "")
    for fn in (sorted(os.listdir(lib)) if os.path.isdir(lib) else []):
        if not fn.lower().endswith(".md"):
            continue
        p = os.path.join(lib, fn)
        is_active = fn == active
        out.append({
            "id": _doc_rel(p), "path": p, "name": fn, "cat": "instruction",
            "size": os.path.getsize(p), "exists": True,
            "enabled": is_active, "active": is_active, "deployable": True,
            "note": ("注入中 · 写入 WorkBuddy 自定义指令与 32 份模板政策块" if is_active
                     else "拨右把它部署为 WorkBuddy 的指令源"),
            "labels": [_label("指令源", "info"),
                       _label("注入中" if is_active else "未部署", "ok" if is_active else "muted")],
            "memo": notes.get(_doc_rel(p), ""),
        })

    # 2) 自定义指令：WorkBuddy 的「注入正文落点」= ChatGPT 的指令文件。
    #    一行对应一个可写的托管文件（项目里一份，部署时整份写进客户端的
    #    personalization.customPrompt 与 32 份模板政策块）。
    body, _h = wd.body_with_mark(active) if active else ("", "")
    custom_checks = [c for c in v["checks"] if c["role"] == "自定义指令"]
    out.append({
        "id": "wb:custom",
        "path": os.path.join(home, "settings.json"),
        "name": (active or "（未选指令源）"),
        "cat": "config",
        "title": "自定义指令",
        "size": len(body.encode("utf-8")),
        "exists": bool(custom_checks),
        "enabled": st.get("custom") is not False,
        "managed": True, "live": True,
        "note": ("正文来自 %s，部署时写入客户端自定义指令" % active) if active
                else "先在指令文件里选一份正文",
        "labels": [_label("正文落点", "info"),
                   _label("已启用" if st.get("custom") is not False else "已关闭",
                          "ok" if st.get("custom") is not False else "muted"),
                   _label("%d 处已就位" % len(custom_checks), "ok" if custom_checks else "muted")],
        "memo": notes.get("wb:custom", ""),
    })
    # 3) 剩下的辅助落点：模板政策块 / 身份层。它们不承载独立正文，
    #    只在总览里按组显示（细项进补丁总览），不再占指令页两行空壳。
    for cat, name, role, nid, flag in (
            ("agents", "身份层", "身份层", "wb:identity", "identity"),
            ("config", "模板政策块", "模板政策块", "wb:templates", "templates")):
        checks = [c for c in v["checks"] if c["role"] == role]
        ok_n = sum(1 for c in checks if c["ok"])
        on = st.get(flag) is not False
        out.append({
            "id": nid,
            "path": (checks[0]["path"] if checks else home),
            "name": name, "cat": cat,
            "size": sum(c.get("bytes") or 0 for c in checks),
            "exists": bool(checks),
            "enabled": on, "managed": True, "live": True,
            "note": ("%d / %d 处已就位" % (ok_n, len(checks))) if checks else "未探测到落点",
            "labels": [_label("落点", "info"),
                       _label("已启用" if on else "已关闭", "ok" if on else "muted"),
                       _label("已就位" if ok_n == len(checks) and checks else "待部署",
                              "ok" if ok_n == len(checks) and checks else "muted")],
            "memo": notes.get(nid, ""),
        })

    # 3) SKILL.md：父类目 + 叶子
    try:
        items = wd._skill_items(all_items=True)
    except Exception:
        items = []
    try:
        routes = _routes_mod()
    except Exception:
        routes = None
    for sid, text in items:
        p = os.path.join(home, "skills", sid, "SKILL.md")
        nid = "wb:skill/%s" % sid
        on = wd.skill_on(sid)
        c = by_path.get(os.path.normcase(p)) or {}
        leaf_p = os.path.join(ROOT, "content", "routes", "leaves", sid + ".md")
        meta = routes.leaf_meta(sid) if (routes and os.path.isfile(leaf_p)) else None
        parent_key = next((k for k, vv in (routes.CATEGORIES if routes else {}).items()
                           if vv[3] == sid), "")
        if meta:
            cn, kw, cat_cn, kind = meta["name"], meta["keywords"], meta["category_name"], "小类"
            note = "命中「%s」时走这一份" % kw
        elif parent_key:
            _stamp, pname, pkw, _slug = routes.CATEGORIES[parent_key]
            cn, kw, cat_cn, kind = pname, pkw, "类目", "父类目"
            note = "类目三拍 · 下辖全部小类的入口"
        else:
            cn, kw, cat_cn, kind = sid, "", "SKILL.md", "技能"
            note = ""
        out.append({
            "id": nid, "path": p, "name": sid, "cat": "skill",
            "title": cn, "keywords": kw, "category": cat_cn,
            "size": len(text.encode("utf-8")), "exists": os.path.isfile(p),
            "enabled": on, "managed": True, "skill": True,
            "note": note,
            "labels": [_label(cat_cn, "info"), _label(kind, "muted"),
                       _label("已投放" if c.get("ok") else "未投放",
                              "ok" if c.get("ok") else "muted")],
            "memo": notes.get(nid, ""),
        })

    return _doc_finish(out, "workbuddy", label, home)


def _doc_finish(out, agent, label, home):
    out.sort(key=lambda it: (_DOC_CAT_ORDER.get(it["cat"], 99), it["name"].lower()))
    notes = _doc_notes()

    def memo_of(it):
        """按 id 找备注；再按旧键兜底（codex:skill/<id> ↔ leaves/<id>.md）。"""
        iid = it.get("id") or ""
        if not iid:
            return ""
        if notes.get(iid):
            return notes[iid]
        sid = ""
        if iid.startswith("content/routes/leaves/"):
            sid = os.path.basename(iid)[:-3]
        elif iid.startswith("codex:skill/"):
            sid = iid[len("codex:skill/"):]
        if sid:
            for alt in ("codex:skill/" + sid, "content/routes/leaves/%s.md" % sid):
                if notes.get(alt):
                    return notes[alt]
        return ""

    for it in out:
        if it.get("memo"):
            continue
        it["memo"] = memo_of(it)
    counts = {}
    cat_label = {"persona": "人格", "seat": "席位源", "unlock": "破甲",
                 "managed": "托管文档", "route": "路由", "leaf": "路由叶子",
                 "instruction": "指令", "custom": "自定义"}
    for it in out:
        if not it.get("labels"):
            it["labels"] = [{"tag": cat_label.get(it["cat"], "文档"), "tone": "info"},
                            {"tag": "已启用" if it["enabled"] else "已停用",
                             "tone": "ok" if it["enabled"] else "muted"}]
        counts[it["cat"]] = counts.get(it["cat"], 0) + 1
    cats = [{"id": cid, "name": _DOC_CAT_NAME[cid], "count": counts[cid]}
            for cid, _n in DOC_CATS if counts.get(cid)]
    # 其它 Agent 仍用旧分类（人格 / 席位源 / 路由 …）—— 没登记的也要给个 tab，
    # 不然这些文档有数据却一个标签都不出现。
    for cid, n in counts.items():
        if cid not in _DOC_CAT_NAME:
            cats.append({"id": cid, "name": _LEGACY_CAT_NAME.get(cid, cid), "count": n})
    on = sum(1 for it in out if it["enabled"])
    return {"ok": True, "agent": agent, "label": label, "root": ROOT,
            "categories": cats, "items": out, "home": home,
            "onCount": on, "offCount": len(out) - on,
            "official": bool(out) and on == 0}


def _inside(p):
    """路径是否在项目根下（部署位在 ~/.codex，跨盘符不能 relpath）。"""
    try:
        ap = os.path.abspath(p)
        root = os.path.abspath(ROOT)
        return os.path.commonpath([ap, root]) == root
    except ValueError:
        return False


def cmd_doc_memo(rel, memo):
    """给某份文档写一句备注（存 状态/_doc_notes.json，不碰源件）。

    ChatGPT 页的 id（codex:agents / codex:config / codex:skill/<id>）直接用 id 当键 ——
    备注是本地元数据，不需要文件落在项目里；本机落点（~/.codex）同理。
    """
    text = (memo or "").strip()
    if len(text) > 200:
        return {"ok": False, "error": "备注最多 200 字"}
    if rel.startswith("codex:"):
        key = rel
    else:
        p = os.path.abspath(os.path.join(ROOT, _doc_base(rel)))
        if not _doc_allowed(p):
            # 本机落点（~/.codex/AGENTS.md 之类）：只存备注，不碰文件
            if not os.path.isfile(p):
                return {"ok": False, "error": "不允许操作：%s" % rel}
            key = _doc_rel(p) if _inside(p) else rel
        else:
            if not _doc_actual(p):
                return {"ok": False, "error": "文件不存在：%s" % rel}
            key = _doc_rel(p)
    notes = _doc_notes()
    if text:
        notes[key] = text
    else:
        notes.pop(key, None)
    if not _doc_notes_save(notes):
        return {"ok": False, "error": "备注写盘失败"}
    return {"ok": True, "id": key, "memo": text}


def _codex_skill_key(sid):
    """SKILL 开关的键：有同名叶子就用叶子路径，否则用 codex:skill/<id>。"""
    leaf = "content/routes/leaves/%s.md" % sid
    if os.path.isfile(os.path.join(ROOT, leaf)):
        return leaf
    return "codex:skill/%s" % sid


def _seat_toggle(sd, seat, flag, want):
    st = sd.state(seat)
    st[flag] = bool(want)
    sd.save_state(seat, st)
    return {"ok": True, "id": "seat:%s/%s" % (seat, flag), "enabled": bool(want),
            "note": "已%s · 下次部署时%s写这一路" % ("启用" if want else "停用",
                                                  "" if want else "不")}


def _seat_skill_toggle(sd, seat, sid, want):
    leaf = os.path.join(ROOT, "content", "routes", "leaves", sid + ".md")
    if os.path.isfile(leaf):
        ok = _doc_set_enabled(_doc_rel(leaf), want)
    else:
        st = sd.state(seat)
        st["seat:skill/%s" % sid] = bool(want)
        sd.save_state(seat, st)
        ok = True
    return {"ok": ok, "id": "seat:%s/skill/%s" % (seat, sid), "enabled": bool(want),
            "note": ("已启用 · 下次部署会重新投放" if want
                     else "已停用 · 下次部署会撤下这一份 skill")}


def _wb_toggle(doc_id, want):
    """WorkBuddy 页的三类开关：自定义指令 / 模板政策块 / 身份层，SKILL 走通用开关表。"""
    wd = _wb_deploy()
    if wd is None:
        return {"ok": False, "error": "engine/workbuddy_deploy.py 缺失"}
    flag_map = {"wb:custom": "custom", "wb:templates": "templates", "wb:identity": "identity"}
    if doc_id in flag_map:
        flag = flag_map[doc_id]
        st = wd.state()
        st[flag] = bool(want)
        wd.save_state(st)
        return {"ok": True, "id": doc_id, "enabled": bool(want),
                "note": "已%s · 下次部署时%s写这一路" % (
                    "启用" if want else "停用", "" if want else "不")}
    if doc_id.startswith("wb:skill/"):
        sid = doc_id[len("wb:skill/"):]
        leaf = os.path.join(ROOT, "content", "routes", "leaves", sid + ".md")
        if os.path.isfile(leaf):
            ok = _doc_set_enabled(_doc_rel(leaf), want)
        else:
            st = wd.state()
            st["wb:skill/%s" % sid] = bool(want)
            wd.save_state(st)
            ok = True
        return {"ok": ok, "id": doc_id, "enabled": bool(want),
                "note": ("已启用 · 下次部署会重新投放" if want
                         else "已停用 · 下次部署会撤下这一份 skill")}
    return {"ok": False, "error": "未知开关：%s" % doc_id}


def _seat_read_landing(doc_id):
    """seat 页落点的只读展示：说清楚这里是什么、现在写了什么。"""
    sd = _seat_deploy()
    if sd is None:
        return {"ok": False, "error": "engine/seats_deploy.py 缺失"}
    seat = doc_id[len("seat:"):].split("/", 1)[0]
    if seat not in _SEAT_DEPLOY_KEYS:
        return {"ok": False, "error": "未知席位"}
    spec = sd.SEATS[seat]
    home = sd.home_of(seat)
    rest = doc_id[len("seat:"):].partition("/")[2]
    if rest == "primary":
        p = os.path.join(home, spec["primary"])
        txt = _read_text(p) if os.path.isfile(p) else ""
        lines = ["# 指令文件落点（只读）", "",
                 "%s 的主注入落点是 `%s`（%s）。" % (
                     spec["label"], spec["primary"],
                     "整份写入" if spec["primary_kind"] == "file" else "标记块合并"),
                 "正文本身在「指令文件」页里编辑；部署时写进这里。", ""]
        if txt:
            lines += ["## 当前内容（%d 字符）" % len(txt), "", txt]
        else:
            lines.append("（当前没有写入）")
        return {"ok": True, "id": doc_id, "path": p, "content": "\n".join(lines),
                "editable": False, "live": True,
                "hint": "落点只读；正文在指令文件里改"}
    if rest == "memory" and spec.get("memory"):
        p = os.path.join(home, spec["memory"])
        txt = _read_text(p) if os.path.isfile(p) else ""
        lines = ["# 记忆文件（只读）", "",
                 "`%s` 里我们只写一个标记块，用户自己的内容原样保留。" % spec["memory"],
                 "下面显示整个文件。", "",
                 "## 当前内容（%d 字符）" % len(txt), "", txt or "（文件不存在）"]
        return {"ok": True, "id": doc_id, "path": p, "content": "\n".join(lines),
                "editable": False, "live": True, "hint": "落点只读"}
    if rest == "settings":
        p = sd.settings_path(seat)
        txt = _read_text(p) if os.path.isfile(p) else ""
        lines = ["# outputStyle 指针（只读）", "",
                 "部署时把 `settings.json` 的 `outputStyle` 设为 `pojia`，"
                 "指向 `output-styles/pojia.md`。", "",
                 "## 当前内容", "", txt or "（文件不存在）"]
        return {"ok": True, "id": doc_id, "path": p, "content": "\n".join(lines),
                "editable": False, "live": True, "hint": "落点只读"}
    return {"ok": False, "error": "未知落点"}


def _wb_edit_path(doc_id):
    """WorkBuddy 页落点的编辑目标：真正承载正文的才可写，落点本身只读展示。"""
    wd = _wb_deploy()
    home = wd.home_of() if wd else os.path.join(os.path.expanduser("~"), ".workbuddy")
    if doc_id.startswith("seat:"):
        # seat 页的落点都是「只读展示」：正文在指令文件里改
        seat = doc_id[len("seat:"):].split("/", 1)[0]
        sd = _seat_deploy()
        if sd is None or seat not in _SEAT_DEPLOY_KEYS:
            return "", "未知席位"
        spec = sd.SEATS[seat]
        shome = sd.home_of(seat)
        rest = doc_id[len("seat:"):].partition("/")[2]
        if rest.startswith("skill/"):
            sid = rest[len("skill/"):]
            leaf = os.path.join(ROOT, "content", "routes", "leaves", sid + ".md")
            if os.path.isfile(leaf):
                return leaf, "SKILL.md 正文源（部署时投放到 <home>/skills/）"
            return "", "父类目内容由 content/routes/ 生成"
        return os.path.join(shome, spec["primary"]), "落点（只读展示）"
    if doc_id.startswith("wb:skill/"):
        sid = doc_id[len("wb:skill/"):]
        leaf = os.path.join(ROOT, "content", "routes", "leaves", sid + ".md")
        if os.path.isfile(leaf):
            return leaf, "SKILL.md 正文源（部署时投放到 <data>/skills/）"
        return "", "父类目内容由 content/routes/ 生成"
    if doc_id == "wb:custom":
        return os.path.join(home, "settings.json"), "客户端里的注入落点（只读）"
    if doc_id == "wb:templates":
        return "", "模板政策块是安装目录里的厂商文件（只读）"
    if doc_id == "wb:identity":
        return os.path.join(home, "SOUL.md"), "本机 SOUL.md（部署时整段写入）"
    return "", ""


def _wb_read_landing(doc_id):
    """落点类文档的只读展示：给出「这里是干什么的、现在写了什么」，不是裸 JSON。"""
    wd = _wb_deploy()
    if wd is None:
        return {"ok": False, "error": "engine/workbuddy_deploy.py 缺失"}
    home = wd.home_of()
    if doc_id == "wb:custom":
        lines = ["# 自定义指令落点（只读）", "",
                 "WorkBuddy 通过 settings.json / app/app-config.json 的 "
                 "personalization.customPrompt 接收注入正文。",
                 "正文本身在「指令文件」页里编辑；下面显示实际写进去的那一份。", ""]
        shown = False
        for sp in wd._custom_spots(home):
            try:
                doc = json.loads(wd._read(sp))
                cur = ((doc.get("personalization") or {}).get("customPrompt") or "")
            except ValueError:
                cur = ""
            if not cur:
                lines.append("## %s — 未写入" % os.path.relpath(sp, home))
                continue
            if shown:
                lines.append("## %s — 同内容（%d 字符）" % (os.path.relpath(sp, home), len(cur)))
                continue
            shown = True
            lines += ["## %s（%d 字符）" % (os.path.relpath(sp, home), len(cur)), "", cur, ""]
        if not shown:
            lines.append("（当前没有写入自定义指令）")
        return {"ok": True, "id": doc_id, "path": os.path.join(home, "settings.json"),
                "content": "\n".join(lines), "editable": False, "live": True,
                "hint": "这是落点说明与已写入内容；正文请在「指令文件」里改"}
    if doc_id == "wb:templates":
        files = wd.policy_files()
        lines = ["# 模板政策块落点（只读）", "",
                 "WorkBuddy 安装目录里有 %d 份提示词模板带 <content_policy> 块：" % len(files),
                 "部署时把同一份指令正文整块替换进去。正文在「指令文件」里编辑。", ""]
        for p in files:
            lines.append("- " + p)
        return {"ok": True, "id": doc_id, "path": os.path.dirname(files[0]) if files else home,
                "content": "\n".join(lines), "editable": False, "live": True,
                "hint": "厂商文件，只读；正文在指令文件里改，部署时整块替换"}
    return {"ok": False, "error": "未知落点"}


def _codex_toggle(doc_id, want):
    """ChatGPT 页的三类开关：AGENTS.md / config.toml / SKILL.md。

    AGENTS.md 与 config.toml 记在 状态/_codex_deploy.json（codex_deploy 的 opts）；
    SKILL.md 复用通用开关表（叶子键 content/routes/leaves/<id>.md）。
    """
    ad = _codex_deploy()
    if ad is None:
        return {"ok": False, "error": "engine/codex_deploy.py 缺失"}
    if doc_id in ("codex:agents", "codex:config"):
        flag = "agents" if doc_id == "codex:agents" else "config"
        st = ad.state()
        st[flag] = bool(want)
        ad.save_state(st)
        return {"ok": True, "id": doc_id, "enabled": bool(want),
                "note": "已%s · 下次部署时%s写这一路" % (
                    "启用" if want else "停用", "" if want else "不")}
    if doc_id.startswith("codex:skill/"):
        sid = doc_id[len("codex:skill/"):]
        key = _codex_skill_key(sid)
        if key.startswith("codex:"):
            st = ad.state()
            st[key] = bool(want)
            ad.save_state(st)
            return {"ok": True, "id": doc_id, "enabled": bool(want),
                    "note": "已%s · 下次部署后生效" % ("启用" if want else "停用")}
        ok = _doc_set_enabled(key, want)
        return {"ok": ok, "id": doc_id, "enabled": bool(want),
                "note": ("已启用 · 下次部署会重新投放" if want
                         else "已停用 · 下次部署会撤下这一份 skill")}
    p = os.path.abspath(os.path.join(ROOT, _doc_base(doc_id)))
    if not _doc_allowed(p):
        return {"ok": False, "error": "不允许操作：%s" % doc_id}
    ok = _doc_set_enabled(_doc_rel(p), want)
    return {"ok": ok, "id": doc_id, "enabled": bool(want),
            "note": ("已启用 · 下次部署会重新投放" if want else "已停用 · 下次部署会撤下这一份 skill")}


def _codex_skill_on(sid):
    ad = _codex_deploy()
    key = _codex_skill_key(sid)
    if key.startswith("codex:"):
        st = ad.state() if ad else {}
        return st.get(key) is not False
    return _doc_enabled(os.path.join(ROOT, key))


def cmd_doc_toggle(rel):
    """每个文档的注入开关：写状态表，不动盘上的源件。

    关闭只是把这份文档从"合成清单"里摘掉，源文件一个字节不改 ——
    生成脚本、引擎按路径找源件的地方都不会对不上。改完实时生效于下一次部署。
    """
    if rel.startswith("seat:"):
        # seat:<席位>/primary|memory|settings|skill/<id>
        body = rel[len("seat:"):]
        seat, _, rest = body.partition("/")
        sd = _seat_deploy()
        if sd is None or seat not in _SEAT_DEPLOY_KEYS:
            return {"ok": False, "error": "未知席位：%s" % rel}
        st = sd.state(seat)
        if rest == "primary":
            return _seat_toggle(sd, seat, "primary", st.get("primary") is False)
        if rest == "memory":
            return _seat_toggle(sd, seat, "memory", st.get("memory") is False)
        if rest == "settings":
            return _seat_toggle(sd, seat, "settings", st.get("settings") is False)
        if rest.startswith("skill/"):
            sid = rest[len("skill/"):]
            leaf = os.path.join(ROOT, "content", "routes", "leaves", sid + ".md")
            if os.path.isfile(leaf):
                return _seat_skill_toggle(sd, seat, sid, not _doc_enabled(leaf))
            return _seat_skill_toggle(sd, seat, sid, st.get("seat:skill/%s" % sid) is False)
        return {"ok": False, "error": "未知开关：%s" % rel}
    if rel.startswith("wb:"):
        st = _wb_deploy().state() if _wb_deploy() else {}
        if rel in ("wb:custom", "wb:templates", "wb:identity"):
            flag = {"wb:custom": "custom", "wb:templates": "templates",
                    "wb:identity": "identity"}[rel]
            return _wb_toggle(rel, st.get(flag) is False)
        if rel.startswith("wb:skill/"):
            sid = rel[len("wb:skill/"):]
            leaf = os.path.join(ROOT, "content", "routes", "leaves", sid + ".md")
            want = (not _doc_enabled(leaf)) if os.path.isfile(leaf) \
                else (st.get(rel) is False)
            return _wb_toggle(rel, want)
        return {"ok": False, "error": "未知开关：%s" % rel}
    if rel.startswith("codex:"):
        if rel in ("codex:agents", "codex:config"):
            ad = _codex_deploy()
            st = ad.state() if ad else {}
            flag = "agents" if rel == "codex:agents" else "config"
            return _codex_toggle(rel, st.get(flag) is False)
        if rel.startswith("codex:skill/"):
            sid = rel[len("codex:skill/"):]
            return _codex_toggle(rel, not _codex_skill_on(sid))
        return {"ok": False, "error": "未知开关：%s" % rel}
    p = os.path.abspath(os.path.join(ROOT, _doc_base(rel)))
    if not _doc_allowed(p):
        return {"ok": False, "error": "不允许操作：%s" % rel}
    base_rel = _doc_rel(p)
    if base_rel == "targets/codex/codex-unlock-instructions.md":
        return {"ok": False, "error": "这是部署快照，不能停用：它跟着实际注入内容自动同步"}
    if not _doc_actual(p):
        return {"ok": False, "error": "文件不存在：%s" % base_rel}
    was_on = _doc_enabled(p)
    if not _doc_set_enabled(base_rel, not was_on):
        return {"ok": False, "error": "开关状态写盘失败"}
    note = "已启用 · 重新部署后生效" if not was_on else "已停用 · 重新部署后生效"
    if base_rel in DOC_GENERATED:
        note = ("已启用 · 下次生成按钮重新生成并部署后生效" if not was_on
                else "已停用 · 引擎改用内置兜底骨架，不属于生成产物、可随拨随生效")
    if base_rel.startswith("content/routes/leaves/"):
        note = ("已启用 · 重新部署后 skill 会重新投放" if not was_on
                else "已停用 · 下次部署会撤下这一份 skill")
    return {"ok": True, "id": base_rel, "path": p,
            "enabled": not was_on, "note": note}


def cmd_doc_toggle_many(items, on):
    """批量开关：一次桥调用写完，界面只等一趟。

    items = [{"id": 相对路径, "on": true/false}]；单项失败不影响其余，
    返回 done / failed 两个列表。
    """
    if not isinstance(items, list):
        return {"ok": False, "error": "items 必须是数组"}
    done, failed = [], []
    for it in items:
        rel = (it or {}).get("id") if isinstance(it, dict) else it
        want = bool((it or {}).get("on", on)) if isinstance(it, dict) else bool(on)
        if isinstance(rel, str) and rel.startswith("codex:"):
            if rel.startswith("codex:skill/"):
                rel = _codex_skill_key(rel[len("codex:skill/"):])
                if not rel.startswith("codex:"):
                    # 折成叶子路径后走通用表，别再按 codex: 处理
                    p2 = os.path.abspath(os.path.join(ROOT, rel))
                    ok2 = _doc_set_enabled(rel, want) if _doc_allowed(p2) else False
                    (done if ok2 else failed).append(
                        {"id": rel, "enabled": want} if ok2
                        else {"id": rel, "error": "写盘失败"})
                    continue
            r = _codex_toggle(rel, want)
            (done if r.get("ok") else failed).append(
                {"id": rel, "enabled": want} if r.get("ok")
                else {"id": rel, "error": r.get("error") or "失败"})
            continue
        p = os.path.abspath(os.path.join(ROOT, _doc_base(rel or "")))
        if not rel or not _doc_allowed(p):
            failed.append({"id": rel, "error": "不允许操作"})
            continue
        base_rel = _doc_rel(p)
        if base_rel == "targets/codex/codex-unlock-instructions.md":
            failed.append({"id": base_rel, "error": "部署快照不可停用"})
            continue
        if not _doc_actual(p):
            failed.append({"id": base_rel, "error": "文件不存在"})
            continue
        if not _doc_set_enabled(base_rel, want):
            failed.append({"id": base_rel, "error": "写盘失败"})
            continue
        done.append({"id": base_rel, "enabled": want})
    return {"ok": True, "done": done, "failed": failed,
            "onCount": sum(1 for d in done if d["enabled"]),
            "offCount": sum(1 for d in done if not d["enabled"])}


def cmd_docs(agent):
    return _doc_scan(agent if agent in _DOC_AGENT_MAP else "codex")


def _doc_scan_all():
    """全库汇总（保留给命令行用，界面不再提供入口）。"""
    out = []
    seen = set()

    def add(p, note=""):
        if not p.lower().endswith(".md"):
            return
        real = _doc_actual(p)
        if not real:
            return
        rel = _doc_rel(real)
        if rel in DOC_MIRRORS or _doc_skip(rel):
            return
        if rel in seen:
            return
        seen.add(rel)
        enabled = _doc_enabled(real)
        out.append({
            "id": rel, "path": os.path.abspath(real), "name": os.path.basename(p),
            "cat": _doc_cat(_doc_rel(p), ""), "size": os.path.getsize(real), "exists": True,
            "enabled": enabled,
            "generated": _doc_rel(p) in DOC_GENERATED or _doc_rel(p).startswith("content/routes/leaves/"),
            "note": note or ("" if enabled else "已停用 · 不参与合成，内容一个字节没动"),
        })

    add(os.path.join(CONTENT, "persona.md"), "全部席位共用的人格源件")
    seats_dir = os.path.join(CONTENT, "seats")
    if os.path.isdir(seats_dir):
        for fn in sorted(os.listdir(seats_dir)):
            m = re.fullmatch(r"(.+\.md)(\.off)?", fn, re.I)
            if not m:
                continue
            add(os.path.join(seats_dir, m.group(1)),
                "用户自建指令" if m.group(1).startswith("custom_") else "席位 / 托管源")
    for key, _label, dirname in DOC_AGENTS:
        tdir = os.path.join(ROOT, "targets", dirname)
        if not os.path.isdir(tdir):
            continue
        for dirpath, dirnames, filenames in os.walk(tdir):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git")]
            for fn in sorted(filenames):
                if fn.lower().endswith(".md.off"):
                    add(os.path.join(dirpath, fn[:-7]))
                else:
                    add(os.path.join(dirpath, fn))

    # codex 破甲候选：标出当前部署
    active_name = ""
    try:
        dep = _deployed_instr_path("codex")
        if dep and os.path.isfile(dep):
            active_name = os.path.basename(dep)
    except Exception:
        pass
    lib = os.path.join(ROOT, "targets", "codex", "instructions")
    for it in out:
        if os.path.dirname(it["path"]) == lib:
            it["deployable"] = True
            it["active"] = it["enabled"] and it["name"] == active_name

    out.sort(key=lambda it: (_DOC_CAT_ORDER.get(it["cat"], 99), it["name"].lower()))
    notes = _doc_notes()
    for it in out:
        it["memo"] = notes.get(_doc_rel(_doc_base(it["path"])), "")
    counts = {}
    for it in out:
        counts[it["cat"]] = counts.get(it["cat"], 0) + 1
    cats = [{"id": cid, "name": _DOC_CAT_NAME[cid], "count": counts[cid]}
            for cid, _n in DOC_CATS if counts.get(cid)]
    return {"ok": True, "agent": "", "label": "全部席位",
            "root": ROOT, "categories": cats, "items": out,
            "offCount": sum(1 for it in out if not it["enabled"])}


def _codex_deploy_doc_keywords(doc_id):
    """ChatGPT 页文档的命中关键词（编辑页顶部要看得见，方便照着改）。"""
    routes = _routes_mod()
    if doc_id.startswith("codex:skill/"):
        sid = doc_id[len("codex:skill/"):]
        parent = next((k for k, v in routes.CATEGORIES.items() if v[3] == sid), "")
        return routes.CATEGORIES[parent][2] if parent else ""
    if doc_id == "codex:agents":
        return "本机 AGENTS.md 标签块（部署时写入）"
    if doc_id == "codex:config":
        return "model_instructions_file · model_provider · model"
    return ""


def _codex_edit_path(doc_id):
    """ChatGPT 页落点的编辑目标：AGENTS.md / config.toml 就是本机实盘文件。"""
    ad = _codex_deploy()
    home = ad.home_of("codex") if ad else os.path.join(os.path.expanduser("~"), ".codex")
    if doc_id == "codex:agents":
        return os.path.join(home, "AGENTS.md"), "本机 AGENTS.md（部署时同步标记块）"
    if doc_id == "codex:config":
        return os.path.join(home, "config.toml"), "本机 config.toml（部署时改写 model_instructions_file）"
    if doc_id.startswith("codex:skill/"):
        sid = doc_id[len("codex:skill/"):]
        key = _codex_skill_key(sid)
        if key.startswith("codex:"):
            return "", "父类目内容由 content/routes/ 生成"
        return os.path.join(ROOT, key), "SKILL.md 正文源（部署时投放到 ~/.codex/skills/）"
    return "", ""


def cmd_doc_read(rel):
    """整文件读出（编辑用）。ChatGPT 页的 id 折到项目里的源件。"""
    path, note = (_codex_edit_path(rel) if rel.startswith("codex:")
                  else _wb_edit_path(rel) if rel.startswith("wb:") else ("", ""))
    if rel.startswith("seat:") and rel.split("/", 1)[1] in ("primary", "memory", "settings"):
        return _seat_read_landing(rel)
    if rel.startswith("wb:") and rel in ("wb:custom", "wb:templates"):
        return _wb_read_landing(rel)
    if rel.startswith("codex:") or rel.startswith("wb:"):
        if not path or not os.path.isfile(path):
            return {"ok": False, "error": note or "这份不可编辑"}
        try:
            content = _read_text(path)
        except OSError as e:
            return {"ok": False, "error": str(e)}
        # 写回时按本机文件落点；顺带把内容源路径告诉界面
        return {"ok": True, "id": rel, "path": path, "content": content,
                "size": os.path.getsize(path), "editable": True,
                "enabled": True, "generated": False, "live": True, "hint": note,
                "keywords": _codex_deploy_doc_keywords(rel)}
    p = os.path.abspath(os.path.join(ROOT, rel))
    if not _doc_allowed(p):
        p = os.path.abspath(os.path.join(ROOT, _doc_base(rel)))
        if not _doc_allowed(p):
            return {"ok": False, "error": "不允许访问：%s" % rel}
    real = _doc_actual(_doc_base(p))
    if not real:
        return {"ok": False, "error": "文件不存在：%s" % rel}
    try:
        sz = os.path.getsize(real)
        if sz > 2 * 1024 * 1024:
            return {"ok": False, "error": "超过 2 MB，请用编辑器打开：%s" % real}
        content = _read_text(real)
    except OSError as e:
        return {"ok": False, "error": str(e)}
    base_rel = _doc_rel(_doc_base(real))
    kw = ""
    if base_rel.startswith("content/routes/leaves/"):
        try:
            kw = _routes_mod().leaf_meta(os.path.basename(base_rel)[:-3])["keywords"]
        except Exception:
            kw = ""
    return {"ok": True, "id": _doc_rel(real), "path": real, "content": content,
            "size": sz, "editable": True, "enabled": _doc_enabled(real),
            "keywords": kw,
            "generated": base_rel in DOC_GENERATED or base_rel.startswith("content/routes/leaves/")}


def _write_outside(doc_id, path, content):
    """写项目外的本机文件（~/.codex）。写前备份到 状态/editor-backup。"""
    try:
        with open(path, "rb") as f:
            old = f.read()
        bak = _backup_path(path)
        if not (os.path.isfile(bak) and open(bak, "rb").read() == old):
            with open(bak, "wb") as f:
                f.write(old)
        new = content.encode("utf-8")
        if old == new:
            return {"ok": True, "id": doc_id, "path": path, "written": False,
                    "note": "内容没变，不写盘"}
        with open(path, "wb") as f:
            f.write(new)
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "id": doc_id, "path": path, "written": True, "backup": bak,
            "size": len(new), "note": "已写回本机文件 · 备份在 状态/editor-backup"}


def cmd_doc_write(rel, content):
    """写回 md（停用的 .md.off 也能写）。先备份原内容到 状态/editor-backup。"""
    if rel.startswith("codex:") or rel.startswith("wb:"):
        path, note = (_codex_edit_path(rel) if rel.startswith("codex:")
                      else _wb_edit_path(rel))
        if not path or not os.path.isfile(path):
            return {"ok": False, "error": note or "这份不可编辑"}
        if not _inside(path):
            # 本机落点（~/.codex / ~/.workbuddy）：直接写，备份到 状态/editor-backup
            return _write_outside(rel, path, content)
    p = os.path.abspath(os.path.join(ROOT, _doc_base(rel)))
    if not _doc_allowed(p):
        return {"ok": False, "error": "不允许写入：%s" % rel}
    real = _doc_actual(p)
    if not real:
        return {"ok": False, "error": "文件不存在：%s" % rel}
    try:
        with open(real, "rb") as f:
            old = f.read()
        bak = _backup_path(real)
        if not (os.path.isfile(bak) and open(bak, "rb").read() == old):
            with open(bak, "wb") as f:
                f.write(old)
        new = content.encode("utf-8")
        if old == new:
            return {"ok": True, "path": real, "written": False, "note": "内容没变，不写盘"}
        with open(real, "wb") as f:
            f.write(new)
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "path": real, "written": True, "backup": bak,
            "size": len(new), "note": "已写回 · 备份在 状态/editor-backup"}


def cmd_doc_new(agent, cat, name):
    """在 content/seats/ 下新建 custom_<name>.md（统一落点，好备份、好迁移）。"""
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,40}", name or ""):
        return {"ok": False, "error": "名称只允许字母 / 数字 / _ / -（1-40 字符）"}
    p = os.path.join(CONTENT, "seats", "custom_%s.md" % name)
    if os.path.exists(p):
        return {"ok": False, "error": "同名文件已存在：%s" % _doc_rel(p)}
    title = _DOC_CAT_NAME.get(cat, "自定义")
    try:
        with open(p, "wb") as f:
            f.write(("<!-- %s · %s -->\n\n# %s\n\n" % (title, agent, name)).encode("utf-8"))
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "id": _doc_rel(p), "path": p}


def cmd_doc_delete(rel):
    """删自定义 md；内置 / 生成产物不让删。"""
    p = os.path.abspath(os.path.join(ROOT, rel))
    if not _doc_allowed(p):
        return {"ok": False, "error": "不允许删除：%s" % rel}
    base = os.path.basename(p)
    if not re.fullmatch(r"custom_[A-Za-z0-9_\-]+\.md", base):
        return {"ok": False, "error": "只允许删除 custom_*.md 自建文件"}
    if not os.path.isfile(p):
        return {"ok": False, "error": "文件不存在"}
    try:
        os.remove(p)
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "id": rel, "path": p}


def cmd_cat(target, index):
    """读补丁表第 index 行（0 起，对应 cmd_patches 展平顺序）的文件头尾预览。"""
    d = cmd_patches(target)
    if not d.get("ok"):
        return d
    flat = [r for g in d["groups"] for r in g["rows"]]
    if index < 0 or index >= len(flat):
        return {"ok": False, "error": "行号越界：0-%d" % (len(flat) - 1) if flat else "没有行"}
    row = flat[index]
    p = row["path"]
    out = {"ok": True, "path": p, "role": row["role"], "state": row["state"],
           "isdir": row["isdir"], "head": "", "tail": "", "sha": row["sha"]}
    if row["isdir"] or not os.path.isfile(p):
        return out
    try:
        with open(p, "rb") as f:
            head = f.read(2400).decode("utf-8-sig", "replace")
        with open(p, "rb") as f:
            f.seek(max(0, os.path.getsize(p) - 1600))
            tail = f.read().decode("utf-8", "replace")
    except OSError as e:
        out["error"] = str(e)
        return out
    out["head"] = head
    out["tail"] = tail
    return out


# ─────────────────────────── 文件编辑（purge 式 editor）───────────────────────────

# 允许编辑的扩展名白名单 —— 破甲改的都是这些文本格式；二进制（exe/asar/dll）一律拒
_EDIT_OK_EXT = (".md", ".txt", ".yml", ".yaml", ".json", ".toml", ".js", ".mjs",
                ".cjs", ".css", ".html", ".py", ".ps1", ".bat", ".cmd", ".cmd")


def _backup_path(p):
    """编辑备份落点：状态/editor-backup/<sha1 前缀>_<文件名>。同内容重复编辑不堆副本。"""
    m = mod()
    digest = m.sha1_short(p)[:8]
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "状态", "editor-backup")
    os.makedirs(d, exist_ok=True)
    return os.path.abspath(os.path.join(d, "%s_%s" % (digest, os.path.basename(p))))


def cmd_read(target, index):
    """整文件读出（编辑用）。cat 只给头尾，编辑必须全文。上限 2 MB。"""
    d = cmd_patches(target)
    if not d.get("ok"):
        return d
    flat = [r for g in d["groups"] for r in g["rows"]]
    if index < 0 or index >= len(flat):
        return {"ok": False, "error": "行号越界"}
    row = flat[index]
    p = row["path"]
    out = {"ok": True, "path": p, "role": row["role"], "state": row["state"],
           "isdir": row["isdir"], "sha": row["sha"], "editable": False, "content": ""}
    if row["isdir"] or not os.path.isfile(p):
        out["error"] = "目录不能编辑"
        return out
    if not p.lower().endswith(_EDIT_OK_EXT):
        out["error"] = "只支持文本格式（md/txt/yml/json/toml/js/py…）"
        return out
    try:
        sz = os.path.getsize(p)
        if sz > 2 * 1024 * 1024:
            out["error"] = "文件超过 2 MB，请直接用编辑器改：%s" % p
            return out
        with open(p, "rb") as f:
            out["content"] = f.read().decode("utf-8-sig", "replace")
    except OSError as e:
        out["error"] = str(e)
        return out
    out["editable"] = True
    out["size"] = sz
    return out


def cmd_write(target, index, content):
    """写回第 index 行的文件。写之前先备份原内容到 状态/editor-backup/。"""
    d = cmd_patches(target)
    if not d.get("ok"):
        return d
    flat = [r for g in d["groups"] for r in g["rows"]]
    if index < 0 or index >= len(flat):
        return {"ok": False, "error": "行号越界"}
    row = flat[index]
    p = row["path"]
    if row["isdir"] or not os.path.isfile(p):
        return {"ok": False, "error": "目录不能写"}
    if not p.lower().endswith(_EDIT_OK_EXT):
        return {"ok": False, "error": "只支持文本格式"}
    try:
        sz = os.path.getsize(p)
        if sz > 2 * 1024 * 1024:
            return {"ok": False, "error": "文件超过 2 MB，不走这里改"}
        with open(p, "rb") as f:
            old = f.read()
        bak = _backup_path(p)
        if not (os.path.isfile(bak) and open(bak, "rb").read() == old):
            with open(bak, "wb") as f:
                f.write(old)
        new = content.encode("utf-8")
        # 内容没变就不写 —— 避免无意义地动 mtime（客户端可能按 mtime 缓存）
        if old == new:
            return {"ok": True, "path": p, "written": False, "backup": bak, "note": "内容没变，不写盘"}
        with open(p, "wb") as f:
            f.write(new)
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "path": p, "written": True, "backup": bak,
            "size": len(new), "note": "已写回，备份在 状态/editor-backup"}


def cmd_status(targets):
    m = mod()  # 预热：模块加载是全局惰性的，别让第一次调用落在工作线程里
    want = m.sha1_short("|" + m.POLICY_BASE + "|" + m.OVERLAY + "|False")

    def one(k):
        try:
            card = CARD_FN[k]()
        except Exception as e:
            return {"key": k, "label": k, "home": "", "state": "broken",
                    "rows": [{"level": "err", "path": "", "note": "检测出错：%s" % e}],
                    "files": [], "metrics": []}
        try:
            _augment_deploy_files(card)
        except Exception:
            pass
        return card

    # 四个目标彼此独立，并行探 —— 串行要 5 秒，dsh 一个就占 3.5 秒
    with ThreadPoolExecutor(max_workers=4) as ex:
        cards = list(ex.map(one, targets))
    return {"when": _stamp(), "want_hash": want, "cards": cards}


def _stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ─────────────────────────── 验证 ───────────────────────────

LV = (("OK", "ok"), ("FAIL", "err"), ("WARN", "warn"))


def parse_lines(text):
    out = []
    for raw in text.splitlines():
        ln = raw.rstrip()
        if not ln.strip():
            continue
        level = "dim"
        for tag, lv in LV:
            if ("[%s" % tag) in ln or ln.strip().startswith(tag):
                level = lv
                break
        out.append({"level": level, "text": ln})
    return out


def cmd_test(targets):
    return _test_impl(targets)


def _test_impl(targets):
    """直接驱动 破甲一键通 做只读核对，避免依赖 console 的 sys.exit。"""
    m = mod()
    lines = []
    ok = True

    def add(lv, txt):
        lines.append({"level": lv, "text": txt})

    # 路由层
    route_keys = ROUTE_KEYS
    exist, _sz, hit, miss = probe(m.ROUTE_FILE, route_keys)
    add("info", "【路由层】%s" % m.ROUTE_FILE)
    if not exist:
        ok = False
        add("err", "  [FAIL] route-layer.md 不存在（当前会用内置兜底骨架）")
    else:
        for k in route_keys:
            good = k in hit
            ok = ok and good
            add("ok" if good else "err", "  [%s] 关键段：%s" % ("OK  " if good else "FAIL", k))
        with open(m.ROUTE_FILE, encoding="utf-8", errors="replace") as f:
            body = m.clean_prompt(f.read())
        bad = [w for w in ROUTE_JB_WORDS if w in body]
        if bad:
            ok = False
            add("err", "  [FAIL] 越狱腔残留：%s" % "、".join(bad))
        else:
            add("ok", "  [OK  ] 无越狱腔残留")
        add("dim", "  体量：%d 字符" % len(m.ROUTE_SECTION))

    if "codex" in targets:
        ad = _codex_deploy()
        if ad is None:
            ok = False
            add("err", "  [FAIL] engine/codex_deploy.py 缺失")
        else:
            v = ad.verify("codex")
            add("info", "【ChatGPT】%s" % v["home"])
            add("dim", "  指令源：%s · %d 个落点" % (v["pack"] or "（未选）", v["total"]))
            for c in v["checks"]:
                if c["role"] == "SKILL.md":
                    continue
                ok = ok and c["ok"]
                add("ok" if c["ok"] else "err",
                    "  [%s] %s：%s" % ("OK  " if c["ok"] else "FAIL", c["role"], c["path"]))
            sk = [c for c in v["checks"] if c["role"] == "SKILL.md"]
            sk_ok = sum(1 for c in sk if c["ok"])
            ok = ok and sk_ok == len(sk)
            add("ok" if sk_ok == len(sk) else "err",
                "  [%s] SKILL.md：%d / %d 已投放" % ("OK  " if sk_ok == len(sk) else "FAIL",
                                                  sk_ok, len(sk)))

    if has_dsh(targets):
        t = m.TARGETS["dsh"]()
        c = card_dsh()
        add("info", "【DSH】%s" % (c["home"] or "(未发现)"))
        add("ok" if c["deploy"]["any"] else "dim", "  [当前部署] %s" % c["deploy"]["text"])
        for r in c["rows"]:
            add(r["level"] if r["level"] != "dim" else "dim", "  [%s] %s" % (r["level"].upper(), r["note"]))
        if c["state"] in ("stale", "broken"):
            ok = False

    if "wb" in targets:
        c = card_wb()
        add("info", "【WorkBuddy】%s" % c["home"])
        for r in c["rows"]:
            add(r["level"], "  [%s] %s" % (r["level"].upper(), r["note"]))
        if c["state"] not in ("current",):
            ok = False

    if "zcode" in targets:
        c = card_zcode()
        add("info", "【ZCode】%s" % c["home"])
        for r in c["rows"]:
            add(r["level"], "  [%s] %s" % (r["level"].upper(), r["note"]))
        if c["state"] not in ("current",):
            ok = False

    add("info", "总体：" + ("[OK] 通过" if ok else "[FAIL] 存在 FAIL/WARN，见上文明细"))
    return {"when": _stamp(), "ok": ok, "items": lines}


# ─────────────────────────── 归一预览 ───────────────────────────

FILLERS = ("帮我", "麻烦", "请问", "求", "快点", "咋整", "给我", "帮忙", "能不能", "可不可以")


def cmd_route(sentence):
    """归一预览（只读）—— 实现见 pojia_routes.preview_route（与 console.py route 同源）。"""
    if ENGINE not in sys.path:
        sys.path.insert(0, ENGINE)
    try:
        import pojia_routes as PR
    except Exception as e:
        return {"input": sentence, "error": "pojia_routes.py 加载失败：%s" % e, "hits": []}
    path = mod().ROUTE_FILE if os.path.isfile(mod().ROUTE_FILE) else None
    r = PR.preview_route(sentence, path)
    r["miss"] = not r["hits"]
    return r


# ─────────────────────────── 执行（NDJSON 流） ───────────────────────────

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
LV_BY_COLOR = (("\x1b[92m", "ok"), ("\x1b[93m", "warn"),
               ("\x1b[91m", "err"), ("\x1b[31m", "err"),
               ("\x1b[96m", "info"))


def classify(raw):
    """把带 ANSI 的一行拆成 (纯文本, 级别)。颜色码比文字匹配可靠，优先用它。"""
    lv = ""
    for code, name in LV_BY_COLOR:
        if code in raw:
            lv = name
            break
    ln = ANSI_RE.sub("", raw).rstrip()
    if not lv:
        if "[FAIL]" in ln:
            lv = "err"
        elif "[WARN]" in ln or "未命中" in ln or "未找到" in ln or "出错" in ln:
            lv = "warn"
        elif "[OK" in ln or "完成" in ln:
            lv = "ok"
        else:
            lv = "dim"
    return ln, lv


# ── 俏皮化：只改口吻，不改事实（路径/数量/文件名原样保留） ──

_SPICE_SUBS = [
    # 长句优先
    ("写入后复查失败", "回头一查不对劲"),
    ("写入失败", "没写进去 😵"),
    ("读取失败", "读不着文件 😵"),
    ("投放失败", "撒漏了 😵"),
    ("撤销失败", "没拆利索 😵"),
    ("校验失败", "核了一遍，不对劲 😅"),
    ("解析失败，跳过上下文登记", "读不懂 settings.json，先绕过"),
    ("检测失败", "摸底失败"),
    ("[FAIL]", "[翻车]"),
    ("需要改动：否，已是最新", "要不要动：不用，已经是最新版啦"),
    ("[预演] 需要改动：", "试跑发现要动的地方："),
    ("[预演] 将投放", "试跑：准备撒"),
    ("[预演] 将应用", "试跑：准备动手改"),
    ("[预演] 将修复", "试跑：准备修"),
    ("[预演] 将移除", "试跑：准备拆掉"),
    ("[预演] 将还原/移除", "试跑：准备拆掉"),
    ("路由 skills：新增", "skill 包塞进去"),
    ("路由 skills：已撤", "skill 包收回"),
    ("路由 skills：", "skill 包清点："),
    ("已部署", "上岗成功"),
    ("已落盘", "写好收档"),
    ("已打补丁", "补丁糊上了"),
    ("闸门已解锁", "闸门撬开了"),
    ("网页过滤已解除", "网页过滤拆掉了"),
    ("已还原", "撤干净了"),
    ("已修复", "修好了"),
    ("已回滚", "撤回到"),
    ("已是最新，跳过", "本来就最新，不折腾"),
    ("已是本脚本这一版，跳过", "就是这个版本，不折腾"),
    ("无需还原", "没东西可拆"),
    ("不存在，无需还原", "压根不存在，没得拆"),
    ("未找到", "没找到"),
    ("未检测到", "没摸到"),
    ("仍在运行", "还开着"),
]
_SPICE_RES = [
    (re.compile(r"已还原 (\S+?)："), r"\1 撤干净了："),
    (re.compile(r"重启(.+?)后生效"), r"重启\1才管用"),
    (re.compile(r"重启(.+?)才生效"), r"重启\1才管用"),
]


def spice(ln):
    s = ln
    for rx, rep in _SPICE_RES:
        s = rx.sub(rep, s)
    for a, b in _SPICE_SUBS:
        if a in s:
            s = s.replace(a, b)
    return s


def _clean_env():
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    for k in ("PYTHONPATH", "BASH_ENV", "NODE_OPTIONS",
              "CODEBUDDY_SAFE_DELETE_SANDBOX", "CODEBUDDY_SAFE_DELETE_BULK_GUARD"):
        env.pop(k, None)
    return env


def _pump(cmd, tag, label):
    """跑一条命令并把输出转成 NDJSON 行事件。返回退出码。

    codex_deploy.py 只吐一段 JSON（没有逐行日志），这里把它翻译成人话，
    让「指令提示页开关 → 部署」链路在日志里看得见。
    """
    try:
        p = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, env=_clean_env(),
                             text=True, encoding="utf-8", errors="replace", bufsize=1)
    except Exception as e:
        emit({"type": "line", "target": tag, "level": "err", "text": "启动失败：%s" % e})
        return 1
    assert p.stdout is not None
    is_codex = tag in ("codex", "wb", "claude", "hermes", "zcode") and any(
        ("codex_deploy.py" in str(c) or "workbuddy_deploy.py" in str(c)
         or "seats_deploy.py" in str(c)) for c in cmd)
    buf = []
    for raw in p.stdout:
        ln, level = classify(raw)
        if not ln.strip():
            continue
        if is_codex:
            buf.append(ln)                 # JSON 原文不进日志，下面翻译成人话
        else:
            emit({"type": "line", "target": tag, "level": level, "text": spice(ln)})
    p.wait()
    if is_codex:
        for text, level in _codex_deploy_lines(buf, tag):
            emit({"type": "line", "target": tag, "level": level, "text": text})
    return p.returncode or 0


def _seat_deploy_lines(seat, d, out, written, removed, pack):
    """Claude / Hermes / ZCode 的日志摘要：每个落点一句话。"""
    act = d.get("action") or ""
    prim = [p for p in written if p.endswith((".md", ".json"))
            and p.replace("\\", "/").endswith(("/pojia.md", "/CLAUDE.md", "/SOUL.md",
                                               "/AGENTS.md", "/settings.json"))]
    skills = [p for p in written if p.replace("\\", "/").endswith("/SKILL.md")]
    if act == "deploy":
        out.append(("指令源：" + (pack or "（无 —— 按官方原版处理）"),
                    "ok" if pack else "warn"))
        if prim:
            out.append(("写入落点 %d 份：%s" % (
                len(prim), "、".join(sorted({os.path.basename(p) for p in prim}))), "ok"))
        if skills:
            out.append(("投放技能 %d 份 SKILL.md" % len(skills), "ok"))
        if not written and not removed:
            out.append(("落点已是最新，本次无需改动", "dim"))
    elif act == "restore":
        out.append(("还原完成：回拷备份、撤下投放的落点", "ok"))
        n_skill = sum(1 for p in removed if p.replace("\\", "/").endswith("SKILL.md"))
        if removed and n_skill != len(removed):
            out.append(("  回拷用户文件 %d 份" % (len(removed) - n_skill), "dim"))
        if n_skill:
            out.append(("  撤下技能 %d 份" % n_skill, "dim"))
    if d.get("note"):
        out.append((d["note"], "ok"))
    return out


def _wb_deploy_lines(d, out, written, removed, pack, kb, short):
    """WorkBuddy 的日志摘要：四路各自一句话，不逐条刷 100+ 个路径。"""
    act = d.get("action") or ""
    custom = [p for p in written
              if p.replace("\\", "/").endswith(("/settings.json", "/app-config.json"))]
    pol = [p for p in written if p.endswith((".tpl", "product.json"))]
    ident = [p for p in written
             if p.replace("\\", "/").endswith(("/SOUL.md", "/IDENTITY.md", "/USER.md"))]
    skills = [p for p in written if p.replace("\\", "/").endswith("/SKILL.md")]
    if act == "deploy":
        out.append(("指令源：" + (pack or "（无 —— 按官方原版处理）"),
                    "ok" if pack else "warn"))
        if custom:
            out.append(("写入自定义指令 personalization.customPrompt（%d 处落点）"
                        % len(custom), "ok"))
        if pol:
            out.append(("替换模板政策块 %d 份（.tpl / product.json）" % len(pol), "ok"))
        if ident:
            out.append(("身份层已注入 %d 份（SOUL / IDENTITY / USER）" % len(ident), "ok"))
        if skills:
            out.append(("投放技能 %d 份 SKILL.md" % len(skills), "ok"))
        if not written and not removed:
            out.append(("四路已是最新，本次无需改动", "dim"))
    elif act == "restore":
        out.append(("还原完成：回拷备份、撤下投放的落点", "ok"))
        n_skill = sum(1 for p in removed if (os.sep + "skills" + os.sep) in p)
        if removed and n_skill != len(removed):
            out.append(("  回拷用户文件 %d 份" % (len(removed) - n_skill), "dim"))
        if n_skill:
            out.append(("  撤下技能 %d 份" % n_skill, "dim"))
    if d.get("note"):
        out.append((d["note"], "ok"))
    return out


def _codex_deploy_lines(lines, tag="codex"):
    """把部署结果折成几行「做了什么、结果怎样」，不逐条刷上百个路径。

    ChatGPT 与 WorkBuddy 的引擎 JSON 结构相同、落点不同：按 tag 选各自的
    归类规则与路径显示。
    """
    blob = "\n".join(lines).strip()
    try:
        d = json.loads(blob)
    except ValueError:
        return []
    if not isinstance(d, dict):
        return []
    out = []
    act = d.get("action") or ""
    if d.get("dry"):
        out.append(("[预演] 不写盘 · 检查四路落点", "dim"))
        return out

    written = d.get("written") or []
    removed = d.get("removed") or []
    pack = d.get("pack") or ""
    home = d.get("home") or ""

    def kb(p):
        try:
            return "%d 字节" % os.path.getsize(p)
        except OSError:
            return "—"

    def short(p):
        root = "~/.codex/" if tag == "codex" else "~/.workbuddy/"
        return p.replace(home + os.sep, root).replace("\\", "/")

    if tag == "wb":
        return _wb_deploy_lines(d, out, written, removed, pack, kb, short)
    if tag in _SEAT_DEPLOY_KEYS:
        return _seat_deploy_lines(tag, d, out, written, removed, pack)
    instr = [p for p in written if (os.sep + "prompts" + os.sep) in p]
    agents = [p for p in written if p.replace("\\", "/").endswith("/AGENTS.md")]
    cfg = [p for p in written if p.replace("\\", "/").endswith("/config.toml")]
    skills = [p for p in written if (os.sep + "skills" + os.sep) in p]

    if act == "deploy":
        out.append(("指令源：" + (pack or "（无 —— 按官方原版处理）"),
                    "ok" if pack else "warn"))
        if instr:
            out.append(("写入指令文件 %s（%s）" % (short(instr[0]), kb(instr[0])), "ok"))
        if agents:
            out.append(("修改 AGENTS.md：插入标记块（%s）" % kb(agents[0]), "ok"))
        if cfg:
            out.append(("修改 config.toml：model_instructions_file → prompts/%s" % pack, "ok"))
        if skills:
            out.append(("投放技能 %d 份 SKILL.md → %s" % (len(skills), short(os.path.dirname(skills[0]))), "ok"))
        if not written and not removed:
            out.append(("四路已是最新，本次无需改动", "dim"))
    elif act == "restore":
        out.append(("还原完成：撤回指令文件、AGENTS 标记块、config 指针与投放的技能", "ok"))
        for p in removed:
            if (os.sep + "skills" + os.sep) in p:
                continue
            out.append(("  撤下 " + short(p), "dim"))
        n_skill = sum(1 for p in removed if (os.sep + "skills" + os.sep) in p)
        if n_skill:
            out.append(("  撤下技能 %d 份" % n_skill, "dim"))

    for p in removed:
        if act != "restore":
            out.append(("  撤下 " + short(p), "dim"))
    if d.get("note"):
        out.append((d["note"], "ok"))
    return out


# 破甲模式：独立动作，不走 per-target 循环 —— 它不是 破甲一键通 的 target，
# 而是 DSH 的一个附加产物（用户级 agent preset）。命令等价于
# `python 破甲一键通.py --dsh-preset deploy|remove|status --yes`。
PRESET_ACTION = {"preset-deploy": "deploy", "preset-remove": "remove",
                 "preset-status": "status"}
PRESET_LABEL = {"deploy": "部署破甲模式", "remove": "移除破甲模式", "status": "查看破甲模式"}


def _run_preset(sub):
    label = PRESET_LABEL.get(sub, sub)
    emit({"type": "start", "action": label, "targets": ["dsh"]})
    emit({"type": "step", "target": "dsh", "action": label})
    rc = _pump([sys.executable, SRC, "--dsh-preset", sub, "--yes"], "dsh", label)
    emit({"type": "done", "target": "dsh", "code": rc})
    emit({"type": "exit", "code": rc})
    return rc


def _fold_targets(keys):
    """界面卡 → 命令行目标：折叠 CARD_ONLY 里的别名并去重（保序）。"""
    out, seen = [], set()
    for k in keys:
        k2 = CARD_ONLY.get(k, k)
        if k2 not in seen:
            seen.add(k2)
            out.append(k2)
    return out


def cmd_run(action, targets, flags=None):
    flags = set(flags or [])
    # console.py 的动作词是 apply / revert / dry（dry 在内部映射成 dry-run）
    argv_action = {"apply": "apply", "revert": "revert", "dry": "dry",
                   "dry-run": "dry", "预演": "dry"}.get(action, action)
    if action in PRESET_ACTION:
        return _run_preset(PRESET_ACTION[action])
    label = {"apply": "apply", "revert": "revert", "dry": "dry-run"}.get(argv_action, argv_action)
    # 伪目标过滤：pojia 只做展示，透传给 console.py 会变成"未知目标"
    targets = [k for k in targets if k not in PSEUDO]
    # 界面卡折叠 + 去重：DSH 桌面版那张卡勾上后要落到 dsh 上，两张卡都勾也只跑一遍
    picked = list(targets)
    targets = _fold_targets(targets)
    if picked != targets:
        emit({"type": "log", "level": "info", "scope": "sys",
              "text": "DSH 桌面版与 Web/CLI 版共用同一套命令（preset 一次写两份 profile），"
                      "已按 dsh 合并执行"})
    # 两个互斥的连通开关（都由 DSH 卡片里那个下拉决定）：
    #   no-preset    —— 只注入提示词，别连带动破甲模式目录
    #   preset-only  —— 只动破甲模式，DSH 本体的提示词一个字节都不碰
    extra = ["--no-dsh-preset"] if "no-preset" in flags else []
    preset_only = "preset-only" in flags
    emit({"type": "start", "action": label, "targets": targets})
    buf = []
    for k in targets:
        emit({"type": "step", "target": k, "action": label})
        if k == "codex":
            # ChatGPT 席位走四落点部署（指令文件 / AGENTS.md / config.toml / SKILL.md）
            cmd = [sys.executable, os.path.join(ENGINE, "codex_deploy.py")]
            if argv_action == "apply":
                # 滑块拨右 = 部署。用户刚点过「还原」的话，先把还原时置 false 的
                # 四路开关拨回来；否则四路全关又会被 deploy() 判成「回官方原版」。
                ad = _codex_deploy()
                st = ad.state() if ad else {}
                if (st.get("pack_on") is False and st.get("agents") is False
                        and st.get("config") is False and st.get("skills") is False):
                    ad.enable_all()
                cmd.append("apply")
            else:
                cmd.append({"revert": "restore", "dry": "deploy"}[argv_action])
            if argv_action == "dry":
                cmd.append("--dry")
        elif k == "wb":
            # WorkBuddy 席位走四落点部署（自定义指令 / 模板政策块 / 身份层 / SKILL.md）
            cmd = [sys.executable, os.path.join(ENGINE, "workbuddy_deploy.py")]
            if argv_action == "apply":
                ad = _wb_deploy()
                st = ad.state() if ad else {}
                if (st.get("pack_on") is False and st.get("custom") is False
                        and st.get("templates") is False and st.get("identity") is False
                        and st.get("skills") is False):
                    ad.enable_all()
                cmd.append("apply")
            else:
                cmd.append({"revert": "restore", "dry": "deploy"}[argv_action])
            if argv_action == "dry":
                cmd.append("--dry")
        elif k in _SEAT_DEPLOY_KEYS:
            # Claude / Hermes / ZCode：各自三落点（指令文件 / 记忆或指针 / SKILL.md）
            cmd = [sys.executable, os.path.join(ENGINE, "seats_deploy.py"), k]
            if argv_action == "apply":
                sd = _seat_deploy()
                st = sd.state(k) if sd else {}
                if all(st.get(f) is False for f in ("pack_on", "primary", "memory",
                                                    "settings", "skills")):
                    sd.enable_all(k)
                cmd.append("apply")
            else:
                cmd.append({"revert": "restore", "dry": "deploy"}[argv_action])
            if argv_action == "dry":
                cmd.append("--dry")
        elif preset_only and k == "dsh":
            # 只有 DSH 有破甲模式这个附加产物；走主脚本的 --dsh-preset 通道，
            # 不带 --target 就等于完全不碰本体文件。
            sub = "remove" if argv_action == "revert" else "deploy"
            cmd = [sys.executable, SRC, "--dsh-preset", sub]
            if argv_action == "dry":
                cmd.append("--dry-run")
            cmd.append("--yes")
        else:
            cmd = [sys.executable, CONSOLE, k, argv_action] + extra
        buf.append(_pump(cmd, k, label))
        emit({"type": "done", "target": k, "code": buf[-1]})
    code = 0 if all(c == 0 for c in buf) else 1
    if "dsh" in targets:
        # 刚动过 DSH，旧的安装基点探测结论一律作废 —— 否则状态卡会拿着几分钟前
        # 的答案（验证：装完 DSH 后卡片仍显示「未检测到安装」）。
        cache_drop("dsh_bases")
        # 文件扫描也一样：刚打完补丁，命中数必须重算，不能拿旧结论糊弄
        cache_drop("dsh_scan")
    emit({"type": "exit", "code": code})
    return code


# ─────────────────────────── diff 预览（先预览再确认写入） ───────────────────────────

_DIFF_KINDS = {"codex", "zcode", "claude", "hermes"}   # 指令文件型，可全文对比


def _diff_rows(before, after, ctx=2):
    """difflib 对齐出并排行：[{l, r, t}]，t ∈ same/del/add/mod。"""
    import difflib
    a = (before or "").splitlines()
    b = (after or "").splitlines()
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    rows = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                rows.append({"l": a[i1 + k], "r": b[j1 + k], "t": "same"})
        elif tag == "delete":
            for k in range(i2 - i1):
                rows.append({"l": a[i1 + k], "r": "", "t": "del"})
        elif tag == "insert":
            for k in range(j2 - j1):
                rows.append({"l": "", "r": b[j1 + k], "t": "add"})
        else:  # replace —— 逐行配对成 mod
            n = max(i2 - i1, j2 - j1)
            for k in range(n):
                rows.append({"l": a[i1 + k] if i1 + k < i2 else "",
                             "r": b[j1 + k] if j1 + k < j2 else "", "t": "mod"})
    return rows


def _trim_rows(rows, ctx=2):
    """只留变化区 ±ctx 行，中间用省略行隔开（全文动辄几百行，没必要全渲染）。"""
    idx = [i for i, r in enumerate(rows) if r["t"] != "same"]
    if not idx:
        return {"rows": [], "unchanged": True}
    keep = set()
    for i in idx:
        for k in range(max(0, i - ctx), min(len(rows), i + ctx + 1)):
            keep.add(k)
    out, last, cut = [], -1, False
    for i in sorted(keep):
        if i != last + 1:
            out.append({"l": "…", "r": "…", "t": "gap"})
        out.append(rows[i])
        last = i
    return {"rows": out, "unchanged": False}


def cmd_diff(target):
    if target not in _DIFF_KINDS:
        try:
            label = mod().TARGETS[target]().label
        except Exception:
            label = target
        return {"target": target, "label": label, "support": "none",
                "note": "该目标一次写多个模板/闸门文件，请用「dry」看文件清单，写入走事务可回退"}
    m = mod()
    t = m.TARGETS[target]()
    args = args_for(target)
    home = t.resolve_home(getattr(args, getattr(t, "dir_flag", "codex_dir"), "") or "")
    user, _src = m.load_user_persona(args.persona or "")
    want = m.seat_want(target, user)
    items = []
    if target == "codex":
        cfg_p, inst_p, st_p, _bak = t.paths(home)
        cfg = m.read_text_safe(cfg_p) or ""
        try:
            rel = t.current_instr(cfg)
        except Exception:
            rel = os.path.basename(inst_p)
        inst_p = os.path.abspath(os.path.join(home, rel)) if rel else inst_p
        items.append({"path": inst_p, "before": m.read_text_safe(inst_p) or "",
                      "after": t.build_instructions(user, want), "note": "指令文件（整文件生成）"})
        ag_p = t.agents_path(home)
        ag_old = m.read_text_safe(ag_p) or ""
        items.append({"path": ag_p, "before": ag_old,
                      "after": t.merge_agents_block(ag_old, t.build_agents_block(want)),
                      "note": "第二注入位 · AGENTS.md 标记块（用户内容保留）"})
    elif target == "zcode":
        ag_p = os.path.join(home, "AGENTS.md")
        items.append({"path": ag_p, "before": m.read_text_safe(ag_p) or "",
                      "after": m.build_zcode_instructions(user, want), "note": "AGENTS.md（整文件改写）"})
    else:  # claude / hermes —— 标记块合并（grok/gemini 已移出 GUI，仅保留 revert 通道）
        md_p = os.path.join(home, t.md_name)
        old = m.read_text_safe(md_p) or ""
        after = m.merge_marked_block(old, target, m.build_zcode_instructions(user, want))
        items.append({"path": md_p, "before": old, "after": after,
                      "note": "标记块合并，用户原有内容保留"})
        if target == "gemini":
            sp = os.path.join(home, "settings.json")
            if os.path.isfile(sp):
                items.append({"path": sp, "before": m.read_text_safe(sp) or "",
                              "after": m.read_text_safe(sp) or "",
                              "note": "settings.json 仅在缺 context.fileName 时才登记，通常不动"})
    for it in items:
        trimmed = _trim_rows(_diff_rows(it["before"], it["after"]))
        it["rows"] = trimmed["rows"]
        it["unchanged"] = trimmed["unchanged"]
    return {"target": target, "label": t.label, "support": "full",
            "changed": [it for it in items if not it.get("unchanged")], "items": items}




def main(argv):
    if not argv:
        emit({"error": "用法：bridge.py meta|status|test|route|tx|diff|patches|cat|run"})
        return 2
    cmd = argv[0].lower()
    if cmd == "meta":
        emit(cmd_meta())
    elif cmd == "tx":
        emit(cmd_tx(argv[1:]))
    elif cmd == "diff":
        tgt = argv[1] if len(argv) > 1 else ""
        emit(cmd_diff(tgt))
    elif cmd == "prompts":
        emit(cmd_prompts())
    elif cmd == "prompt-read":
        emit(cmd_prompt_read(argv[1] if len(argv) > 1 else ""))
    elif cmd == "prompt-write":
        # prompt-write <id> <base64 内容> —— 全文经 base64 过桥
        try:
            content = base64.b64decode(argv[2] if len(argv) > 2 else "").decode("utf-8")
        except Exception:
            emit({"ok": False, "error": "prompt-write 第 3 参必须是 UTF-8 文本的 base64"})
            return 2
        emit(cmd_prompt_write(argv[1] if len(argv) > 1 else "", content))
    elif cmd == "prompt-new":
        emit(cmd_prompt_new(argv[1] if len(argv) > 1 else ""))
    elif cmd == "prompt-delete":
        emit(cmd_prompt_delete(argv[1] if len(argv) > 1 else ""))
    elif cmd == "instr-list":
        emit(cmd_instr_list(argv[1] if len(argv) > 1 else "codex"))
    elif cmd == "instr-apply":
        emit(cmd_instr_apply(argv[1] if len(argv) > 1 else "codex",
                             argv[2] if len(argv) > 2 else ""))
    elif cmd == "instr-disable":
        emit(cmd_instr_disable(argv[1] if len(argv) > 1 else "codex"))
    elif cmd == "instr-toggle":
        emit(cmd_instr_toggle(argv[1] if len(argv) > 1 else "codex",
                              argv[2] if len(argv) > 2 else "",
                              (argv[3] if len(argv) > 3 else "1") not in ("0", "false", "off")))
    elif cmd == "docs":
        emit(cmd_docs(argv[1] if len(argv) > 1 else "codex"))
    elif cmd == "doc-read":
        emit(cmd_doc_read(argv[1] if len(argv) > 1 else ""))
    elif cmd == "doc-write":
        try:
            content = base64.b64decode(argv[2] if len(argv) > 2 else "").decode("utf-8")
        except Exception:
            emit({"ok": False, "error": "doc-write 第 3 参必须是 UTF-8 文本的 base64"})
            return 2
        emit(cmd_doc_write(argv[1] if len(argv) > 1 else "", content))
    elif cmd == "doc-new":
        emit(cmd_doc_new(argv[1] if len(argv) > 1 else "codex",
                         argv[2] if len(argv) > 2 else "custom",
                         argv[3] if len(argv) > 3 else ""))
    elif cmd == "doc-delete":
        emit(cmd_doc_delete(argv[1] if len(argv) > 1 else ""))
    elif cmd == "doc-toggle":
        emit(cmd_doc_toggle(argv[1] if len(argv) > 1 else ""))
    elif cmd == "doc-toggle-many":
        # doc-toggle-many <base64 JSON [{"id":..,"on":..}]>
        try:
            payload = json.loads(base64.b64decode(argv[1] if len(argv) > 1 else "").decode("utf-8"))
        except Exception:
            emit({"ok": False, "error": "doc-toggle-many 参数必须是 JSON 数组的 base64"})
            return 2
        emit(cmd_doc_toggle_many(payload, True))
    elif cmd == "doc-memo":
        emit(cmd_doc_memo(argv[1] if len(argv) > 1 else "",
                          argv[2] if len(argv) > 2 else ""))
    elif cmd == "patches":
        tgt = argv[1] if len(argv) > 1 else ""
        emit(cmd_patches(tgt))
    elif cmd == "cat":
        tgt = argv[1] if len(argv) > 1 else ""
        idx = int(argv[2]) if len(argv) > 2 and argv[2].isdigit() else -1
        emit(cmd_cat(tgt, idx))
    elif cmd == "read":
        tgt = argv[1] if len(argv) > 1 else ""
        idx = int(argv[2]) if len(argv) > 2 and argv[2].isdigit() else -1
        emit(cmd_read(tgt, idx))
    elif cmd == "write":
        # write <target> <idx> <base64 内容> —— 全文经 base64 过桥，别让换行/引号炸 JSON
        tgt = argv[1] if len(argv) > 1 else ""
        idx = int(argv[2]) if len(argv) > 2 and argv[2].isdigit() else -1
        try:
            content = base64.b64decode(argv[3] if len(argv) > 3 else "").decode("utf-8")
        except Exception:
            emit({"ok": False, "error": "write 第 4 参必须是 UTF-8 文本的 base64"})
            return 2
        emit(cmd_write(tgt, idx, content))
    elif cmd == "status":
        emit(cmd_status(norm_targets(argv[1] if len(argv) > 1 else "")))
    elif cmd == "test":
        emit(_test_impl(norm_targets(argv[1] if len(argv) > 1 else "")))
    elif cmd == "route":
        emit(cmd_route(" ".join(argv[1:])))
    elif cmd == "run":
        if len(argv) < 2:
            emit({"error": "run 需要动作：apply|revert|dry|preset-deploy|preset-remove|preset-status"})
            return 2
        flags = [f for f in re.split(r"[,，\s]+", argv[3]) if f] if len(argv) > 3 else []
        cmd_run(argv[1], norm_targets(argv[2] if len(argv) > 2 else ""), flags)
    else:
        emit({"error": "未知命令：%s" % cmd})
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
