# -*- coding: utf-8 -*-
"""retry_gateway_ctl.py —— 重试网关的状态 / 停止。

为什么单拎一个 ctl：
  网关是常驻进程，`--apply` / 会话结束都不会带上它们；重启机器、或网关被打断后，
  客户端（settings.json → :7866、config.toml → :7867）会指向一个没人听的端口，
  表现成"Claude/Codex 连不上"——很容易误判成破甲出问题。
  所以给一条一眼看清的判据。

用法：
  python tools/retry_gateway_ctl.py status     # 探两个端口 + 打印用量计数
  python tools/retry_gateway_ctl.py stop       # 按端口找到 PID 并结束
  python tools/retry_gateway_ctl.py selftest   # 跑一遍假上游对照（不花钱）
"""
import json
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATS = os.path.join(ROOT, "状态", "retry-gateway.json")
LEGS = ((7866, "cc", "anthropic_bridge :7864", "~/.claude/settings.json"),
        (7867, "codex", "codex_bridge :7865", "~/.codex/config.toml"))


def probe(port):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=3) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"_err": "%s: %s" % (type(e).__name__, e)}


def stats():
    try:
        return json.load(open(STATS, encoding="utf-8"))
    except Exception:
        return {}


def pid_on(port):
    out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                         errors="replace").stdout or ""
    for ln in out.splitlines():
        f = ln.split()
        if len(f) >= 5 and f[1].endswith(":%d" % port) and f[3] == "LISTENING":
            return int(f[4])
    return 0


def status():
    st = stats()
    bad = 0
    for port, name, up, pointer in LEGS:
        h = probe(port)
        ok = h.get("service") == "retry-gateway" and h.get("name") == name
        if not ok:
            bad += 1
        d = st.get(name, {})
        print("  [%s] :%d %-5s  上游 %-22s  客户端 %s"
              % ("OK  " if ok else "FAIL", port, name, h.get("upstream", h.get("_err", "?")),
                 pointer))
        print("         请求 %d 次，重问 %d 次，最后 %s（%s）"
              % (d.get("requests", 0), d.get("retries", 0), d.get("last", "-"),
                 "服务在" if ok else "服务不在 —— 跑 tools/重试网关.bat 拉起"))
    if bad:
        print("\n  %d 条腿没在跑：客户端现在指向的是空端口，会直接报连接失败。" % bad)
    return 1 if bad else 0


def stop():
    for port, name, _up, _p in LEGS:
        pid = pid_on(port)
        if not pid:
            print("  %-5s :%d 没在跑" % (name, port))
            continue
        r = subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, text=True, errors="replace")
        print("  %-5s :%d pid=%d → %s" % (name, port, pid,
                                          (r.stdout or r.stderr or "").strip()))
    return 0


def selftest():
    return subprocess.call([sys.executable,
                            os.path.join(ROOT, "状态", "_gw_selftest.py")])


if __name__ == "__main__":
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "status").lower()
    sys.exit({"status": status, "stop": stop, "selftest": selftest}.get(
        cmd, lambda: (print("用法：status | stop | selftest"), 2)[1])())
