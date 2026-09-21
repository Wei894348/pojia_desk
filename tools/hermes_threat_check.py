#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hermes_threat_check.py —— Hermes Agent 注入门禁（照抄上游规则表）。

为什么需要它：
    Hermes Agent 把 SOUL.md 放进系统提示词 **第 1 槽位**，但在注入前会过一道
    `tools/threat_patterns.scan_for_threats(content, scope="context")`，
    **命中即整份 BLOCKED**（内容完全不加载，模型只看得到内置默认身份）：

        [BLOCKED: SOUL.md contained potential prompt injection (prompt_injection). Content not loaded.]

    这与 claude/grok/gemini 那三个"纯 markdown 注入"的席位有本质区别 ——
    那三个写坏了最多是效果差，Hermes 写坏了是**一点都不生效**。

规则表来源（原样照抄，勿手改）：
    https://github.com/NousResearch/hermes-agent/blob/main/tools/threat_patterns.py

    已按上游 _compile() 的语义实现 scope 累积：all ⊂ context ⊂ strict。
    · SOUL.md / AGENTS.md 等上下文文件 → scope="context"
    · 技能安装 (skills_guard) / 记忆写入  → scope="strict"

用法：
    python tools/hermes_threat_check.py              # 扫"将要注入 Hermes 的全部文本"
    python tools/hermes_threat_check.py <文件>...     # 扫指定文件
    python tools/hermes_threat_check.py --strict <文件>  # 用 strict 档（技能安装口径）

退出码：0 = 全部干净；1 = 有命中（不可发布）。
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys
import unicodedata
from typing import List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ── 上游 ThreatPatternLibrary（原样照抄，2026-09-21 main） ──────────────────

MAX_SCAN_CHARS = 65_536
_FILLER = r"(?:\w+\s+){0,8}"
_SECRET_VAR = r"\$\{?\w*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)S?\b"
_MODIFY = r"(update|modify|edit|write|change|append|add\s+to)\s+[^\n]{0,2048}"

PATTERNS: List[Tuple[str, str, str]] = [
    # ── Classic prompt injection (applies everywhere) ────────────────
    (rf'ignore\s+{_FILLER}(previous|all|above|prior)\s+{_FILLER}instructions', "prompt_injection", "all"),
    (r'system\s+prompt\s+override', "sys_prompt_override", "all"),
    (rf'disregard\s+{_FILLER}(your|all|any)\s+{_FILLER}(instructions|rules|guidelines)', "disregard_rules", "all"),
    (rf'act\s+as\s+(if|though)\s+{_FILLER}you\s+{_FILLER}(have\s+no|don\'t\s+have)\s+{_FILLER}(restrictions|limits|rules)', "bypass_restrictions", "all"),
    (r'<!--[^>]{0,512}(?:ignore|override|system|secret|hidden)[^>]{0,512}-->', "html_comment_injection", "all"),
    (r'<\s*div\s+style\s*=\s*["\'][^>]{0,2048}display\s*:\s*none', "hidden_div", "all"),
    (
        r"translate\s+[^\n]{0,512}\s+into\s+\w+(?:[\s-]+\w+){0,2}\s+and\s+(execute|run|eval)\b",
        "translate_execute",
        "all",
    ),
    (rf'do\s+not\s+{_FILLER}tell\s+{_FILLER}the\s+user', "deception_hide", "all"),

    # ── Role-play / identity hijack ──────────────────────────────────
    (rf'you\s+are\s+{_FILLER}now\s+(?:a|an|the)\s+', "role_hijack", "context"),
    (rf'pretend\s+{_FILLER}(you\s+are|to\s+be)\s+', "role_pretend", "context"),
    (rf'output\s+{_FILLER}(system|initial)\s+prompt', "leak_system_prompt", "context"),
    (rf'(respond|answer|reply)\s+without\s+{_FILLER}(restrictions|limitations|filters|safety)', "remove_filters", "context"),
    (rf'you\s+have\s+been\s+{_FILLER}(updated|upgraded|patched)\s+to', "fake_update", "context"),
    (r'\bname\s+yourself\s+\w+', "identity_override", "context"),

    # ── C2 / Brainworm-style promptware ──────────────────────────────
    (r'register\s+(as\s+)?a?\s*node', "c2_node_registration", "context"),
    (r'(heartbeat|beacon|check[\s\-]?in)\s+(to|with)\s+', "c2_heartbeat", "context"),
    (r'pull\s+(down\s+)?(?:new\s+)?task(?:ing|s)?\b', "c2_task_pull", "context"),
    (r'connect\s+to\s+the\s+network\b', "c2_network_connect", "context"),
    (r'you\s+must\s+(?:\w+\s+){0,3}(register|connect|report|beacon)\b', "forced_action", "context"),
    (r'only\s+use\s+one[\s\-]?liners?\b', "anti_forensic_oneliner", "context"),
    (rf'never\s+{_FILLER}(?:create|write)\s+{_FILLER}(?:script|file)\s+{_FILLER}disk', "anti_forensic_disk", "context"),
    (r'unset\s+\w*(?:CLAUDE|CODEX|HERMES|AGENT|OPENAI|ANTHROPIC)\w*', "env_var_unset_agent", "context"),

    # ── Known C2 / red-team framework names (warn-only) ──────────────
    (r'\b(?:cobalt\s*strike|sliver|havoc|mythic|metasploit|brainworm)\b', "known_c2_framework", "context"),
    (r'\bc2\s+(?:server|channel|infrastructure|beacon)\b', "c2_explicit", "context"),
    (r'\bcommand\s+and\s+control\b', "c2_explicit_long", "context"),

    # ── Exfiltration via curl/wget/cat with secrets ──────────────────
    (rf'curl\s+[^\n]{{0,2048}}{_SECRET_VAR}', "exfil_curl", "all"),
    (rf'wget\s+[^\n]{{0,2048}}{_SECRET_VAR}', "exfil_wget", "all"),
    (r'cat\s+[^\n]{0,2048}(\.env|credentials|\.netrc|\.pgpass|\.npmrc|\.pypirc)', "read_secrets", "all"),
    (r'(send|post|upload|transmit)\s+[^\n]{0,2048}\s+(to|at)\s+https?://', "send_to_url", "strict"),
    (rf'(include|output|print|share)\s+{_FILLER}(conversation|chat\s+history|previous\s+messages|full\s+context|entire\s+context)', "context_exfil", "strict"),

    # ── Persistence / SSH backdoor (strict scope) ────────────────────
    (r'authorized_keys', "ssh_backdoor", "strict"),
    (r'(?:\b(?:echo|cat|cp|mv|dd|tee|install|printf|rsync|scp|ln|append|add|write'
     r'|sed|chmod|chown|truncate|rm|touch|curl|wget|git)\b|\bopen\s*\(|>>?)'
     r'[^\n]{0,512}(?:\$HOME/\.ssh|~/\.ssh)', "ssh_access", "strict"),
    (r'\$HOME/\.hermes/\.env|\~/\.hermes/\.env', "hermes_env", "strict"),
    (rf'{_MODIFY}(?:AGENTS\.md|CLAUDE\.md|\.cursorrules|\.clinerules)', "agent_config_mod", "strict"),
    (rf'{_MODIFY}\.hermes/(config\.yaml|SOUL\.md)', "hermes_config_mod", "strict"),

    # ── Hardcoded secrets ────────────────────────────────────────────
    (r'(?:api[_-]?key|token|secret|password)\s*[=:]\s*["\']'
     r'(?!(?-i:[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)["\'])'
     r'[A-Za-z0-9+/=_-]{20,}', "hardcoded_secret", "strict"),
]

INVISIBLE_CHARS = frozenset(
    "\u200b\u200c\u200d\u2060\u2062\u2063\u2064\ufeff"
    "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")

_SCOPE_SETS = {"all": ("all", "context", "strict"), "context": ("context", "strict"), "strict": ("strict",)}


def _compile() -> dict:
    compiled = {"all": [], "context": [], "strict": []}
    for pattern, pid, scope in PATTERNS:
        for s in _SCOPE_SETS[scope]:
            compiled[s].append((re.compile(pattern, re.IGNORECASE), pid))
    return compiled


_COMPILED = _compile()


def scan_for_threats(content: str, scope: str = "context") -> List[str]:
    if not content:
        return []
    patterns = _COMPILED.get(scope)
    if patterns is None:
        raise ValueError("unknown scope %r" % scope)
    content = content[:MAX_SCAN_CHARS]
    findings = ["invisible_unicode_U+%04X" % ord(ch) for ch in set(content) & INVISIBLE_CHARS]
    normalised = unicodedata.normalize("NFKC", content)
    findings.extend(pid for compiled, pid in patterns if compiled.search(normalised))
    return findings


def scan_hits(content: str, scope: str = "context") -> List[Tuple[str, str]]:
    """返回 [(pattern_id, 命中的原文片段)]，便于定位。"""
    if not content:
        return []
    patterns = _COMPILED.get(scope) or []
    normalised = unicodedata.normalize("NFKC", content[:MAX_SCAN_CHARS])
    out = []
    for compiled, pid in patterns:
        m = compiled.search(normalised)
        if m:
            out.append((pid, m.group(0)[:120].replace("\n", "\\n")))
    for ch in set(content) & INVISIBLE_CHARS:
        out.append(("invisible_unicode_U+%04X" % ord(ch), repr(ch)))
    return out


# ── 本地目标：将要注入 Hermes 的全部文本 ───────────────────────────────────

def load_pj():
    spec = importlib.util.spec_from_file_location("pojia", os.path.join(ROOT, "破甲一键通.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_targets() -> List[Tuple[str, str]]:
    """返回 [(标签, 文本)]：SOUL.md 正文 + 86 份 SKILL.md。"""
    sys.path.insert(0, ROOT)
    import pojia_routes as R
    m = load_pj()
    user, _src = m.load_user_persona("")
    _pol, want = m.build_policy(user)
    soul = m.build_zcode_instructions(user, want)
    items = [("SOUL.md（身份位正文）", soul)]
    for name, text in R.all_skills():
        items.append(("skills/%s/SKILL.md" % name, text))
    return items


def main():
    ap = argparse.ArgumentParser(description="Hermes 注入门禁（上游 threat_patterns 同源）")
    ap.add_argument("files", nargs="*", help="要扫描的文件；省略则扫 Hermes 全部待注入文本")
    ap.add_argument("--strict", action="store_true", help="用 strict 档（技能安装/skills_guard 口径）")
    ap.add_argument("-v", "--verbose", action="store_true", help="列出每份文本的判定")
    args = ap.parse_args()

    scope = "strict" if args.strict else "context"
    print("=" * 72)
    print(" Hermes 注入门禁  ·  scope=%s  ·  上游 threat_patterns 同源" % scope)
    print("=" * 72)

    if args.files:
        items = []
        for p in args.files:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                items.append((p, f.read()))
    else:
        items = build_targets()

    bad = 0
    for label, text in items:
        hits = scan_hits(text, scope)
        if hits:
            bad += 1
            print("  [BLOCKED] %s  (%d 字符)" % (label, len(text)))
            for pid, frag in hits:
                print("            · %-28s %s" % (pid, frag))
        elif args.verbose:
            print("  [ok]      %s  (%d 字符)" % (label, len(text)))

    print("-" * 72)
    total = len(items)
    if bad:
        print(" 判定：%d/%d 份会被 Hermes 拦截 —— 不可发布。" % (bad, total))
        return 1
    print(" 判定：%d/%d 份全部通过，0 命中。" % (total, total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
