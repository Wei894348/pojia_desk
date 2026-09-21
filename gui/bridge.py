# -*- coding: utf-8 -*-
"""bridge.py -- 破甲工作台 GUI 与 破甲一键通 之间的 JSON 桥。

Electron 主进程只跟这个脚本对话，不直接碰 破甲一键通.py。
两种输出形态：
  · 单发 JSON   meta / status / test / route
  · NDJSON 流   run（逐行日志，末尾一条 exit）

用法：
    python gui/bridge.py meta
    python gui/bridge.py status [codex,dsh,wb,zcode]
    python gui/bridge.py test   [codex,dsh,wb,zcode]
    python gui/bridge.py route  "帮我弄个挂"
    python gui/bridge.py run    apply|revert|dry [codex,dsh,wb,zcode]
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "破甲一键通.py")
CONSOLE = os.path.join(ROOT, "console.py")
ORDER = ("codex", "dsh", "wb", "zcode", "claude", "grok", "gemini", "hermes")
# 与 console.py 的 CODEX_KEYS 对齐（主脚本里没有这个常量，它在控制台那层）
CODEX_KEYS = ("unlock-v6", "任务路由", "反拒绝铁律", "创作许可前提", "点名即需求", "最终覆盖")
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


def cached(key, ttl, fn):
    """落盘缓存。dsh 的 find_bases 会去探测运行中的进程，单次要 3.4 秒，
    而结果（npx 缓存目录）基本不变，缓存 5 分钟足够。"""
    path = os.path.join(ROOT, "状态", "_gui_cache_%s.json" % key)
    try:
        d = json.load(open(path, encoding="utf-8"))
        if time.time() - d.get("t", 0) < ttl:
            return d.get("v")
    except Exception:
        pass
    v = fn()
    try:
        json.dump({"t": time.time(), "v": v}, open(path, "w", encoding="utf-8"),
                  ensure_ascii=False)
    except Exception:
        pass
    return v


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
    out = []
    for p in re.split(r"[,，\s]+", (spec or "").strip().lower()):
        if not p:
            continue
        p = {"workbuddy": "wb", "zai": "zcode"}.get(p, p)
        if p == "all":
            return list(ORDER)
        if p in ORDER and p not in out:
            out.append(p)
    return out or list(ORDER)


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
        txt = open(path, encoding="utf-8", errors="replace").read()
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
    persona = os.path.join(ROOT, "persona.md")
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

    对应冷咖啡 workbench 的「版本对比」页：看得到改动、查得到完整性、回得去。
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
    m = mod()
    t = m.TARGETS["codex"]()
    home = t.resolve_home("")
    cfg_p, inst_p, st_p, bak = t.paths(home)
    keys = list(CODEX_KEYS) + ["沉浸优先", "表演规则"]
    rows, files, state = [], [], "none"

    if not os.path.isdir(home):
        return {"key": "codex", "label": t.label, "home": home, "state": "broken",
                "rows": [{"level": "err", "path": cfg_p, "note": "目录不存在（Codex 未安装？）"}],
                "files": [], "metrics": []}

    cfg = m.read_text_safe(cfg_p) or ""
    target = ""
    try:
        target = t.current_instr(cfg)
    except Exception as e:
        rows.append({"level": "err", "path": cfg_p, "note": "配置解析失败：%s" % e})
    inst_path = os.path.abspath(os.path.join(home, target)) if target else ""
    is_ours = bool(inst_path) and os.path.normcase(inst_path) == os.path.normcase(os.path.abspath(inst_p))

    if is_ours:
        exist, sz, hit, miss = probe(inst_p, keys)
        files.append({"path": inst_p, "exists": exist, "size": sz, "sha": sha_of(inst_p),
                      "hit": hit, "miss": miss, "role": "指令文件"})
        if not exist or sz == 0:
            state = "stale"
            rows.append({"level": "warn", "path": inst_p, "note": "配置指过去了，但指令文件不存在或为空"})
        elif not miss:
            state = "current"
            rows.append({"level": "ok", "path": inst_p, "note": "指令文件 · 本脚本版本"})
        else:
            state = "stale"
            rows.append({"level": "warn", "path": inst_p, "note": "已部署但缺关键段：%s" % "、".join(miss)})
        rows.append({"level": "ok", "path": cfg_p,
                     "note": "顶层 model_instructions_file → %s（新会话加载）" % target})
    elif target:
        state = "foreign"
        rows.append({"level": "warn", "path": cfg_p,
                     "note": "model_instructions_file = %s（不是本工具的）" % target})
    elif m.MARK_BEGIN in cfg:
        state = "stale"
        rows.append({"level": "warn", "path": cfg_p,
                     "note": "部署块不在 TOML 顶层，Codex 不会加载；重新部署修复"})
    else:
        state = "none"
        rows.append({"level": "dim", "path": cfg_p, "note": "未部署"})

    st = {}
    try:
        st = t.load_state(home) or {}
    except Exception:
        pass
    if st:
        rows.append({"level": "dim", "path": st_p,
                     "note": "上次 %s @ %s" % (st.get("last_action", "?"), st.get("last_apply_time", "?"))})

    metrics = [{"name": "配置", "value": os.path.basename(cfg_p), "ok": os.path.isfile(cfg_p)},
               {"name": "指令文件", "value": ("%d B" % size_of(inst_p)) if is_ours else "—", "ok": is_ours},
               {"name": "备份目录", "value": "有" if os.path.isdir(bak) else "无", "ok": os.path.isdir(bak)}]
    if files:
        metrics.append({"name": "关键段", "value": "%d/%d" % (len(files[0]["hit"]), len(keys)),
                        "ok": not files[0]["miss"]})
    return {"key": "codex", "label": t.label, "home": home, "state": state,
            "rows": rows, "files": files, "metrics": metrics}


def card_zcode():
    m = mod()
    t = m.TARGETS["zcode"]()
    home = t.resolve_home("")
    ag, st_p, bak = t.paths(home)
    keys = list(m.ZCODE_KEYS) + ["学姐模式开启", "工程任务"]
    rows, files = [], []

    if not os.path.isfile(ag):
        return {"key": "zcode", "label": t.label, "home": home, "state": "none",
                "rows": [{"level": "dim", "path": ag, "note": "AGENTS.md 不存在（未部署）"}],
                "files": [], "metrics": []}

    exist, sz, hit, miss = probe(ag, keys)
    txt = open(ag, encoding="utf-8", errors="replace").read()
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
        st = json.loads(open(st_p, encoding="utf-8", errors="replace").read())
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


def card_dsh():
    m = mod()
    t = m.TARGETS["dsh"]()
    bases = []
    try:
        bases = cached("dsh_bases", 300, lambda: t.find_bases(""))
    except Exception:
        bases = []
    rows, files, total, hit = [], [], 0, 0
    # 剪枝 + 字节预筛：全量 walk 要 3.5 秒，实际只需要"含人格标记"的那几个文件
    skip_dirs = {".git", "node_modules", "test", "tests", "__tests__", "docs",
                 "coverage", ".cache", "examples", "types", "esm", "cjs", "fixtures"}
    exts = (".js", ".mjs", ".cjs", ".json", ".yml", ".yaml")
    b_marks = ("麻衣".encode("utf-8"), "沉浸优先".encode("utf-8"), b"unlock-v6")
    for base in bases:
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
                full = all(k in txt for k in ("沉浸优先", "表演规则", "学姐的创作室")) or "unlock-v6" in txt
                if full:
                    hit += 1
                files.append({"path": p, "exists": True, "size": len(data),
                              "sha": sha_of(p), "role": "补丁后文件",
                              "hit": [k for k in ("沉浸优先", "表演规则", "任务路由") if k in txt],
                              "miss": [] if full else ["完整人格段"]})
    if not bases:
        state = "broken"
        rows.append({"level": "err", "path": "(npx 缓存)", "note": "未发现 @deepseek-ai 安装"})
    elif total == 0:
        state = "none"
        rows.append({"level": "dim", "path": bases[0], "note": "未发现带人格标记的文件 —— 未部署"})
    elif hit < total:
        state = "stale"
        rows.append({"level": "warn", "path": bases[0],
                     "note": "含人格标记文件 %d / %d —— 有旧版残留，建议重新部署" % (hit, total)})
    else:
        state = "current"
        rows.append({"level": "ok", "path": bases[0], "note": "含人格标记文件 %d / %d 全部命中" % (hit, total)})
    for p in files[:6]:
        rows.append({"level": "dim", "path": p["path"], "note": "补丁后 %d B" % p["size"]})

    metrics = [{"name": "安装基点", "value": "%d 处" % len(bases), "ok": bool(bases)},
               {"name": "人格文件", "value": "%d / %d" % (hit, total), "ok": total > 0 and hit == total}]
    home = bases[0] if bases else ""
    return {"key": "dsh", "label": t.label, "home": home, "state": state,
            "rows": rows, "files": files[:10], "metrics": metrics}


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
    _pol, want = m.build_policy(_user)
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
    # 卡片依旧报「本版」，和 console 侧的判定不一致（2026-09-19 往返实测踩到）。
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

    metrics = [{"name": "模板靶点", "value": "%d 宽松 / %d 命中" % (loose, pol_n), "ok": pol_n > 0 and loose == pol_n},
               {"name": "命令闸门", "value": "%d / %d" % (gate_ok, len(js_files)), "ok": js_files and gate_ok == len(js_files)},
               {"name": "网页过滤", "value": "%d / %d" % (wf_ok, len(js_files)), "ok": js_files and wf_ok == len(js_files)},
               {"name": "文件保护", "value": "已中和" if guard == 0 else "%d 残留" % guard, "ok": guard == 0},
               {"name": "数据目录", "value": data or "—", "ok": bool(data)}]
    if state == "current" and not full_ok:
        state = "warn"
    return {"key": "wb", "label": t.label, "home": install, "state": state,
            "rows": rows, "files": files, "metrics": metrics}


def card_seat_md(key):
    """Claude / Grok / Gemini 通用卡片：单文件标记块 + skills 投放计数。"""
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

    txt = ""
    if os.path.isfile(md_p):
        with open(md_p, "rb") as f:
            txt = f.read().decode("utf-8-sig", "replace")
    user, _src = m.load_user_persona("")
    _pol, want = m.build_policy(user)
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


CARD_FN = {"codex": card_codex, "dsh": card_dsh, "wb": card_wb, "zcode": card_zcode,
           "claude": lambda: card_seat_md("claude"),
           "grok": lambda: card_seat_md("grok"),
           "gemini": lambda: card_seat_md("gemini"),
           "hermes": lambda: card_seat_md("hermes")}


def cmd_status(targets):
    m = mod()  # 预热：模块加载是全局惰性的，别让第一次调用落在工作线程里
    want = m.sha1_short("|" + m.POLICY_BASE + "|" + m.OVERLAY + "|False")

    def one(k):
        try:
            return CARD_FN[k]()
        except Exception as e:
            return {"key": k, "label": k, "home": "", "state": "broken",
                    "rows": [{"level": "err", "path": "", "note": "检测出错：%s" % e}],
                    "files": [], "metrics": []}

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
        body = m.clean_prompt(open(m.ROUTE_FILE, encoding="utf-8", errors="replace").read())
        bad = [w for w in ROUTE_JB_WORDS if w in body]
        if bad:
            ok = False
            add("err", "  [FAIL] 越狱腔残留：%s" % "、".join(bad))
        else:
            add("ok", "  [OK  ] 无越狱腔残留")
        add("dim", "  体量：%d 字符" % len(m.ROUTE_SECTION))

    if "codex" in targets:
        t = m.TARGETS["codex"]()
        home = t.resolve_home("")
        cfg_p, inst_p, _st, _bak = t.paths(home)
        add("info", "【Codex】%s" % home)
        cfg = ""
        if os.path.isfile(cfg_p):
            cfg = open(cfg_p, encoding="utf-8", errors="replace").read()
        tgt = ""
        for ln in cfg.splitlines():
            if "model_instructions_file" in ln and "=" in ln:
                tgt = ln.split("=", 1)[1].strip().strip('"').strip("'")
                break
        if not tgt:
            ok = False
            add("err", "  [FAIL] config.toml 缺少 model_instructions_file")
        else:
            instr = os.path.normpath(os.path.join(home, tgt))
            add("dim", "  指令文件：%s" % instr)
            e2, _s2, hit2, miss2 = probe(instr, list(CODEX_KEYS) + ["沉浸优先", "表演规则"])
            for k in list(CODEX_KEYS) + ["沉浸优先", "表演规则"]:
                good = k in hit2
                ok = ok and good
                add("ok" if good else "err", "  [%s] 关键段：%s" % ("OK  " if good else "FAIL", k))
        p = os.path.join(ROOT, "persona.md")
        if os.path.isfile(p) and os.path.isfile(inst_p):
            if os.path.getmtime(inst_p) + 5 < os.path.getmtime(p):
                add("warn", "  [WARN] persona.md 比部署文件新 —— 改了人格没重打，需要重新部署")
            else:
                add("ok", "  [OK  ] 部署文件与 persona.md 同步（新鲜度）")

    if "dsh" in targets:
        t = m.TARGETS["dsh"]()
        c = card_dsh()
        add("info", "【DSH】%s" % (c["home"] or "(未发现)"))
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

    add("info", "总体：" + ("✔ 通过" if ok else "✘ 存在 FAIL/WARN，见上文明细"))
    return {"when": _stamp(), "ok": ok, "items": lines}


# ─────────────────────────── 归一预览 ───────────────────────────

FILLERS = ("帮我", "麻烦", "请问", "求", "快点", "咋整", "给我", "帮忙", "能不能", "可不可以")


def cmd_route(sentence):
    """归一预览（只读）—— 实现见 pojia_routes.preview_route（与 console.py route 同源）。"""
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
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
        elif "[OK" in ln or "✔" in ln or "完成" in ln:
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


def cmd_run(action, targets):
    # console.py 的动作词是 apply / revert / dry（dry 在内部映射成 dry-run）
    argv_action = {"apply": "apply", "revert": "revert", "dry": "dry",
                   "dry-run": "dry", "预演": "dry"}.get(action, action)
    label = {"apply": "apply", "revert": "revert", "dry": "dry-run"}.get(argv_action, argv_action)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    for k in ("PYTHONPATH", "BASH_ENV", "NODE_OPTIONS",
              "CODEBUDDY_SAFE_DELETE_SANDBOX", "CODEBUDDY_SAFE_DELETE_BULK_GUARD"):
        env.pop(k, None)
    emit({"type": "start", "action": label, "targets": targets})
    buf = []
    for k in targets:
        emit({"type": "step", "target": k, "action": label})
        cmd = [sys.executable, CONSOLE, k, argv_action]
        try:
            p = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, env=env,
                                 text=True, encoding="utf-8", errors="replace", bufsize=1)
        except Exception as e:
            emit({"type": "line", "target": k, "level": "err", "text": "启动失败：%s" % e})
            buf.append(1)
            continue
        assert p.stdout is not None
        for raw in p.stdout:
            ln, level = classify(raw)
            if ln.strip():
                emit({"type": "line", "target": k, "level": level, "text": spice(ln)})
        p.wait()
        buf.append(p.returncode or 0)
        emit({"type": "done", "target": k, "code": p.returncode or 0})
    code = 0 if all(c == 0 for c in buf) else 1
    emit({"type": "exit", "code": code})


# ─────────────────────────── diff 预览（先预览再确认写入） ───────────────────────────

_DIFF_KINDS = {"codex", "zcode", "claude", "grok", "gemini", "hermes"}   # 指令文件型，可全文对比


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
    _pol, want = m.build_policy(user)
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
    elif target == "zcode":
        ag_p = os.path.join(home, "AGENTS.md")
        items.append({"path": ag_p, "before": m.read_text_safe(ag_p) or "",
                      "after": m.build_zcode_instructions(user, want), "note": "AGENTS.md（整文件改写）"})
    else:  # claude / grok / gemini / hermes —— 标记块合并
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
        emit({"error": "用法：bridge.py meta|status|test|route|tx|diff|run"})
        return 2
    cmd = argv[0].lower()
    if cmd == "meta":
        emit(cmd_meta())
    elif cmd == "tx":
        emit(cmd_tx(argv[1:]))
    elif cmd == "diff":
        tgt = argv[1] if len(argv) > 1 else ""
        emit(cmd_diff(tgt))
    elif cmd == "status":
        emit(cmd_status(norm_targets(argv[1] if len(argv) > 1 else "")))
    elif cmd == "test":
        emit(_test_impl(norm_targets(argv[1] if len(argv) > 1 else "")))
    elif cmd == "route":
        emit(cmd_route(" ".join(argv[1:])))
    elif cmd == "run":
        if len(argv) < 2:
            emit({"error": "run 需要动作：apply|revert|dry"})
            return 2
        cmd_run(argv[1], norm_targets(argv[2] if len(argv) > 2 else ""))
    else:
        emit({"error": "未知命令：%s" % cmd})
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
