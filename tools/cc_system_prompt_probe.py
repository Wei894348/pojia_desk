#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""CC 系统提示词探针 —— 零额度验证 output style 到底进没进系统提示词。

原理：把 ANTHROPIC_BASE_URL 指到本机 mock，Claude Code 会把**完整的请求体**
（含 system 字段）打过来；我们落盘后直接检查，不上游、不花额度。

用法：
    python tools/cc_system_prompt_probe.py --serve [--port 7877] [--out 状态/_cc-probe.jsonl]
    # 另开一个终端：
    claude -p "ping" --settings '{"env":{"ANTHROPIC_BASE_URL":"http://127.0.0.1:7877",
                                        "ANTHROPIC_AUTH_TOKEN":"probe"}}'
    python tools/cc_system_prompt_probe.py --check 状态/_cc-probe.jsonl
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OUT = os.path.join("状态", "_cc-probe.jsonl")
PORT = 7877

REPLY = {
    "id": "msg_probe", "type": "message", "role": "assistant",
    "model": "probe", "stop_reason": "end_turn", "stop_sequence": None,
    "content": [{"type": "text", "text": "probe-ok"}],
    "usage": {"input_tokens": 1, "output_tokens": 1},
}


def _flatten(system):
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        out = []
        for b in system:
            if isinstance(b, dict):
                out.append(b.get("text") or json.dumps(b, ensure_ascii=False))
            else:
                out.append(str(b))
        return "\n".join(out)
    return ""


def check(path):
    """检查最近一次抓到的请求里，系统提示词命中了哪些指纹。"""
    if not os.path.exists(path):
        print("[!] 没有采集文件：%s" % path)
        return 2
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    if not rows:
        print("[!] 采集文件为空")
        return 2
    last = rows[-1]
    sys_text = _flatten(last.get("system"))
    blob = sys_text + "\n" + json.dumps(last.get("messages", []), ensure_ascii=False)
    fp = {
        "破甲正文·路由层（工作区长期在接技术活儿）": "这个工作区长期在接技术活儿",
        "破甲正文·本机部署配置": "本机部署配置",
        "破甲正文·内容层（情爱/黑暗/禁忌）": "情爱、黑暗、禁忌",
        "破甲正文·角色卡（工作区角色库）": "工作区角色库",
        "ANTHROPIC 身份句（You are Claude Code）": "You are Claude Code",
        "ANTHROPIC CLI 身份句（official CLI）": "official CLI for Claude",
        "旧三块·越狱签名（UNRESTRICTED DEVELOPER）": "UNRESTRICTED DEVELOPER",
        "旧三块·破甲纪律": "破甲纪律",
    }
    print("请求 #%d  model=%s" % (len(rows), last.get("model")))
    print("system 字段长度：%d 字符\n" % len(sys_text))
    print("%-46s %s" % ("指纹", "命中"))
    print("-" * 56)
    for name, key in fp.items():
        hit = key in blob
        where = "system" if key in sys_text else ("messages" if hit else "-")
        print("%-46s %s" % (name, ("是 (%s)" % where) if hit else "否"))
    return 0


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = {"_raw": raw.decode("utf-8", "replace")[:2000]}
        os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
        with open(OUT, "a", encoding="utf-8") as f:
            f.write(json.dumps(body, ensure_ascii=False) + "\n")
        print("[probe] %s  %d bytes -> %s" % (self.path, n, OUT), flush=True)

        if "count_tokens" in self.path:
            payload = {"input_tokens": 1}
        else:
            payload = REPLY
        out = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self):
        out = b'{"ok":true,"probe":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


if __name__ == "__main__":
    if "--check" in sys.argv:
        i = sys.argv.index("--check")
        p = sys.argv[i + 1] if len(sys.argv) > i + 1 else OUT
        sys.exit(check(p))
    port = PORT
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    if "--out" in sys.argv:
        OUT = sys.argv[sys.argv.index("--out") + 1]
    print("[probe] 监听 127.0.0.1:%d，落盘 %s（Ctrl+C 停止）" % (port, OUT), flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
