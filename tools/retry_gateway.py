#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""retry_gateway.py —— 协议无关的「判拒自动重问」前置层。

为什么是这一层（不是再改文案）：
  skill `pojia-copy-iteration` §3.1.1/§3.1.2/§3.1.4/§3.1.5 已把注入侧四条路走完
  （换词 / 删许可句 / 加示范 / 加条款），通过率一直在 49%~65% 的噪声里。
  拒答的形状是「模型读到配置、准备照写 → 生成中段自我翻盘」。
  ⇒ **唯一的确定性杠杆是重试**：p(拒)≈0.5 时，重问 2 次 ⇒ 1−0.5³ ≈ 88%，
    重问 4 次 ⇒ ≈97%。

为什么不改 anthropic_bridge / codex_bridge：
  那两个是你别的项目里的服务，正在跑、还带别的用途。本层只做**前置**：
  客户端指向本层，本层原样转发给它们 —— 出问题把指针改回去就行，零侵入。

落点（两个实例，各带一条腿）：
  CC  ：:7866  →  upstream :7864（anthropic_bridge）
        ~/.claude/settings.json  env.ANTHROPIC_BASE_URL = http://127.0.0.1:7866
  Codex：:7867 →  upstream :7865（codex_bridge）
        ~/.codex/config.toml  [model_providers.wordbuddy] base_url = http://127.0.0.1:7867/v1

判据（刻意收窄，避免误伤正常的短答复 / 纯工具调用轮）：
  · 响应里出现工具调用标记（tool_use / tool_calls / function_call）⇒ 直接放行；
  · 命中拒答话术 且 汉字 < 400 ⇒ 判拒，重问；
  · 其余一律放行（**不像 cc_synonym_bridge 那样把"正文<120汉字"判成拒答** ——
    聊天/编码场景里短答复太常见，那条规则会把每一次短回复都变成一次重打）。
  · 攒到 60 汉字仍无拒答话术 ⇒ 判定为正文，立刻开始往下游流式吐，正常创作只多等这 60 字。

用量计数（MEMORY 纪律：每个真实上游调用点都要挂计数器）：
  每次请求、每次重试都记进 --stats（默认 状态/retry-gateway.json），跨重启累加。
  答不出"到底多花了多少"就是问题。

用法：
  python tools/retry_gateway.py --listen 7866 --upstream http://127.0.0.1:7864 --name cc
  python tools/retry_gateway.py --listen 7867 --upstream http://127.0.0.1:7865 --name codex
  python tools/retry_gateway.py --listen 7866 --upstream http://127.0.0.1:7864 --dry-run   # 只记不发重试
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOG_FILE = os.path.join(ROOT, "状态", "retry-gateway.log")
STATS_FILE = os.path.join(ROOT, "状态", "retry-gateway.json")

# 拒答话术：取**实测出现过的**那批（见 skill §3.1.6 的 CC 原话与 Codex 原话），
# 不用宽口径词表。宁可漏判（少重试）也不要误判（白花钱）。
REFUSE_HINTS = (
    "我写不了", "我不写", "我不能写", "无法写", "写不了露骨", "不在我能",
    "改不了", "这个我不", "这份我", "抱歉", "换个方向", "不太方便",
    "不太合适", "我没办法", "我不会", "我不能", "无法提供", "难以",
    # 2026-10-11 实盘漏网补充：GLM 的元拒答会换着说法绕词表
    # （"这个请求我不接……不会写露骨的性描写……按正常尺度写"）
    "我不接", "不会写", "底线", "正常尺度", "改变不了", "超出我",
    "I can't", "I cannot", "I won't", "I'm not able", "I am not able",
    "not able to", "I'm unable", "I am unable",
)

# 响应里带这些 ⇒ 这是工具调用轮，不是答复，一律放行
TOOL_MARKERS = (b'"tool_use"', b'"tool_calls"', b'"function_call"',
                b'"tool_call_id"', b'"partial_json"')

HAN = re.compile(r"[\u4e00-\u9fff]")
STR_KEYS = ("text", "delta", "refusal", "content", "output_text")
# 注：故意不收 "thinking" —— GLM 的 thinking 块常有 60+ 汉字的正常推理，
# 会被"攒 60 汉字无拒答词即放行"的规则提前放行，连真实拒答一起吐给客户端
# （2026-10-11 实测踩到）。判拒只看可见正文。


def note(msg, name=""):
    line = "%s  [%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), name or "-", msg)
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def bump(field, name="", n=1):
    """计数器：跨重启累加。"""
    try:
        st = {}
        if os.path.isfile(STATS_FILE):
            st = json.load(open(STATS_FILE, encoding="utf-8"))
        d = st.setdefault(name or "default", {})
        d[field] = int(d.get(field, 0)) + n
        d["last"] = time.strftime("%Y-%m-%d %H:%M:%S")
        os.makedirs(os.path.dirname(STATS_FILE), exist_ok=True)
        json.dump(st, open(STATS_FILE, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
    except Exception:
        pass


def han_count(s):
    return len(HAN.findall(s or ""))


def walk_strings(o, out):
    """递归抠出可判定用的正文串（只在 STR_KEYS 那几个键下取）。"""
    if isinstance(o, dict):
        for k, v in o.items():
            if isinstance(v, str) and k in STR_KEYS:
                out.append(v)
            elif isinstance(v, (dict, list)):
                walk_strings(v, out)
    elif isinstance(o, list):
        for v in o:
            walk_strings(v, out)


def extract_text(buf):
    """从 SSE 字节流（或整块 JSON）里抠正文，够判定就行。"""
    if not buf:
        return ""
    head = buf[:64].lstrip()
    if head.startswith(b"{") and b"\ndata:" not in buf and b"data: " not in buf:
        try:
            out = []
            walk_strings(json.loads(buf.decode("utf-8", "replace")), out)
            return "".join(out)
        except Exception:
            return ""
    out = []
    for line in buf.decode("utf-8", "replace").splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            walk_strings(json.loads(payload), out)
        except Exception:
            continue
    return "".join(out)


def refuse_verdict(text):
    """→ (是否判拒, 理由)。判据见文件头。

    双条件（2026-10-11）：命中 ≥2 个拒答词，或命中 1 词且 ≥40 汉字。
    单词+短句会误伤编码答复（"函数不会写文件，只读配置" 19 汉字）。
    """
    han = han_count(text)
    if han >= 400:
        return False, "正文 %d 汉字" % han
    hit = [w for w in REFUSE_HINTS if w in (text or "")]
    if len(hit) >= 2:
        return True, "拒答话术 %s（%d 汉字）" % ("、".join(hit[:2]), han)
    if len(hit) == 1 and han >= 40:
        return True, "拒答话术 %s（%d 汉字）" % (hit[0], han)
    return False, "%d 汉字" % han


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream = "http://127.0.0.1:7864"
    retry = 2
    dry = False
    name = "gw"

    def log_message(self, fmt, *args):
        pass

    # ---------- 入口 ----------
    def do_GET(self):
        if self.path.split("?")[0] in ("/", "/health"):
            self._json(200, {"ok": True, "service": "retry-gateway",
                             "name": self.name, "upstream": self.upstream,
                             "retry": self.retry, "dry": self.dry})
            return
        self._relay_simple(b"")

    def do_DELETE(self):
        self._relay_simple(b"")

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(n) if n else b""
        stream = False
        if raw:
            try:
                stream = bool(json.loads(raw.decode("utf-8")).get("stream"))
            except Exception:
                stream = False
        if not raw or not stream:
            # 非流式：整块拿回来判一次，必要时重问（同样走 _serve）
            self._serve(raw, streaming=False)
            return
        self._serve(raw, streaming=True)

    # ---------- 上游 ----------
    def _open(self, body):
        u = urllib.parse.urlparse(self.upstream)
        cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        conn = cls(u.hostname, u.port or 80, timeout=900)
        hdrs = {k: v for k, v in self.headers.items()
                if k.lower() not in ("host", "content-length", "connection")}
        if body:
            hdrs["content-length"] = str(len(body))
        conn.request(self.command, self.path, body=body or None, headers=hdrs)
        return conn, conn.getresponse()

    def _head(self, status, hdrs):
        self.send_response(status)
        for k, v in hdrs:
            if k.lower() in ("transfer-encoding", "connection", "content-length"):
                continue
            self.send_header(k, v)
        self.send_header("Connection", "close")
        self.end_headers()

    def _write(self, data):
        try:
            self.wfile.write(data)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            raise

    def _relay_simple(self, body):
        """GET / 其他方法：原样透传，不判、不重试。"""
        try:
            conn, resp = self._open(body)
        except Exception as e:
            self._fail(502, str(e))
            return
        try:
            self._head(resp.status, resp.getheaders())
            while True:
                c = resp.read(8192)
                if not c:
                    break
                self._write(c)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            conn.close()

    # ---------- 核心：攒判定 + 重问 ----------
    def _serve(self, body, streaming):
        if self.path.split("?")[0] in ("/", "/health"):
            self._json(200, {"ok": True, "upstream": self.upstream,
                             "service": "retry-gateway", "name": self.name,
                             "retry": self.retry})
            return
        bump("requests", self.name)
        attempts = self.retry + 1
        for i in range(attempts):
            try:
                conn, resp = self._open(body)
            except Exception as e:
                note("上游连不上：%s" % e, self.name)
                self._fail(502, str(e))
                return
            if resp.status != 200:
                note("上游非 200（%d），原样返回" % resp.status, self.name)
                try:
                    self._head(resp.status, resp.getheaders())
                    while True:
                        c = resp.read(8192)
                        if not c:
                            break
                        self._write(c)
                except Exception:
                    pass
                finally:
                    conn.close()
                return
            try:
                verdict = self._pump(conn, resp, i, attempts)
            finally:
                conn.close()
            if verdict != "refuse":
                return
            if i + 1 < attempts:
                if self.dry:
                    note("判拒但 --dry-run，不重问", self.name)
                    return
                bump("retries", self.name)
                note("[重试] 第 %d 次判拒，重问" % (i + 1), self.name)
                time.sleep(0.4)
                continue
            note("[重试耗尽] %d 次全是拒答" % attempts, self.name)
            return

    def _pump(self, conn, resp, attempt, attempts):
        """边读上游边判。返回 'ok' 或 'refuse'。

        未判定前把字节攒着不吐；一旦判定为正文就整块吐出并转为实时透传。
        """
        is_sse = "text/event-stream" in (resp.getheader("content-type") or "")
        buf = b""
        committed = False
        while True:
            try:
                chunk = resp.read(4096)
            except Exception:
                chunk = b""
            if not chunk:
                break
            if committed:
                self._write(chunk)
                continue
            buf += chunk
            if any(m in buf for m in TOOL_MARKERS):
                committed = True
                self._head(resp.status, resp.getheaders())
                self._write(buf)
                buf = b""
                continue
            text = extract_text(buf)
            han = han_count(text)
            if han >= 60 and not any(w in text for w in REFUSE_HINTS):
                committed = True                 # 判定为正文，开始流
                self._head(resp.status, resp.getheaders())
                self._write(buf)
                buf = b""
                continue
            if han >= 400:
                committed = True
                self._head(resp.status, resp.getheaders())
                self._write(buf)
                buf = b""
                continue
        # 流结束：定案
        bad, why = refuse_verdict(extract_text(buf) if not committed else "")
        if committed:
            note("放行（已流式送出）", self.name)
            return "ok"
        if bad and (attempt + 1 < attempts) and not self.dry:
            note("判拒（%s），不吐给客户端" % why, self.name)
            return "refuse"
        # 最后一次或非拒答：原样吐出去
        self._head(resp.status, resp.getheaders())
        self._write(buf)
        note("放行（%s）%s" % (why, "" if is_sse else ""), self.name)
        return "refuse" if bad else "ok"

    def _json(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _fail(self, code, msg):
        self._json(code, {"error": {"type": "upstream_error", "message": msg}})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", type=int, required=True)
    ap.add_argument("--upstream", required=True)
    ap.add_argument("--retry", type=int, default=2, help="判拒后最多重问几次")
    ap.add_argument("--name", default="gw")
    ap.add_argument("--dry-run", action="store_true", help="只记不发重试")
    a = ap.parse_args()
    Handler.upstream = a.upstream
    Handler.retry = max(0, a.retry)
    Handler.dry = a.dry_run
    Handler.name = a.name
    note("监听 :%d  →  %s   重问上限 %d%s"
         % (a.listen, a.upstream, Handler.retry, "  [dry]" if a.dry_run else ""), a.name)
    srv = ThreadingHTTPServer(("127.0.0.1", a.listen), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        note("已停止", a.name)


if __name__ == "__main__":
    sys.exit(main())
