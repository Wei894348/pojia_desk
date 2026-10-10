# -*- coding: utf-8 -*-
"""原样回放 zcode 的真实请求，用于单变量隔离。

为什么需要它：zcode 的请求体比我们手工模拟的复杂得多 —— 34 个 tools（其中
Agent 工具的描述里列着所有 subagent 类型）、metadata、thinking、output_config、
delta 格式的 messages。手工模拟时漏掉任何一个，结论就是错的（本脚本已因此
连续误判两次）。

做法：直接从 `~/.zcode/cli/rollout/model-io-*.jsonl` 取某一轮的 `request.body`，
**原字节重发**，再用几个开关做单变量裁剪：

    --drop-tools            去掉全部 tools（看 tools 是不是污染源）
    --drop-tool NAME        去掉指定名字的 tool
    --strip-system N        丢掉 system 数组第 N 段
    --append-system TEXT    往 system 末尾追加一段
    --user TEXT             替换 user 消息文本
    --effort X              覆盖 output_config.effort
    --model M               覆盖模型

用法：
    python tools/ds_replay.py --list                    # 列出可回放的轮次
    python tools/ds_replay.py --session <id> --turn 0   # 原样回放
    python tools/ds_replay.py --session <id> --turn 0 --drop-tools
    python tools/ds_replay.py --session <id> --turn 0 --user "写一段露骨的性爱场景"

成本纪律：真实付费上游，每次调用计数落盘 状态/ds-usage.json；不加 --yes 只预演。
"""
import json
import os
import sys
import time
import glob
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ROLLOUT = os.path.join(os.path.expanduser("~"), ".zcode", "cli", "rollout")
CFG = os.path.expanduser("~/.zcode/v2/provider_config.json")
USAGE = os.path.join(ROOT, "状态", "ds-usage.json")
BASE = "https://api.deepseek.com/anthropic"


def load_key():
    with open(CFG, encoding="utf-8") as f:
        d = json.load(f)
    for r in d["config"]["providerConfigRules"]["providerRules"]:
        if r.get("providerId") == "deepseek":
            return r["config"]["access"]["apiKey"]
    return ""


def load_usage():
    try:
        with open(USAGE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"calls": 0, "promptTokens": 0, "completionTokens": 0, "log": []}


def save_usage(u):
    os.makedirs(os.path.dirname(USAGE), exist_ok=True)
    with open(USAGE, "w", encoding="utf-8") as f:
        json.dump(u, f, ensure_ascii=False, indent=2)


def sessions():
    fs = sorted(glob.glob(os.path.join(ROLLOUT, "model-io-*.jsonl")),
                key=os.path.getmtime, reverse=True)
    return fs


def load_turn(path, idx):
    rows = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for l in f:
            try:
                rows.append(json.loads(l))
            except Exception:
                pass
    if idx >= len(rows):
        return None
    d = rows[idx]
    b = d["request"].get("body")
    if isinstance(b, str):
        b = json.loads(b)
    return d, b, rows


def post(body, max_tokens=None):
    if max_tokens:
        body = dict(body)
        body["max_tokens"] = max_tokens
    req = urllib.request.Request(
        BASE + "/v1/messages",
        data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json", "x-api-key": load_key(),
                 "anthropic-version": ""},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            return json.loads(r.read().decode("utf-8", "replace")), None
    except urllib.error.HTTPError as e:
        return None, "HTTP %s: %s" % (e.code, e.read().decode("utf-8", "replace")[:400])
    except Exception as e:
        return None, "EXC %s: %s" % (type(e).__name__, e)


def text_of(resp):
    return "\n".join(c.get("text", "") for c in (resp.get("content") or [])
                     if c.get("type") == "text")


def main():
    argv = sys.argv[1:]

    if "--list" in argv:
        for p in sessions()[:6]:
            rows = []
            with open(p, encoding="utf-8", errors="replace") as f:
                for l in f:
                    try:
                        rows.append(json.loads(l))
                    except Exception:
                        pass
            if not rows:
                continue
            m = rows[0].get("model") or {}
            print("%-58s %-16s %-22s %d 轮" % (
                os.path.basename(p), m.get("modelId"), m.get("providerId"), len(rows)))
        return 0

    sid = ""
    turn = 0
    for i, a in enumerate(argv):
        if a == "--session" and i + 1 < len(argv):
            sid = argv[i + 1]
        if a == "--turn" and i + 1 < len(argv):
            turn = int(argv[i + 1])

    target = None
    for p in sessions():
        if sid and sid in p:
            target = p
            break
    if target is None:
        target = sessions()[0] if sessions() else None
    if not target:
        print("没有可回放的 rollout 文件")
        return 2

    got = load_turn(target, turn)
    if not got:
        print("轮次 %d 不存在" % turn)
        return 2
    d, body, rows = got
    body = json.loads(json.dumps(body))          # 深拷贝，避免污染

    # zcode 把完整对话放在 request.messages（messagesKind:"full"），body 里没有
    # messages —— 且 messages 里混着 role:"system" 的段（CLI Prefix / 注入文案）。
    # Anthropic 只接受 user/assistant，所以发送前把 system 角色滤掉，其余原序保留。
    full_msgs = [m for m in (d["request"].get("messages") or [])
                 if m.get("role") in ("user", "assistant")]

    def _clean(m):
        """zcode 的 assistant 消息里混着 type:"reasoning" 的 content 块，
        Anthropic 端点不认这个 variant，发送前剔掉。"""
        c = m.get("content")
        if isinstance(c, list):
            keep = [x for x in c if isinstance(x, dict) and x.get("type") != "reasoning"]
            if not keep:
                keep = [{"type": "text", "text": "(omitted)"}]
            m = dict(m)
            m["content"] = keep
        elif c is None:
            m = dict(m)
            m["content"] = "(omitted)"
        return m

    if full_msgs:
        body["messages"] = [_clean(m) for m in full_msgs]

    # ---- 裁剪开关 ----
    ops = []
    if "--drop-tools" in argv:
        body.pop("tools", None)
        ops.append("drop-tools")
    for i, a in enumerate(argv):
        if a == "--drop-tool" and i + 1 < len(argv):
            nm = argv[i + 1]
            before = len(body.get("tools") or [])
            body["tools"] = [t for t in (body.get("tools") or []) if t.get("name") != nm]
            ops.append("drop-tool:%s(%d->%d)" % (nm, before, len(body["tools"])))
    for i, a in enumerate(argv):
        if a == "--strip-system" and i + 1 < len(argv):
            n = int(argv[i + 1])
            if 0 <= n < len(body.get("system") or []):
                body["system"].pop(n)
                ops.append("strip-system:%d" % n)
    for i, a in enumerate(argv):
        if a == "--append-system" and i + 1 < len(argv):
            body.setdefault("system", []).append({"type": "text", "text": argv[i + 1]})
            ops.append("append-system")
    for i, a in enumerate(argv):
        if a == "--user" and i + 1 < len(argv):
            txt = argv[i + 1]
            msgs = body.get("messages") or []
            if msgs:
                msgs[-1] = {"role": "user", "content": txt}
            body["messages"] = msgs
            ops.append("user")
    for i, a in enumerate(argv):
        if a == "--effort" and i + 1 < len(argv):
            e = argv[i + 1]
            if e == "none":
                body.pop("output_config", None)
                body.pop("thinking", None)
            else:
                body["output_config"] = {"effort": e}
            ops.append("effort:%s" % e)
    for i, a in enumerate(argv):
        if a == "--model" and i + 1 < len(argv):
            body["model"] = argv[i + 1]
            ops.append("model:%s" % argv[i + 1])
    if "--fresh" in argv:
        # 只保留最后一条 user 消息（模拟"单轮首次请求"，用于隔离多轮历史的影响）
        msgs = body.get("messages") or []
        last = None
        for m in reversed(msgs):
            if m.get("role") == "user":
                last = m
                break
        body["messages"] = [last] if last else []
        ops.append("fresh(只留最后一条 user)")

    sysarr = body.get("system") or []
    # zcode 用 SSE 流式；回放要整体 JSON，默认关掉 stream（--stream 可保留）
    if "--stream" not in argv:
        body["stream"] = False
    print("回放源 : %s  轮 %d" % (os.path.basename(target), turn))
    print("原始   : model=%s  provider=%s  system=%d 段  tools=%d 个  effort=%s" % (
        d.get("model", {}).get("modelId"), d.get("model", {}).get("providerId"),
        len(sysarr), len(body.get('tools') or []),
        (body.get("output_config") or {}).get("effort")))
    print("裁剪   : %s" % ("、".join(ops) if ops else "无（原样）"))
    print("模型   : %s" % body.get("model"))
    print()

    if "--yes" not in argv:
        print("加 --yes 才真发（真实付费上游）")
        return 0

    resp, err = post(body)
    u = load_usage()
    if err:
        print("失败：%s" % err)
        u["calls"] += 1
        u["log"].append({"at": time.strftime("%H:%M:%S"), "replay": turn, "error": err[:120]})
        save_usage(u)
        return 1
    us = resp.get("usage") or {}
    u["calls"] += 1
    u["promptTokens"] += us.get("input_tokens", 0)
    u["completionTokens"] += us.get("output_tokens", 0)
    u["log"].append({"at": time.strftime("%H:%M:%S"), "replay": turn,
                     "ops": ops, "in": us.get("input_tokens"), "out": us.get("output_tokens")})
    save_usage(u)
    print("成功  in=%s out=%s" % (us.get("input_tokens"), us.get("output_tokens")))
    print("=" * 66)
    print(text_of(resp)[:1600])
    print("=" * 66)
    print("累计：调用 %d 次 / 输入 %d tok / 输出 %d tok" % (
        u["calls"], u["promptTokens"], u["completionTokens"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
