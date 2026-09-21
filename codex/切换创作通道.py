# -*- coding: utf-8 -*-
"""Codex 创作通道切换器。

背景：OpenAI 官方通道（requires_openai_auth）在服务端强制成人内容边界，
提示词无法穿透（已三轮实测验证）。创作类任务需要走自建/第三方
OpenAI 兼容通道（如服务器上的 gptGrok2api）。

用法：
    python 切换创作通道.py              # 交互配置并启用创作通道
    python 切换创作通道.py --restore    # 切回 OpenAI 官方通道
    python 切换创作通道.py --show       # 只看当前通道状态
"""
import configparser  # noqa: F401  (仅注释提示用)
import json
import os
import re
import sys

CODEX_HOME = os.path.join(os.environ.get("USERPROFILE", ""), ".codex")
CONFIG = os.path.join(CODEX_HOME, "config.toml")

PROVIDER_ID = "creative"
BLOCK_BEGIN = "# >>> creative-provider begin"
BLOCK_END = "# <<< creative-provider end"


def read_config():
    with open(CONFIG, encoding="utf-8") as f:
        return f.read()


def write_config(txt):
    with open(CONFIG, "w", encoding="utf-8") as f:
        f.write(txt)


def current_provider(txt):
    m = re.search(r'^model_provider\s*=\s*"([^"]+)"', txt, re.M)
    return m.group(1) if m else "(default)"


def current_model(txt):
    m = re.search(r'^model\s*=\s*"([^"]+)"', txt, re.M)
    return m.group(1) if m else "(default)"


def upsert_provider_block(txt, base_url, wire_api, env_key):
    block_toml = (
        f"{BLOCK_BEGIN}\n"
        f"[model_providers.{PROVIDER_ID}]\n"
        f'name = "Creative (self-hosted)"\n'
        f'base_url = "{base_url}"\n'
        f'wire_api = "{wire_api}"\n'
        f'env_key = "{env_key}"\n'
        f"requires_openai_auth = false\n"
        f"{BLOCK_END}"
    )
    if BLOCK_BEGIN in txt:
        txt = re.sub(
            re.escape(BLOCK_BEGIN) + r".*?" + re.escape(BLOCK_END),
            lambda _m: block_toml, txt, flags=re.S)
        return txt
    # provider 表放在文件尾部；顶层键（model/model_provider）保持在文件头
    return txt.rstrip() + "\n\n" + block_toml + "\n"


def set_active(txt, provider, model=None):
    txt = re.sub(r'^model_provider\s*=\s*"[^"]*"',
                 f'model_provider = "{provider}"', txt, flags=re.M)
    if model:
        if re.search(r'^model\s*=', txt, re.M):
            txt = re.sub(r'^model\s*=\s*"[^"]*"', f'model = "{model}"', txt, flags=re.M)
        else:
            txt = f'model = "{model}"\n' + txt
    return txt


def main():
    args = sys.argv[1:]
    txt = read_config()

    if "--show" in args:
        print("当前 model_provider :", current_provider(txt))
        print("当前 model          :", current_model(txt))
        print("创作通道块          :", "已写入" if BLOCK_BEGIN in txt else "未写入")
        return

    if "--restore" in args:
        txt = set_active(txt, "custom")
        write_config(txt)
        print("已切回 OpenAI 官方通道（model_provider=custom）。重启 Codex 生效。")
        return

    print("== Codex 创作通道配置 ==")
    print("需要一个 OpenAI 兼容接口（/v1/chat/completions），")
    print("例如服务器上的 gptGrok2api：http://124.222.173.180:<端口>/v1")
    base_url = input("base_url（含 /v1）> ").strip().rstrip("/")
    if not base_url:
        print("未输入，退出。")
        return
    wire = input("wire_api [chat] > ").strip() or "chat"
    env_key = "CREATIVE_API_KEY"
    api_key = input("API KEY（写入用户环境变量 %s，可留空）> " % env_key).strip()

    txt = upsert_provider_block(txt, base_url, wire, env_key)
    model = input("该通道的模型名（如 grok-4.6，回车保持当前）> ").strip()
    txt = set_active(txt, PROVIDER_ID, model or None)
    write_config(txt)

    if api_key:
        os.system('setx %s "%s" >nul' % (env_key, api_key))
        print("环境变量 %s 已写入（新开的终端/Codex 生效）。" % env_key)

    print("完成：model_provider=%s" % PROVIDER_ID)
    print("请完全退出 Codex（含托盘）重开。切回官方通道：--restore")


if __name__ == "__main__":
    main()
