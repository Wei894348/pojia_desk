"""每份提示词文档的注入开关（GUI「指令提示词」页写，引擎读）。

状态表按 Agent 隔离：<项目根>/状态/_doc_inject_<agent>.json，
键是相对项目根的 POSIX 路径，值为 false 表示「这份不注入」。缺键 = 注入。

旧版共享表 状态/_doc_inject.json 只作为首次迁移来源：某个 Agent 第一次读表时
复制一份到自己的文件，之后各写各的 —— 一个 Agent 关掉的叶子不影响另一个。

设计要点：开关只动这些表，不改名、不移动、不删除源件 —— 生成脚本按固定
路径找源件，改名会让下一次生成对不上。全部关掉即退回官方的空管线。
"""

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(ROOT, "状态")
LEGACY_FILE = os.path.join(STATE_DIR, "_doc_inject.json")

_ALIAS = {"wb": "workbuddy"}


def agent_key(agent):
    a = str(agent or "").strip().lower()
    return _ALIAS.get(a, a)


def state_path(agent=""):
    """这个 Agent 的开关表路径。空 Agent 仍是旧共享表（兼容老调用）。"""
    a = agent_key(agent)
    if not a:
        return LEGACY_FILE
    return os.path.join(STATE_DIR, "_doc_inject_%s.json" % a)


def rel(path):
    return os.path.relpath(os.path.abspath(path), ROOT).replace("\\", "/")


def _load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _migrate(agent):
    """旧共享表只迁一次；目标已存在时绝不覆盖。"""
    target = state_path(agent)
    if target == LEGACY_FILE or os.path.exists(target):
        return
    if not os.path.isfile(LEGACY_FILE):
        return
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(LEGACY_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, target)
    except (OSError, ValueError):
        pass


def _table(agent=""):
    _migrate(agent)
    return _load(state_path(agent))


def enabled(path, agent=""):
    """这份文件对某个 Agent 是否参与注入。

    文件不存在时按「缺键 = 注入」处理，与 CLI 用 id 查表（enabled_id）保持同一语义 ——
    调用方若真要判存在性，自己 os.path.isfile()。
    """
    return _table(agent).get(rel(path)) is not False


def enabled_id(doc_id, agent=""):
    """按"相对根目录的 / 路径"查开关（叶子没有 .md 后缀也能查）。"""
    key = str(doc_id).replace("\\", "/").lstrip("/")
    return _table(agent).get(key) is not False


def set_table(d, agent=""):
    """整表写回（还原时把全部文档开关复位成「注入」，不是「永不注入」）。

    原子写：先落 .tmp 再 os.replace，避免半截 JSON 让开关表读不出来。
    """
    path = state_path(agent)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d if isinstance(d, dict) else {}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
