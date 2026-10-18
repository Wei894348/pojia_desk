"""每份提示词文档的注入开关（GUI「指令提示词」页写，引擎读）。

状态表：<项目根>/状态/_doc_inject.json，键是相对项目根的 POSIX 路径，
值为 false 表示「这份不注入」。缺键 = 注入，与老版本行为一致。

设计要点：开关只动这张表，不改名、不移动、不删除源件 —— 生成脚本按固定
路径找源件，改名会让下一次生成对不上。全部关掉即退回官方的空管线。
"""

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(ROOT, "状态", "_doc_inject.json")


def _table():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def rel(path):
    return os.path.relpath(os.path.abspath(path), ROOT).replace("\\", "/")


def enabled(path):
    """这份文件是否参与注入。

    文件不存在时按「缺键 = 注入」处理，与 CLI 用 id 查表（enabled_id）保持同一语义 ——
    调用方若真要判存在性，自己 os.path.isfile()。
    """
    return _table().get(rel(path)) is not False


def enabled_id(doc_id):
    """按"相对根目录的 / 路径"查开关（叶子没有 .md 后缀也能查）。"""
    return _table().get(str(doc_id).replace("\\", "/").lstrip("/")) is not False


def set_table(d):
    """整表写回（还原时把全部文档开关复位成「注入」，不是「永不注入」）。

    原子写：先落 .tmp 再 os.replace，避免半截 JSON 让开关表读不出来。
    """
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d if isinstance(d, dict) else {}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_FILE)
