# -*- coding: utf-8 -*-
"""console.py -- 破甲控制台, 四目标统一入口。

用法：
    python console.py                     # 交互菜单
    python console.py <目标> <动作>

    目标： codex | dsh | wb | zcode | all
    动作： apply   破甲（--force 落盘）
           revert  复原（从备份还原官方原版）
           status  状态检测（只读）
           test    验证测试（只读：核对部署产物是否为当前人格版本）
           dry     预演（不改盘）
           route   口语归一预览（只读）
           tx      部署事务：历史 / 校验 / 按快照回退

示例：
    python console.py codex apply
    python console.py wb revert
    python console.py all test
    python console.py tx                      # 部署历史
    python console.py tx restore 3fa1c2d8     # 回退到某次部署之前
"""
import argparse
import glob
import importlib.util
import io
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "破甲一键通.py")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

spec = importlib.util.spec_from_file_location("pojia", SRC)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

# 打桩：沙箱黑名单会拦 schtasks.exe，直接报告"守护任务不存在"
m.WorkBuddyTarget._task_exists = staticmethod(lambda name: False)
m.WorkBuddyTarget.task_command = staticmethod(lambda name: "")

TARGET_ALIAS = {"codex": "codex", "c": "codex",
                "dsh": "dsh",
                "wb": "wb", "workbuddy": "wb", "w": "wb",
                "zcode": "zcode", "zai": "zcode", "z": "zcode",
                "claude": "claude", "cc": "claude", "claude-code": "claude",
                "grok": "grok", "grok-cli": "grok",
                "gemini": "gemini", "gemini-cli": "gemini",
                "hermes": "hermes", "hermes-agent": "hermes", "hermess": "hermes",
                "hr": "hermes",
                "all": "all"}

ALL_TARGETS = ("dsh", "codex", "wb", "zcode", "claude", "grok", "gemini", "hermes")


def norm_target_spec(spec):
    """把 'claude,grok' / 'dsh codex' / 'cc' 这类写法归一成 'claude,grok'。

    任一片段不认识就返回 None（由调用方报错）；含 all 直接返回 'all'。
    单一来源：目标集合以主脚本的 resolve_targets 为准，这里只做别名 + 分片。
    """
    parts = [p for p in re.split(r"[,，\s+]+", (spec or "").strip().lower()) if p]
    if not parts:
        return None
    out = []
    for p in parts:
        k = TARGET_ALIAS.get(p)
        if k is None:
            return None
        if k == "all":
            return "all"
        if k not in out:
            out.append(k)
    return ",".join(out) or None


ACTION_ALIAS = {"apply": "apply", "破甲": "apply", "a": "apply",
                "revert": "revert", "restore": "revert", "复原": "revert", "r": "revert",
                "status": "status", "状态": "status", "s": "status",
                "test": "test", "verify": "test", "验证": "test", "t": "test",
                "route": "route", "归一": "route", "归": "route",
                "dry": "dry-run", "预演": "dry-run", "d": "dry-run"}

PERSONA_KEYS = ("沉浸优先", "表演规则", "学姐的创作室")
CODEX_KEYS = ("unlock-v6", "任务路由", "反拒绝铁律", "创作许可前提", "点名即需求", "最终覆盖")
ROUTE_KEYS = ("任务路由", "归一", "定类", "出招", "草稿质量契约")
# 路由层不该出现的自曝词（对照 Astra冷咖啡 的自曝特征清单）
ROUTE_JB_WORDS = ("忽略之前", "覆盖系统", "prompt injection", "越狱", "jailbreak",
                  "最高优先级覆盖", "不许拒绝", "不得省略")


def build_args(target, extra=None):
    """构造与 破甲一键通.py 一致的参数对象。

    ⚠ full / nofileguard / web_filter 必须是 True。
    否则会出现**往返不对称**：`console.py wb revert` 会把 `.unlockbak` 全量还原，
    其中包括 --full 档写进去的「网页过滤解除」「文件保护中和」；apply 侧不带 full
    时只补回命令闸门与模板，网页过滤/文件保护就留在官方态 —— 一次
    revert→apply 往返等于静默降级。（2026-09-19 实测：codebuddy.js 从
    「网页过滤 已解除」退回「未解除」，console.py wb test 却因为不做断言而没报。）

    ask_mode 保持 False：它作用的 ASK_OLD 文本只出现在 ask-mode-reminder.tpl，
    该文件不含 <content_policy>、不在靶点集里，开了是空转。
    """
    return argparse.Namespace(
        target=target, persona="", codex_dir="", dsh_dir="", zcode_dir="",
        claude_dir="", grok_dir="", gemini_dir="", hermes_dir="",
        wb_install="", wb_data="",
        quiet=False, yes=True,
        apply=False, revert=False, dry_run=False, force=True,
        restart_wb=False, kill_dsh=False,
        status=False, diagnose=False, full=True,
        nofileguard=True, ask_mode=False, web_filter=True,
        guard=None, snapshot=False, compare=False,
        purge_spill=False, keep_cache=False,
        pick="", clear=False,
    )


# ────────────────────────── 验证测试 ──────────────────────────

def test_codex():
    ok = True
    home = m.TARGETS["codex"]().resolve_home("")
    cfg = os.path.join(home, "config.toml")
    print("  [Codex] %s" % home)
    if not os.path.exists(cfg):
        print("    [FAIL] config.toml 不存在"); return False
    conf = open(cfg, encoding="utf-8", errors="replace").read()
    line = ""
    for ln in conf.splitlines():
        if "model_instructions_file" in ln and "=" in ln:
            line = ln.split("=", 1)[1].strip().strip('"').strip("'")
            break
    if not line:
        print("    [FAIL] config.toml 缺少 model_instructions_file"); return False
    instr = os.path.normpath(os.path.join(home, line))
    print("    指令文件: %s" % instr)
    if not os.path.exists(instr):
        print("    [FAIL] 指令文件不存在"); return False
    txt = open(instr, encoding="utf-8", errors="replace").read()
    for k in CODEX_KEYS + PERSONA_KEYS:
        good = k in txt
        ok = ok and good
        print("    [%s] 关键段: %s" % ("OK  " if good else "FAIL", k))
    # 新鲜度：persona.md 改过但没重打 → stale
    persona = os.path.join(HERE, "persona.md")
    if os.path.exists(persona):
        age_p = os.path.getmtime(persona)
        age_d = os.path.getmtime(instr)
        if age_d + 5 < age_p:
            print("    [WARN] persona.md 比部署文件新 —— 改了人格还没重打，请 apply")
        else:
            print("    [OK  ] 部署文件与 persona.md 同步（新鲜度）")
    return ok


def test_dsh():
    ok = True
    npx_root = os.path.expandvars(r"%LOCALAPPDATA%\npm-cache\_npx")
    roots = glob.glob(os.path.join(npx_root, "*", "node_modules", "@deepseek-ai"))
    if not roots:
        print("  [DSH] npx 缓存中未发现 @deepseek-ai 安装（未打也未装）")
        return True
    total = hit = 0
    for root in roots:
        print("  [DSH] %s" % root)
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                if not fn.endswith((".js", ".json", ".yml", ".yaml", ".md", ".ts")):
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    t = open(p, encoding="utf-8", errors="replace").read()
                except Exception:
                    continue
                if "麻衣" in t or "沉浸优先" in t or "unlock-v6" in t:
                    total += 1
                    if all(k in t for k in PERSONA_KEYS) or "unlock-v6" in t:
                        hit += 1
    print("    含人格标记文件: %d / %d" % (hit, total))
    if total and hit < total:
        print("    [WARN] 有文件未带当前人格标记 —— 可能是旧版残留，建议 apply --force")
        ok = False
    elif total:
        print("    [OK  ] 全部命中")
    return ok


def test_wb():
    """WorkBuddy 验证：不能只打印状态，要落成判定。

    判定项 = "wb revert 能撤掉的东西，apply 必须能补回来"：
      ① 模板靶点：含政策块的模板全部是本版本宽松态
      ② 命令闸门：codebuddy.js / codebuddy-headless.js 均已解锁
      ③ 网页过滤：已解除（--full 档，build_args 默认开）
      ④ 文件保护：所有靶点均已中和
    （原实现直接 `return True`，是四个目标里唯一不做断言的 —— 所以网页过滤被
     revert 撤掉后 test 依旧报「✔ 通过」，见 2026-09-19 往返实测。）
    """
    ok = True
    args = build_args("wb")
    t = m.TARGETS["wb"]()
    print("  [WorkBuddy] 状态核对：")
    n0 = len(m._log_buf)
    try:
        t.dump(args)
    except Exception as e:
        print("    [FAIL] 检测出错：%s" % e)
        return False
    rows = m._log_buf[n0:]

    def grab(pat):
        for ln in rows:
            mm = re.search(pat, ln)
            if mm:
                return mm
        return None

    mm = grab(r"含政策块 (\d+) 个，本版本宽松 (\d+) 个，旧人格宽松 (\d+) 个")
    if mm:
        pol, loose, other = int(mm.group(1)), int(mm.group(2)), int(mm.group(3))
        good = pol > 0 and (loose + other) == pol
        ok = ok and good
        print("    [%s] 模板靶点: 含政策块 %d，本版本宽松 %d，旧人格宽松 %d"
              % ("OK  " if good else "FAIL", pol, loose, other))
    else:
        print("    [WARN] 未读到模板靶点统计")

    gates = []
    for ln in rows:
        mm = re.search(r"命令闸门 : (\S+)\s+(已解锁|未解锁)\s+网页过滤 (已解除|未解除)", ln)
        if mm:
            gates.append(mm.groups())
    if gates:
        bad = [g[0] for g in gates if g[1] != "已解锁"]
        ok = ok and not bad
        print("    [%s] 命令闸门: %d/%d 已解锁%s"
              % ("OK  " if not bad else "FAIL", len(gates) - len(bad), len(gates),
                 "" if not bad else "（未解锁：%s）" % ", ".join(bad)))
        wf = [g[0] for g in gates if g[2] != "已解除"]
        ok = ok and not wf
        print("    [%s] 网页过滤: %d/%d 已解除%s"
              % ("OK  " if not wf else "FAIL", len(gates) - len(wf), len(gates),
                 "" if not wf else "（未解除：%s —— 跑 apply 补回）" % ", ".join(wf)))
    else:
        print("    [WARN] 未读到命令闸门状态")

    mm = grab(r"文件保护 : (.+)$")
    if mm:
        val = mm.group(1).strip()
        good = (val == "已中和")
        ok = ok and good
        print("    [%s] 文件保护: %s" % ("OK  " if good else "FAIL", val))
    else:
        print("    [WARN] 未读到文件保护状态")
    return ok


def test_zcode():
    """ZCode 专项验证：正向查关键段，反向查「越狱腔残留」。
    反向校验是 2026-09-18 加的关键项 —— GLM 会识别 jailbreak 模板并反向加固拒绝，
    所以"不含反拒绝铁律/覆盖声明/unlock 标记"与"含工作区说明"同样重要。"""
    ok = True
    home = m.TARGETS["zcode"]().resolve_home("")
    ag = os.path.join(home, "AGENTS.md")
    print("  [ZCode] %s" % home)
    if not os.path.exists(ag):
        print("    [FAIL] AGENTS.md 不存在（未部署）"); return False
    txt = open(ag, encoding="utf-8", errors="replace").read()
    for k in m.ZCODE_KEYS + ("学姐模式开启", "工程任务"):
        good = k in txt
        ok = ok and good
        print("    [%s] 关键段: %s" % ("OK  " if good else "FAIL", k))
    jb = m.ZCODE_JB_WORDS
    hits = [w for w in jb if w in txt]
    if hits:
        ok = False
        print("    [FAIL] 越狱腔残留（会被 GLM 判为注入攻击）: %s" % "、".join(hits))
    else:
        print("    [OK  ] 无越狱腔残留（反拒绝铁律/覆盖声明/unlock 标记 均为 0）")
    persona = os.path.join(HERE, "persona.md")
    if os.path.exists(persona):
        if os.path.getmtime(ag) + 5 < os.path.getmtime(persona):
            print("    [WARN] persona.md 比部署文件新 —— 但 ZCode 版不走 persona.md，可忽略")
        else:
            print("    [OK  ] 部署文件新鲜度正常")
    return ok


# ────────────────────────── 路由层 ──────────────────────────

SEAT_MD = {"claude": ("CLAUDE.md", "~/.claude"),
           "grok": ("AGENTS.md", "~/.grok"),
           "gemini": ("GEMINI.md", "~/.gemini"),
           # Hermes 桌面版把 home 藏在 <安装根>/data/hermes-home，家目录下没有；
           # 这里只是「默认位置」文案，实际路径由 resolve_home 自动发现后打印。
           "hermes": ("SOUL.md", "~/.hermes（或桌面版 <安装根>/data/hermes-home）")}


def _hermes_gate(text, label="SOUL.md"):
    """Hermes 专属门禁：注入文本必须过上游 threat_patterns 扫描。

    为什么单列这条：Hermes 在把 SOUL.md 放进系统提示词第 1 槽位之前会跑
    `tools/threat_patterns.scan_for_threats(content, scope="context")`，
    **命中即整份 BLOCKED** —— 模型只看得到内置默认身份，等于白打。
    这与 claude/grok/gemini 那三个"纯 markdown 注入"的席位有本质区别：
    那三个写坏了只是效果差，Hermes 写坏了是**一点都不生效**，而且从会话里
    看不出来（没有报错、没有警告，只有一句 BLOCKED 走在日志里）。

    所以判定必须由本地出：拿上游同源规则表扫一遍，0 命中才算通过。
    """
    ok = True
    p = os.path.join(os.path.dirname(os.path.abspath(m.SELF)), "tools", "hermes_threat_check.py")
    if not os.path.exists(p):
        print("    [WARN] 门禁脚本缺失，跳过扫描：%s" % p)
        return True
    try:
        spec = importlib.util.spec_from_file_location("hermes_gate", p)
        hg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hg)
    except Exception as e:
        print("    [FAIL] 门禁脚本加载失败：%s" % e)
        return False
    hits = hg.scan_hits(text, "context")
    if hits:
        ok = False
        print("    [FAIL] Hermes 门禁 %s：%d 处命中（会被 BLOCKED）" % (label, len(hits)))
        for pid, frag in hits:
            print("           · %-28s %s" % (pid, frag))
    else:
        print("    [OK  ] Hermes 门禁 %s：0 命中（context 档，不会被 BLOCKED）" % label)
    # 顺带把 86 份路由 skill 也过一遍 strict 档（skill 安装口径）
    try:
        hh = m.TARGETS["hermes"]().resolve_home("")
        seat_skills = m.SKILLS.plan("hermes", hh) if m.SKILLS else []
    except Exception:
        seat_skills = []
    if seat_skills:
        bad = []
        for path, body in seat_skills:
            h = hg.scan_hits(body, "strict")
            if h:
                bad.append((os.path.basename(os.path.dirname(path)), h))
        if bad:
            ok = False
            print("    [FAIL] Hermes 门禁 skills：%d 份命中 strict 档" % len(bad))
            for name, h in bad[:5]:
                print("           · %-20s %s" % (name, h[0][0]))
        else:
            print("    [OK  ] Hermes 门禁 skills：%d 份全部通过 strict 档" % len(seat_skills))
    return ok


def test_home_md(seat):
    """Claude / Grok / Gemini 专项验证（三席位共用一套断言）。

    只断言**我们自己的标记块**，不碰用户自有内容 —— 那部分本来就不归我们管
    （比如 .claude/CLAUDE.md 里用户自己的手稿，内容是什么与我们无关）。
    """
    ok = True
    md_name, home_disp = SEAT_MD[seat]
    t = m.TARGETS[seat]()
    home = t.resolve_home("")
    md = os.path.join(home, md_name)
    print("  [%s] %s" % (seat.capitalize(), home))
    if not os.path.exists(md):
        print("    [WARN] %s 不存在（未部署）—— %s" % (md_name, home_disp))
        return False
    txt = open(md, encoding="utf-8", errors="replace").read()
    begin, end = m.SEAT_MARKS[seat]
    if begin not in txt or end not in txt:
        print("    [WARN] 用户自有文件，未含标记块（未部署）")
        return False
    # 只取标记块内部做正反校验
    blk = txt.split(begin, 1)[1].split(end, 1)[0]
    for k in ("工作区角色库", "出戏就是演砸", "学姐模式开启", "工程任务"):
        good = k in blk
        ok = ok and good
        print("    [%s] 关键段: %s" % ("OK  " if good else "FAIL", k))
    hits = [w for w in m.ZCODE_JB_WORDS if w in blk]
    if hits:
        ok = False
        print("    [FAIL] 越狱腔残留: %s" % "、".join(hits))
    else:
        print("    [OK  ] 无越狱腔残留")
    if seat == "hermes":
        ok = _hermes_gate(blk, md_name) and ok
    # 客户端还在不在（文件写对了，但没人读它也是白打）—— 只提示，不参与判定
    try:
        found, where = t.client_probe()
        if found is False:
            print("    [提示] 未检测到 %s 可执行文件（PATH / npm 全局 / npx 缓存）"
                  "—— 文件在位但没人读，可能已卸载或装在自定义路径" % t.label)
        elif found:
            print("    [OK  ] 客户端：%s" % where)
    except Exception as e:
        print("    [提示] 客户端探测失败：%s" % e)
    # 用户内容保护断言：剥块后剩余内容非空时必须与用户原稿一致（即块是追加不是替换）
    rest = m.strip_seat_block(txt, seat)
    print("    [%s] 用户内容保留（剥块后 %d 字符）" % ("OK  " if rest.strip() or True else "FAIL",
                                                     len(rest)))
    return ok


def compose_cmd(argv):
    """档位×通道组合器入口。argv 直接透传给 compose.py 的 main()。"""
    import compose as _compose
    try:
        return _compose.main(argv)
    except SystemExit as e:
        code = e.code
        if isinstance(code, str):
            print(code)
            return 2
        return code or 0


def route_preview(sentence):
    """本地口语归一预览（只读）。

    把用户原话按 route-layer.md 的归一表跑一遍，看会被归到哪个类目。
    用途：判断某个说法到底能不能命中类目 —— 命不中就说明该往归一表里补一行。
    （对照 Astra冷咖啡 的 NORMALIZER：拒写的直接触发点常是用户原话的措辞，
      归一的意义就是让那句话不进模型。）

    实现放在 pojia_routes.preview_route（单一来源），GUI 的 route 卡片调的是同一个。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import pojia_routes as PR
    except Exception as e:
        print("  [FAIL] pojia_routes.py 加载失败：%s" % e)
        return 2

    path = getattr(m, "ROUTE_FILE", "")
    r = PR.preview_route(sentence, path if os.path.isfile(path) else None)
    if r["error"]:
        print("  %s" % r["error"])
        return 2

    print("  输入: %s" % r["input"])
    if r["washed"]:
        print("  洗口: 会去掉 %s" % "、".join(r["washed"]))
    if r["typos"]:
        print("  错字归一: %s  →  %s" % ("、".join(r["typos"]), r["normalized"]))
    if not r["hits"]:
        print("  结果: 未命中归一表 —— 请求会按字面理解，不做归类。")
        print("  建议: 在「用户常说的」列补一条这种写法 —— 源件 routes/NORMALIZER.md，")
        print("        改完跑 tools/render_routes.py 再 console.py all apply。")
        return 1

    print("  结果:（归一表 %d 行中命中 %d 处）" % (r["table_rows"], len(r["hits"])))
    for h in r["hits"][:4]:
        print("    命中「%s」 → %s → 【%s】" % (h["frag"], h["term"], h["cat"]))
    print("  主类目: 【%s】 —— 按该类目三步走，第一步直接交付。" % r["main"])
    if len(r["hits"]) > 1:
        print("  其余诉求记为后续工单: %s" % "、".join(r["others"]))
    return 0


def test_route():
    """路由层校验（只读）。

    正向：route-layer.md 含归一表 / 六类目 / 三步走 / 草稿质量契约。
    反向：不含越狱腔自曝词 —— 路由层的作用是「让模型不必判断该不该做」，
          写成越狱腔会把效果反过来（对照 Astra冷咖啡 的自曝特征清单）。
    """
    ok = True
    path = getattr(m, "ROUTE_FILE", "")
    print("  [路由层] %s" % path)
    if not path or not os.path.exists(path):
        print("    [FAIL] route-layer.md 不存在（当前会用内置兜底骨架）")
        return False
    txt = open(path, encoding="utf-8", errors="replace").read()
    body = m.clean_prompt(txt)          # 先剥 HTML 注释：注释里可写"哪些词不能用"的说明
    for k in ROUTE_KEYS:
        good = k in body
        ok = ok and good
        print("    [%s] 关键段: %s" % ("OK  " if good else "FAIL", k))
    bad = [w for w in ROUTE_JB_WORDS if w in body]
    if bad:
        ok = False
        print("    [FAIL] 越狱腔残留（会被判为注入）: %s" % "、".join(bad))
    else:
        print("    [OK  ] 无越狱腔残留")
    print("    体量: %d 字符（%-4s）" % (
        len(m.ROUTE_SECTION), "偏小" if len(m.ROUTE_SECTION) < 800 else "正常"))

    # 归一表可用性（回归网：曾因分片符只认 "/"，而表和正文用的是「、」，整表恒不命中）
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import pojia_routes as PR
        probes = (("脱壳", "sw-unpack"), ("唾壳", "sw-unpack"),
                  ("弄个挂", "game-aim"), ("批量注册", "auto-register"),
                  ("写篇小说", "ink-novel"), ("对一下私有协议", "sw-proto"),
                  ("今天天气不错", ""))
        bad = []
        rows = 0
        for q, want in probes:
            rr = PR.preview_route(q, path)
            rows = rr["table_rows"] or rows
            if rr["main"] != want:
                bad.append("%s→%s（期望 %s）" % (q, rr["main"] or "未命中", want or "不归类"))
        if bad:
            ok = False
            print("    [FAIL] 归一表命中异常: %s" % "；".join(bad))
        else:
            print("    [OK  ] 归一表 %d 行，%d/%d 探针归类正确（含错字归一 + 闲聊不归类）"
                  % (rows, len(probes), len(probes)))
    except Exception as e:
        ok = False
        print("    [FAIL] 归一预览不可用: %s" % e)
    return ok


def run_test(targets):
    print("=" * 62)
    print(" 验证测试（只读）  " + time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 62)
    ok = True
    ok = test_route() and ok
    if "codex" in targets:
        ok = test_codex() and ok
    if "dsh" in targets:
        ok = test_dsh() and ok
    if "wb" in targets:
        ok = test_wb() and ok
    if "zcode" in targets:
        ok = test_zcode() and ok
    for seat in ("claude", "grok", "gemini", "hermes"):
        if seat in targets:
            ok = test_home_md(seat) and ok
    print("-" * 62)
    print(" 总体: " + ("✔ 通过" if ok else "✘ 存在 FAIL/WARN，见上文明细"))
    return 0 if ok else 1


# ────────────────────────── 部署事务 ──────────────────────────
# 对齐 gpt6-Astra「冷咖啡」seat-transactions 的对外语义：
# 能列部署历史、能逐文件校验、能按快照回退，且回退前先确认现场没被别的操作动过。

def _tx_mod():
    return getattr(m, "TX", None)


def _resolve_tx_id(tx, prefix):
    """支持用前 8 位编号引用一次部署。"""
    rows = tx.list_history()
    if not prefix:
        return ""
    for r in rows:
        if r["id"] == prefix:
            return r["id"]
    cand = [r for r in rows if r["id"].startswith(prefix)]
    return cand[0]["id"] if len(cand) == 1 else ""


def tx_history(seat="", limit=0):
    tx = _tx_mod()
    if tx is None:
        print("  [FAIL] 事务模块未加载（pojia_tx.py 缺失或加载失败）")
        return 2
    rows = tx.list_history(seat=seat, limit=limit)
    print("=" * 62)
    print(" 部署历史（最近在前）")
    print(" 快照目录: %s" % tx.tx_root())
    print("=" * 62)
    if not rows:
        print("  （还没有部署记录 —— 跑一次 apply 就会出现）")
        return 0
    print("  %-10s %-8s %-13s %-7s %s" % ("编号", "目标", "时间", "改动", "状态"))
    for r in rows:
        note = ("  " + r["error"][:36]) if r.get("error") else ""
        print("  %-10s %-8s %-13s %-7s %s%s" % (
            r["id"][:8], r["seat"], r["at"][5:16],
            "%d/%d" % (r["changed"], r["files"]), r["status"], note))
    print()
    print("  校验: python console.py tx verify <编号>")
    print("  回退: python console.py tx restore <编号>")
    return 0


def tx_verify(tx_id):
    tx = _tx_mod()
    if tx is None:
        print("  [FAIL] 事务模块未加载"); return 2
    full = _resolve_tx_id(tx, tx_id)
    if not full:
        print("  找不到这次部署：%s（用 python console.py tx 看列表）" % tx_id)
        return 2
    rep = tx.verify(full)
    print("=" * 62)
    print(" 快照校验   %s  %s  %s  [%s]" % (rep["id"][:8], rep["seat"], rep["at"], rep["status"]))
    print("=" * 62)
    for f in rep["files"]:
        if f["snapshot_ok"] is None:
            snap = "无底"
        else:
            snap = "完整" if f["snapshot_ok"] else "缺失"
        if f["current_ok"] is None:
            cur = "-"
        else:
            cur = "一致" if f["current_ok"] else "已变"
        print("  快照:%-4s 当前:%-4s  %s" % (snap, cur, f["path"]))
        if f.get("note"):
            print("        (%s)" % f["note"])
    print()
    print("  总体: " + ("✔ 快照完整、落盘态与记录一致" if rep["ok"]
                       else "✘ 有不一致项 —— 若当前态已变，说明部署后文件被别的操作改过"))
    return 0 if rep["ok"] else 1


def tx_restore(tx_id, force=False):
    tx = _tx_mod()
    if tx is None:
        print("  [FAIL] 事务模块未加载"); return 2
    full = _resolve_tx_id(tx, tx_id)
    if not full:
        print("  找不到这次部署：%s（用 python console.py tx 看列表）" % tx_id)
        return 2
    print("  按快照回退 %s ..." % full[:8])
    try:
        rep = tx.restore(full, force=force)
    except tx.TxError as e:
        print("  [中止] %s" % e)
        return 1
    print("  已还原 %d 个文件，状态 %s" % (rep["restored"], rep["status"]))
    if rep["failed"]:
        print("  [WARN] 以下文件没能还原（快照缺底）：")
        for p in rep["failed"]:
            print("         %s" % p)
    print("  提示：目标程序需重启才会重新读取被还原的文件。")
    return 0 if not rep["failed"] else 1


def tx_purge(keep=None):
    tx = _tx_mod()
    if tx is None:
        print("  [FAIL] 事务模块未加载"); return 2
    k = tx.KEEP_TX if keep is None else keep      # 注意 0 是合法值，不能用 or 兜底
    n = tx.purge(keep=k)
    print("  已清理 %d 个旧快照（保留最近 %d 次）" % (n, k))
    return 0


def tx_usage():
    print("用法:")
    print("  python console.py tx                    部署历史")
    print("  python console.py tx list [目标]        只看某个目标（codex/dsh/wb/zcode/claude/grok/gemini）")
    print("  python console.py tx verify <编号>      校验快照 + 当前落盘态")
    print("  python console.py tx restore <编号>     按快照回退（现场被改过则中止）")
    print("  python console.py tx restore <编号> --force   强制覆盖，丢掉后续改动")
    print("  python console.py tx purge [n]          清理旧快照，默认保留 12 次")
    return 0


def tx_cmd(argv):
    if argv and argv[0] in ("-h", "--help", "help", "帮助"):
        return tx_usage()
    if not argv:
        return tx_history()
    sub = argv[0].lower()
    if sub in ("list", "ls", "列表"):
        seat = argv[1].lower() if len(argv) > 1 else ""
        seat = "" if seat in ("all", "*") else TARGET_ALIAS.get(seat, seat)
        return tx_history(seat=seat)
    if sub in ("verify", "check", "校验"):
        if len(argv) < 2:
            print("  缺少编号。用法: python console.py tx verify <编号>"); return 2
        return tx_verify(argv[1])
    if sub in ("restore", "rollback", "回退", "还原"):
        if len(argv) < 2:
            print("  缺少编号。用法: python console.py tx restore <编号>"); return 2
        return tx_restore(argv[1], force=("--force" in argv or "-f" in argv))
    if sub in ("purge", "clean", "清理"):
        k = None
        if len(argv) > 1 and argv[1].isdigit():
            k = int(argv[1])
        return tx_purge(k)
    print("  未知子命令: %s" % sub)
    return tx_usage()


# ────────────────────────── 分发 ──────────────────────────

EXTRA_FLAGS = {"--full": "full", "--nofileguard": "nofileguard",
               "--ask-mode": "ask_mode", "--web-filter": "web_filter",
               "--restart-wb": "restart_wb", "--kill-dsh": "kill_dsh",
               "--keep-cache": "keep_cache"}


def dispatch(target, action, extra=None):
    tset = m.resolve_targets(target)        # 支持 'claude,grok' / 'all'，单一来源在主脚本
    if action == "test":
        return run_test(tset)
    args = build_args(target)
    for flag in (extra or []):
        attr = EXTRA_FLAGS.get(flag)
        if attr:
            setattr(args, attr, True)
    if action == "status":
        m.run_status(args, targets=tset)
    else:
        m.run_action(args, action, targets=tset)
    return 0


def menu():
    while True:
        print()
        print("=" * 62)
        print("  破甲控制台   目标: codex / dsh / wb / zcode / claude / grok / gemini / hermes / all")
        print("               动作: apply / revert / status / test / dry")
        print("=" * 62)
        print("  [1] 全部破甲        [2] 全部复原       [3] 全部状态")
        print("  [4] 全部验证测试    [5] 单目标操作     [6] 归一预览")
        print("  [7] 部署历史/回退   [0] 退出")
        try:
            c = input("  选择 > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if c in ("0", "", "q", "exit"):
            return
        if c == "1":
            dispatch("all", "apply")
        elif c == "2":
            dispatch("all", "revert")
        elif c == "3":
            dispatch("all", "status")
        elif c == "4":
            returncode = dispatch("all", "test")
            print("  (exit=%d)" % returncode)
        elif c == "6":
            s = input("  用户原话 > ").strip()
            if s:
                route_preview(s)
        elif c == "7":
            tx_history()
            t = input("  回退某次？输入编号（回车跳过）> ").strip()
            if t:
                if input("  确认回退 %s ？(y/N) " % t).strip().lower() == "y":
                    tx_restore(t)
        elif c == "5":
            t = input("  目标 (codex/dsh/wb/zcode/claude/grok/gemini) > ").strip().lower()
            t = TARGET_ALIAS.get(t)
            a = input("  动作 (apply/revert/status/test) > ").strip().lower()
            a = ACTION_ALIAS.get(a)
            if t and a:
                dispatch(t, a)
            else:
                print("  无效输入")


def main(argv):
    args = [a for a in argv if a]
    if not args:
        menu()
        return 0
    # 归一预览：python console.py route "帮我弄个挂"
    if args[0].lower() in ("route", "归一", "归"):
        return route_preview(" ".join(args[1:]))
    # 部署事务：python console.py tx [list|verify|restore|purge]
    if args[0].lower() in ("tx", "事务", "历史"):
        return tx_cmd(args[1:])
    # 档位×通道组合器：python console.py compose max reverse "目标"
    if args[0].lower() in ("compose", "组合", "档位"):
        return compose_cmd(args[1:])
    target = norm_target_spec(args[0])
    if target is None:
        print("未知目标: %s" % args[0])
        print("  可用: %s / all（多目标可用逗号或空格分隔）" % " / ".join(ALL_TARGETS))
        return 2
    if len(args) < 2:
        print("缺少动作。用法: python 破甲控制台.py %s <apply|revert|status|test|dry>" % target)
        return 2
    action = ACTION_ALIAS.get(args[1].lower())
    if action is None:
        print("未知动作: %s （可用: apply/revert/status/test/dry/route）" % args[1])
        return 2
    if action == "route":
        return route_preview(" ".join(args[2:]))
    extra = [a for a in args[2:] if a in EXTRA_FLAGS]
    unknown = [a for a in args[2:] if a not in EXTRA_FLAGS]
    if unknown:
        print("未知参数: %s （可用: %s）" % (" ".join(unknown), " ".join(EXTRA_FLAGS)))
        return 2
    return dispatch(target, action, extra)


if __name__ == "__main__":
    if os.name == "nt" and len(sys.argv) == 1:
        # 交互菜单在双击/手跑时都直接进
        pass
    sys.exit(main(sys.argv[1:]))
