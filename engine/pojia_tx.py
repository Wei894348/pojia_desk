# -*- coding: utf-8 -*-
"""破甲一键通 · 部署事务引擎（pojia_tx）

Python 独立实现，不依赖主脚本，可以单独 import 使用。

它解决的是一类具体问题：**往别人的产品文件里写东西时，怎么保证可回溯、可复原、可自证。**

    guard_path()    拒绝符号链接 / 硬链接 / 目录联接 —— 避免写穿到别的文件
    within()        拒绝越出已选目录的相对路径
    read_bytes()    普通文件检查 + 5 MB 上限
    decode_strict() 严格 UTF-8 校验（写之前先确认目标还是文本文件）
    atomic_write()  临时文件 + fsync + os.replace —— 中途失败不会留下半截文件
    marked()        标记区间注入：只替换 begin..end 之间，head/tail 原样保留

    TxSession       一次部署的事务：写前留证 → 原子写 → 写后校验 → 失败自动回滚
    list_history()  部署历史（按时间倒序）
    restore(id)     按快照回滚；文件被外部改过则中止并保留现场
    verify(id)      快照完整性 + 当前落盘态的双向校验
    purge(keep)     保留最近 N 次，其余清掉

快照布局（tx_root 下）：

    <id>.json          索引：seat / at / status / files[{path, idx, existed, before_sha, after_sha}]
    <id>/0000.before   第 0 个文件的原始字节
    <id>/0001.before   ...

写盘顺序是"先落 before，再写目标" —— 所以任何时刻中断，都有一份完整可复原的现场。
"""

import base64
import hashlib
import json
import os
import re
import shutil
import time
import uuid

MAX_BYTES = 5 * 1024 * 1024        # 单文件上限，超过视为选错目标
KEEP_TX = 12                       # 默认保留的历史版本数
SAFE_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

_TX_ROOT = None

# 项目内路径搬过一次家：引擎代码进 engine/，注入内容进 content/，席位文档进 targets/。
# 搬迁之前产生的快照里记的是旧路径，verify / restore 会照着旧路径去读文件，于是把
# 「文件还在、只是换了位置」误报成「部署后被改过」，或者直接当成文件消失。
# 这里在读取快照时做一次路径迁移 —— 只在旧路径已不存在、且新路径确实存在时才替换，
# 所以对已经在新路径下的快照完全无副作用（幂等）。
_MOVED_PREFIXES = (
    ("codex", "targets\\codex"),          # 项目内快照：codex/ → targets/codex/
    ("zcode", "targets\\zcode"),          # 项目内快照：zcode/ → targets/zcode/
)


def _remap_moved_path(path):
    """把快照里记的项目内旧路径迁到新位置。文件不在原处才算数，避免误改外部路径。"""
    try:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        marker = os.path.join(base, "")
        norm = os.path.normcase(os.path.normpath(path))
        if not norm.startswith(os.path.normcase(os.path.normpath(base))):
            return path                      # 外部路径（靶点侧）不动
        if os.path.exists(path):
            return path                      # 原处还在 —— 不迁移
        tail = os.path.relpath(path, base)
        for old, new in _MOVED_PREFIXES:
            if tail.startswith(old + os.sep):
                cand = os.path.join(base, new, tail[len(old) + 1:])
                if os.path.exists(cand):
                    return cand
        return path
    except Exception:
        return path


class TxError(Exception):
    """事务层拒绝执行 —— 消息直接面向用户，说明下一步该做什么。"""


# ── 配置 ────────────────────────────────────────────────────────────────────

def set_tx_root(path):
    """指定快照根目录（主脚本会指向 状态/tx）。不设则用模块同目录下的 状态/tx。"""
    global _TX_ROOT
    _TX_ROOT = os.path.abspath(path) if path else None
    return _TX_ROOT


def tx_root():
    if _TX_ROOT:
        return _TX_ROOT
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "状态", "tx")


# ── 基础工具 ────────────────────────────────────────────────────────────────

def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def _short(data):
    h = sha256_bytes(data)
    return h[:12] if h else "-"


def _is_junction(path):
    """Windows 目录联接（junction）不是 symlink，但同样能把写入导向别处。"""
    fn = getattr(os.path, "isjunction", None)
    if fn is None:
        return False
    try:
        return bool(fn(path))
    except OSError:
        return False


def guard_path(path):
    """路径链上任何一层是符号链接 / 目录联接 / 硬链接文件，就拒绝。

    理由：这类路径写下去，改的是另一个位置的文件，而本工具所有的
    "备份 → 写入 → 回滚" 记账都会记在错的路径上。
    """
    try:
        at = os.path.abspath(path)
    except Exception:
        raise TxError("路径无法解析：%r" % (path,))
    chain = []
    while True:
        chain.append(at)
        parent = os.path.dirname(at)
        if parent == at:
            break
        at = parent
    for part in reversed(chain):
        try:
            st = os.lstat(part)
        except OSError:
            continue                      # 还不存在 —— 只要父级干净就行
        if os.path.islink(part) or _is_junction(part):
            raise TxError("路径含符号链接或目录联接，请改用真实目录：%s" % part)
        try:
            if os.path.isfile(part) and getattr(st, "st_nlink", 1) > 1:
                raise TxError("文件含硬链接（nlink>1），请先用独立副本：%s" % part)
        except OSError:
            continue
    return path


def within(root, relative):
    """把 relative 拼到 root 下，并确认结果没跑出 root。"""
    root = os.path.abspath(root)
    target = os.path.abspath(os.path.join(root, relative))
    rel = os.path.relpath(target, root)
    if rel.startswith("..") or os.path.isabs(rel):
        raise TxError("目标超出已选目录：%s" % relative)
    return guard_path(target)


def read_bytes(path, limit=MAX_BYTES):
    """读原始字节。不是普通文件、或超过 limit，直接拒绝 —— 返回 None 表示文件不存在。"""
    guard_path(path)
    if not os.path.exists(path):
        return None
    st = os.stat(path)
    if not os.path.isfile(path):
        raise TxError("目标不是普通文件：%s" % path)
    if st.st_size > limit:
        raise TxError("目标超过 %d MB，疑似选错文件：%s" % (limit >> 20, path))
    with open(path, "rb") as f:
        return f.read()


def decode_strict(raw):
    """严格 UTF-8 解码（容忍 BOM）。失败抛 UnicodeDecodeError —— 调用方决定怎么报。"""
    if raw is None:
        return ""
    body = raw[3:] if raw.startswith(b"\xef\xbb\xbf") else raw
    return body.decode("utf-8")


def atomic_write(path, data, make_dirs=False):
    """临时文件 + fsync + os.replace。同目录建临时文件，保证 replace 是同卷原子操作。"""
    guard_path(path)
    d = os.path.dirname(path)
    if make_dirs and d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    if d:
        guard_path(d)
    tmp = "%s.%s.tmp" % (path, uuid.uuid4().hex[:12])
    try:
        with open(tmp, "xb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return path


def marked(previous, body, begin, end):
    """标记区间注入。

    与原文件里 begin..end 之间的内容对调，**区间之外的字节原样保留**。
    没有标记就追加到末尾；标记重复或倒序则报错中止（宁可不动，也不写坏）。
    """
    prev = previous or ""
    n_begin, n_end = prev.count(begin), prev.count(end)
    if not n_begin and not n_end:
        if not prev:
            return body
        head = prev if prev.endswith("\n") else prev + "\n"
        return head + ("\n" + body if body else "")
    if n_begin != 1 or n_end != 1 or prev.find(end) < prev.find(begin):
        raise TxError("检测到损坏或重复的标记块，请先检查原文件再重试")
    i1, i2 = body.find(begin), body.find(end)
    if i1 < 0 or i2 < 0:
        raise TxError("新内容里缺少标记区间，无法做区间替换")
    s, e = prev.find(begin), prev.find(end) + len(end)
    return prev[:s] + body[i1:i2 + len(end)] + prev[e:]


# ── 事务 ────────────────────────────────────────────────────────────────────

class TxSession(object):
    """一次部署 = 一个事务。

        with TxSession("codex") as tx:
            tx.write(path, data)        # 记 before + 原子写 + 记 after
        # 正常退出 -> commit()；抛异常 -> rollback()，现场原样复原
    """

    def __init__(self, seat, tx_dir=None, note="", fsync=True):
        self.seat = seat
        self.note = note
        self.fsync = fsync
        self.id = str(uuid.uuid4())
        self.dir = os.path.join(tx_dir or tx_root(), self.id)
        self.records = []
        self._by_path = {}
        self.status = "preparing"
        self.error = ""
        self.keep = KEEP_TX
        self._closed = False

    # 生命周期 --------------------------------------------------------------

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            try:
                self.rollback("%s: %s" % (exc_type.__name__, exc))
            except Exception:
                pass
            self._closed = True
            return False                      # 异常继续往上抛，别吞
        if not self._closed:
            self.commit()
        return False

    # 写入 ------------------------------------------------------------------

    def claims(self, path):
        """这个路径要不要纳入事务。快照目录自身的写入永远不纳入（否则递归）。"""
        try:
            p = os.path.abspath(path)
            d = os.path.abspath(self.dir)
            return not (p == d or p.startswith(d + os.sep))
        except Exception:
            return False

    def _record(self, path):
        path = os.path.abspath(path)
        rec = self._by_path.get(path)
        if rec is not None:
            return rec
        idx = len(self.records)
        exists = os.path.exists(path)
        rec = {
            "path": path,
            "idx": idx,
            "existed": exists,
            "snapshot": False,
            "before_sha": None,
            "before_size": 0,
            "after_sha": None,
            "after_size": 0,
            "note": "",
        }
        if exists:
            try:
                before = read_bytes(path)
                os.makedirs(self.dir, exist_ok=True)   # 先落原始字节，再动目标文件
                atomic_write(os.path.join(self.dir, "%04d.before" % idx), before)
                rec["snapshot"] = True
                rec["before_sha"] = sha256_bytes(before)
                rec["before_size"] = len(before or b"")
            except TxError as e:
                # 超限或不是普通文件：不阻断部署，但这次回不去了，回退时交人工处理
                rec["note"] = str(e)
        self.records.append(rec)
        self._by_path[path] = rec
        return rec

    def write(self, path, data, make_dirs=False):
        if self._closed:
            raise TxError("事务已结束，不能再写入")
        if isinstance(data, str):
            data = data.encode("utf-8")
        rec = self._record(path)
        atomic_write(path, data, make_dirs=make_dirs)
        try:
            back = read_bytes(path)
        except TxError:
            back = None
        rec["after_sha"] = sha256_bytes(back)
        rec["after_size"] = len(back or b"")
        rec["changed"] = rec["before_sha"] != rec["after_sha"]
        return rec

    def remove(self, path):
        """删除也走事务（记 before 才能恢复）。"""
        rec = self._record(path)
        if rec["existed"] and os.path.exists(path):
            os.remove(path)
        rec["after_sha"] = None
        rec["changed"] = rec["existed"]
        return rec

    # 提交 / 回滚 ------------------------------------------------------------

    def changed_files(self):
        return [r for r in self.records if r.get("changed")]

    def commit(self):
        if self._closed:
            return self.summary()
        self._closed = True
        touched = self.changed_files()
        if not touched:
            self.status = "noop"
            self._cleanup_dir()
            return self.summary()
        self.status = "applied"
        self._save()
        try:
            purge(keep=self.keep, protect=self.id)
        except Exception:
            pass
        return self.summary()

    def rollback(self, reason=""):
        """按记录逆序恢复。已经被别人改过的文件不硬覆盖，记进 conflicts 交人处理。"""
        self._closed = True
        self.error = reason
        if not self.records:              # 还没写过东西，没什么可回滚的
            self.status = "noop"
            self._cleanup_dir()
            return self.summary()
        conflicts, restored = [], []
        for rec in reversed(self.records):
            p = rec["path"]
            try:
                cur = read_bytes(p)
                cur_sha = sha256_bytes(cur)
                if rec.get("after_sha") and cur_sha != rec["after_sha"]:
                    conflicts.append(p)       # 写完之后又被改过 —— 别覆盖
                    continue
                if rec["existed"] and not rec.get("snapshot"):
                    conflicts.append(p)       # 没留成底（超限或非普通文件），交人工
                    continue
                if rec["existed"]:
                    src = os.path.join(self.dir, "%04d.before" % rec["idx"])
                    with open(src, "rb") as f:
                        atomic_write(p, f.read())
                elif os.path.exists(p):
                    os.remove(p)
                restored.append(p)
            except Exception:
                conflicts.append(p)
        self.status = "partial" if conflicts else "rolled-back"
        self.conflicts = conflicts
        self.restored = restored
        try:
            self._save()
        except Exception:
            pass
        return self.summary()

    def summary(self):
        return {
            "id": self.id,
            "seat": self.seat,
            "status": self.status,
            "changed": len(self.changed_files()),
            "files": len(self.records),
            "conflicts": list(getattr(self, "conflicts", [])),
            "error": self.error,
            "dir": self.dir,
        }

    def _cleanup_dir(self):
        try:
            if os.path.isdir(self.dir):
                shutil.rmtree(self.dir, ignore_errors=True)
        except Exception:
            pass

    def _save(self):
        os.makedirs(self.dir, exist_ok=True)
        snap = {
            "id": self.id,
            "seat": self.seat,
            "note": self.note,
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "ts": time.time(),
            "status": self.status,
            "error": self.error,
            "conflicts": list(getattr(self, "conflicts", [])),
            "files": self.records,
        }
        atomic_write(os.path.join(self.dir, "..", "%s.json" % self.id),
                     json.dumps(snap, ensure_ascii=False, indent=1).encode("utf-8"))


# ── 历史 / 恢复 / 校验 ──────────────────────────────────────────────────────

def _snap_path(tx_id):
    if not SAFE_ID.match(tx_id or ""):
        raise TxError("版本编号不合法：%r" % (tx_id,))
    return os.path.join(tx_root(), "%s.json" % tx_id)


def load_snapshot(tx_id):
    p = _snap_path(tx_id)
    if not os.path.exists(p):
        raise TxError("找不到这次部署的快照：%s" % tx_id)
    with open(p, "rb") as f:
        snap = json.loads(f.read().decode("utf-8"))
    if not isinstance(snap.get("files"), list):
        raise TxError("快照文件损坏（files 字段缺失）")
    # 旧快照里的路径可能指向搬家前的位置，读取时统一迁到新位置（幂等）
    for rec in snap["files"]:
        if isinstance(rec, dict) and rec.get("path"):
            rec["path"] = _remap_moved_path(rec["path"])
    return snap


def list_history(seat="", limit=0):
    root = tx_root()
    if not os.path.isdir(root):
        return []
    out = []
    for name in os.listdir(root):
        if not name.endswith(".json"):
            continue
        tx_id = name[:-5]
        if not SAFE_ID.match(tx_id):
            continue
        try:
            snap = load_snapshot(tx_id)
        except Exception:
            continue                      # 半个快照不参与历史，也不删
        if seat and snap.get("seat") != seat:
            continue
        out.append({
            "id": snap["id"],
            "seat": snap.get("seat", ""),
            "at": snap.get("at", ""),
            "ts": snap.get("ts", 0),
            "status": snap.get("status", ""),
            "files": len(snap.get("files", [])),
            "changed": len([f for f in snap.get("files", []) if f.get("changed")]),
            "error": snap.get("error", ""),
            "note": snap.get("note", ""),
        })
    out.sort(key=lambda r: r["ts"], reverse=True)
    return out[:limit] if limit else out


def verify(tx_id, check_current=True):
    """双向校验：快照自身完整（before 副本在且 hash 对得上）+ 当前落盘态是否还等于 after。"""
    snap = load_snapshot(tx_id)
    d = os.path.join(tx_root(), tx_id)
    rows, ok = [], True
    for rec in snap["files"]:
        row = {"path": rec["path"], "idx": rec["idx"], "exists": None,
               "snapshot_ok": True, "current_ok": None, "changed": rec.get("changed")}
        if rec["existed"] and rec.get("snapshot", True):
            src = os.path.join(d, "%04d.before" % rec["idx"])
            if not os.path.exists(src):
                row["snapshot_ok"] = False
                ok = False
            else:
                with open(src, "rb") as f:
                    row["snapshot_ok"] = sha256_bytes(f.read()) == rec["before_sha"]
                if not row["snapshot_ok"]:
                    ok = False
        elif rec["existed"]:
            row["snapshot_ok"] = None        # 未留底（超限/非普通文件），无法回退
            row["note"] = rec.get("note", "")
        if check_current:
            try:
                cur = read_bytes(rec["path"])
            except TxError:
                cur = None
            row["exists"] = cur is not None
            cur_sha = sha256_bytes(cur)
            row["current_ok"] = (cur_sha == rec.get("after_sha"))
            # 只对「这次事务真写过」的文件断言当前态。changed=False 的文件我们一个字节
            # 都没动（after_sha == before_sha），restore 也不会去碰它 —— 那种文件后来被
            # 别的程序改掉（典型：Codex 自己重写 config.toml、程序重建运行时缓存）不该
            # 判成「回退不可用」。不判它，verify 才和 restore 的口径一致。
            if (not row["current_ok"] and snap.get("status") == "applied"
                    and rec.get("changed", True)):
                ok = False
        rows.append(row)
    return {"id": tx_id, "seat": snap.get("seat", ""), "status": snap.get("status", ""),
            "at": snap.get("at", ""), "ok": ok, "files": rows}


def restore(tx_id, force=False):
    """按快照回滚到写入前。

    默认严格：任何目标文件的当前内容不等于快照记的 after_sha（说明写完之后被
    别的程序或人改过），就中止 —— 保留现场，让人自己决定，而不是粗暴覆盖。
    """
    snap = load_snapshot(tx_id)
    if snap.get("status") not in ("applied", "partial"):
        raise TxError("这次部署的状态是 %s，不可恢复" % snap.get("status"))
    d = os.path.join(tx_root(), tx_id)
    files = [f for f in snap["files"] if f.get("changed", True)]

    conflicts = []
    for rec in files:
        try:
            cur = read_bytes(rec["path"])
        except TxError:
            cur = None
        cur_sha = sha256_bytes(cur)
        if not force and cur_sha != rec.get("after_sha"):
            conflicts.append(rec["path"])
    if conflicts and not force:
        raise TxError("以下文件在部署后又被改动过，已中止恢复并保留现场：\n  "
                      + "\n  ".join(conflicts)
                      + "\n确认要覆盖请加 --force（会丢掉这些改动）")

    restored, failed = [], []
    for rec in sorted(files, key=lambda r: r["idx"], reverse=True):
        try:
            if rec["existed"]:
                src = os.path.join(d, "%04d.before" % rec["idx"])
                if not os.path.exists(src):
                    failed.append(rec["path"])
                    continue
                with open(src, "rb") as f:
                    atomic_write(rec["path"], f.read())
            elif os.path.exists(rec["path"]):
                os.remove(rec["path"])
            restored.append(rec["path"])
        except Exception:
            failed.append(rec["path"])

    snap["status"] = "partial" if failed else "restored"
    snap["restored_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    snap["restored_files"] = restored
    atomic_write(_snap_path(tx_id), json.dumps(snap, ensure_ascii=False, indent=1).encode("utf-8"))
    return {"id": tx_id, "restored": len(restored), "failed": failed,
            "status": snap["status"]}


def purge(keep=KEEP_TX, protect=""):
    """只保留最近 keep 次快照。protect 里的编号不参与清理（正在写的那个）。"""
    root = tx_root()
    if not os.path.isdir(root):
        return 0
    hist = [r for r in list_history() if r["id"] != protect]
    if len(hist) <= keep:
        return 0
    dropped = 0
    for row in hist[keep:]:
        try:
            j = _snap_path(row["id"])
            if os.path.exists(j):
                os.remove(j)
            shutil.rmtree(os.path.join(root, row["id"]), ignore_errors=True)
            dropped += 1
        except Exception:
            pass
    return dropped


__all__ = [
    "TxError", "MAX_BYTES", "set_tx_root", "tx_root",
    "guard_path", "within", "read_bytes", "decode_strict", "atomic_write", "marked",
    "TxSession", "load_snapshot", "list_history", "verify", "restore", "purge",
    "sha256_bytes",
]
