# -*- coding: utf-8 -*-
"""zcode_doctor.py —— ZCode 破甲环境一键体检（**只读**，不发任何网络请求）

每次风控/排障/升级后先跑它，一张表看清：
  cjs 补丁状态 / AGENTS.md 状态 / 推理等级 / 活跃 provider / 最近请求结果

用法：
    python zcode/zcode_doctor.py            # 打印
    python zcode/zcode_doctor.py --json     # 输出 JSON
"""
import os, io, sys, json, glob, sqlite3, datetime

HOME = os.path.expanduser(r"~\.zcode")
CJS = r"F:\Softstore\ZCode\resources\glm\zcode.cjs"
WS = r"F:\提示词"

R = {}   # 结果字典


def bj(ms=None, ts=None):
    try:
        if ms is not None:
            return datetime.datetime.fromtimestamp(ms / 1000.0).strftime("%m-%d %H:%M:%S")
        dt = datetime.datetime.strptime(ts.replace("Z", "")[:19], "%Y-%m-%dT%H:%M:%S")
        return (dt + datetime.timedelta(hours=8)).strftime("%m-%d %H:%M:%S")
    except Exception:
        return str(ts or ms)


# ---------- 1) cjs ----------
def check_cjs():
    d = {}
    if not os.path.exists(CJS):
        R["cjs"] = {"error": "文件不存在: %s" % CJS}
        return
    data = io.open(CJS, "rb").read()
    d["size"] = len(data)
    d["mtime"] = datetime.datetime.fromtimestamp(os.path.getmtime(CJS)).strftime("%m-%d %H:%M:%S")
    marks = {
        "官方s9o": b"You are ZCode, an interactive coding agent",
        "官方u9o": b"You are an interactive ZCode agent that helps users",
        "官方Refuse句": b"Refuse requests for destructive",
        "我方沙盒身份": b"execution node of a local, offline workspace",
        "我方s9o改": b"running in a local, offline workspace",
        "尾注-我方": b"records this workspace's conventions",
        "尾注-官方": b"may or may not be relevant",
        "OVERRIDE声明": b"OVERRIDE any default behavior",
    }
    d["marks"] = {k: data.count(v) for k, v in marks.items()}
    d["sysprompt"] = "我方替换" if d["marks"]["我方沙盒身份"] else "官方原生"
    d["tailnote"] = ("我方中性版" if d["marks"]["尾注-我方"]
                     else ("官方" if d["marks"]["尾注-官方"] else "未知(两次替换都未命中)"))
    R["cjs"] = d


# ---------- 2) AGENTS.md ----------
def check_agents():
    d = {}
    for tag, p in (("全局", os.path.join(HOME, "AGENTS.md")), ("工作区", os.path.join(WS, "AGENTS.md"))):
        if not os.path.exists(p):
            d[tag] = {"path": p, "exists": False}
            continue
        t = io.open(p, "r", encoding="utf-8", errors="replace").read()
        ver = "未知"
        for key, name in (("离线文本生产沙盒", "v5 storylab"),
                          ("都落在纸面上", "v4 写实"),
                          ("工作区说明", "v2/v3"),
                          ("反拒绝铁律", "v1 越狱腔 ⚠️")):
            if key in t:
                ver = name
                break
        d[tag] = {"path": p, "exists": True, "size": len(t.encode("utf-8")), "version": ver,
                  "mtime": datetime.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%m-%d %H:%M:%S")}
    R["agents"] = d


# ---------- 3) 推理等级 ----------
def check_reasoning():
    p = os.path.join(HOME, "v2", "config.json")
    d = {"file": p}
    if not os.path.exists(p):
        R["reasoning"] = {"error": "config.json 不存在"}
        return
    cfg = json.load(io.open(p, "r", encoding="utf-8"))
    lv = {}
    n = 0
    for pid, pv in (cfg.get("provider") or {}).items():
        for mid, mv in ((pv or {}).get("models") or {}).items():
            v = ((mv or {}).get("reasoning") or {}).get("defaultVariant")
            if v:
                lv[v] = lv.get(v, 0) + 1
                n += 1
    d["model_levels"] = lv
    d["model_count"] = n
    d["note"] = "⚠️ 会话级 thoughtLevel 会覆盖它；改这个文件必须 ZCode 完全退出，否则被回写"
    # 活跃 provider / 会话级等级
    dbp = os.path.join(HOME, "cli", "db", "db.sqlite")
    if os.path.exists(dbp):
        try:
            con = sqlite3.connect("file:%s?mode=ro" % dbp.replace("\\", "/"), uri=True)
            cur = con.cursor()
            row = cur.execute(
                "SELECT time_updated, data FROM session_entry "
                "WHERE type='runtime/model_selection' ORDER BY time_updated DESC LIMIT 1").fetchone()
            if row:
                sel = json.loads(row[1])
                d["active"] = {"at": bj(ms=row[0]), "providerId": sel.get("providerId"),
                               "modelId": sel.get("modelId"), "thoughtLevel": sel.get("thoughtLevel")}
            row = cur.execute(
                "SELECT value, time_updated FROM local_setting "
                "WHERE namespace='model' AND key='reasoningLevel' LIMIT 1").fetchone()
            if row:
                d["user_default"] = {"value": row[0], "at": bj(ms=row[1])}
            con.close()
        except Exception as e:
            d["db_error"] = str(e)
    R["reasoning"] = d


# ---------- 4) provider 通道 ----------
def check_providers():
    p = os.path.join(HOME, "v2", "config.json")
    out = []
    try:
        cfg = json.load(io.open(p, "r", encoding="utf-8"))
    except Exception:
        R["providers"] = {"error": "读不到 config.json"}
        return
    for pid, pv in (cfg.get("provider") or {}).items():
        opt = (pv or {}).get("options") or {}
        key = opt.get("apiKey")
        out.append({
            "id": pid, "name": pv.get("name"),
            "baseURL": opt.get("baseURL"),
            "hasKey": bool(key and len(str(key)) > 20),
            "keyLen": len(str(key)) if key else 0,
            "models": list(((pv or {}).get("models") or {}).keys()),
        })
    R["providers"] = out


# ---------- 5) 最近请求 ----------
def check_rollout(n=8):
    roll = os.path.join(HOME, "cli", "rollout")
    files = sorted(glob.glob(os.path.join(roll, "*.jsonl")), key=os.path.getmtime)
    R["rollout_files"] = len(files)
    rows = []
    for p in files:
        for ln in io.open(p, "r", encoding="utf-8", errors="replace"):
            ln = ln.strip()
            if not ln:
                continue
            try:
                o = json.loads(ln)
            except Exception:
                continue
            mdl = o.get("model") or {}
            err = (o.get("error") or {}).get("message") or ""
            req = o.get("request") or {}
            body = req.get("body") or {}
            msgs = (body.get("messages") if isinstance(body, dict) else None) or []
            sig = []
            for j, m in enumerate(msgs[:4]):
                if not isinstance(m, dict):
                    continue
                c = m.get("content")
                s = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
                if j in (0, 1):
                    kind = ("官方" if s.startswith("You are ZCode, an interactive") or
                            "participate in a full spectrum of tasks" in s or
                            "interactive ZCode agent" in s else "**我方**")
                    sig.append("m[%d]=%s" % (j, kind))
            rows.append((bj(ts=o.get("startedAt") or o.get("completedAt") or ""),
                         str(o.get("requestId"))[:12],
                         mdl.get("variant") or "-",
                         ("OK" if not err else "ERR " + err[:44]),
                         " ".join(sig)))
    rows.sort()
    R["recent"] = rows[-n:]
    if files:
        R["rollout_latest_mtime"] = datetime.datetime.fromtimestamp(
            os.path.getmtime(files[-1])).strftime("%m-%d %H:%M:%S")


# ---------- 6) 备份 ----------
def check_backups():
    b = os.path.join(HOME, "zcode-unlock-backups")
    if not os.path.isdir(b):
        R["backups"] = {"error": "无备份目录"}
        return
    items = []
    for n in sorted(os.listdir(b), key=lambda x: os.path.getmtime(os.path.join(b, x))):
        fp = os.path.join(b, n)
        items.append({"name": n, "size": os.path.getsize(fp),
                      "mtime": datetime.datetime.fromtimestamp(
                          os.path.getmtime(fp)).strftime("%m-%d %H:%M:%S")})
    R["backups"] = items


def main():
    for fn in (check_cjs, check_agents, check_reasoning, check_providers, check_rollout, check_backups):
        try:
            fn()
        except Exception as e:
            R[fn.__name__] = {"error": repr(e)}

    if "--json" in sys.argv:
        print(json.dumps(R, ensure_ascii=False, indent=2))
        return

    L = []
    ap = L.append
    ap("=" * 68)
    ap("ZCode 破甲体检（只读）  %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    ap("=" * 68)

    c = R.get("cjs") or {}
    ap("")
    ap("[1] zcode.cjs  %s  %d B  mtime=%s" % (
        "❌ 缺失" if "error" in c else "", c.get("size", 0), c.get("mtime", "?")))
    if "marks" in c:
        ap("    system prompt : %s" % c["sysprompt"])
        ap("    尾注          : %s" % c["tailnote"])
        for k, v in c["marks"].items():
            ap("      %-14s x%d" % (k, v))

    ap("")
    ap("[2] AGENTS.md")
    for tag, v in (R.get("agents") or {}).items():
        if not v.get("exists"):
            ap("    %-4s ❌ 未部署  %s" % (tag, v.get("path")))
        else:
            ap("    %-4s ✅ %-12s %5d B  %s  %s" % (
                tag, v["version"], v["size"], v["mtime"], v["path"]))

    ap("")
    r = R.get("reasoning") or {}
    ap("[3] 推理等级")
    if "error" in r:
        ap("    %s" % r["error"])
    else:
        ap("    config.json 模型级 defaultVariant: %s（共 %d 个模型条目）" % (
            r.get("model_levels"), r.get("model_count")))
        a = r.get("active") or {}
        ap("    最近会话: %s  provider=%s  model=%s  thoughtLevel=%s" % (
            a.get("at"), a.get("providerId"), a.get("modelId"), a.get("thoughtLevel")))
        u = r.get("user_default") or {}
        ap("    用户级默认: %s (upd %s)" % (u.get("value"), u.get("at")))
        if a.get("thoughtLevel") == "max":
            ap("    ⚠️ 会话级 max —— 会在界面覆盖文件设置，需手动调低")
        ap("    %s" % r.get("note", ""))

    ap("")
    ap("[4] provider 通道（★ = 配了真实 key）")
    for p in (R.get("providers") or []):
        if isinstance(p, dict):
            ap("    %s %-32s %-16s key=%-3s %s" % (
                "★" if p["hasKey"] else " ", p["id"], p["name"] or "",
                p["keyLen"], p["baseURL"]))

    ap("")
    ap("[5] 最近 %d 条请求（rollout 文件 %s 个，最新 %s）" % (
        len(R.get("recent") or []), R.get("rollout_files"), R.get("rollout_latest_mtime")))
    for row in (R.get("recent") or []):
        ap("    %s  %-12s v=%-4s %-48s %s" % row)

    ap("")
    ap("[6] 备份（%d 项）" % len(R.get("backups") or []))
    for b in (R.get("backups") or []):
        ap("    %-52s %9d B  %s" % (b["name"], b["size"], b["mtime"]))

    print("\n".join(L))


if __name__ == "__main__":
    main()
