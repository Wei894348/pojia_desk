# -*- coding: utf-8 -*-
"""给 builtin:zai-start-plan（当前在用的套餐端点）补一个满血 GLM-5.3 条目。

背景（2026-09-19 09:10 实测）
------------------------------------------------------------
用户当前唯一可用的通道是 `builtin:zai-start-plan`
    baseURL = https://zcode.z.ai/api/v1/zcode-plan/anthropic
    models  = { GLM-5.3-Flash }        ← 只有 Flash

满血 GLM-5.3（非 Flash）只在 `builtin:zai-coding-plan` 的 models 里，
而那个 provider 带 `systemDisabledReason: "coding_plan_not_entitled"`
—— 账号没有 Coding Plan 权益；且它那把 49 字符 key 打 api.z.ai 回 1113
（额度不足）。见 `状态/_zc_keycheck.txt`。

但 zcode.cjs 里有一份常量把 **GLM-5.3 与全部套餐账号类型并列**：
    oko = ["account:zai-start-plan", "account:bigmodel-start-plan",
           "account:zai-individual-coding-plan", ...]
    iko = ["GLM-5.3", "GLM-5.3-Flash", "GLM-5V-Turbo", "GLM-5.2", ...]
⇒ 套餐端点**可能**支持满血 GLM-5.3，只是本地 config 没列出来。
   本脚本就是做这个实验：把条目补上，重启后看界面模型选择器里有没有它。

用法（★ 必须完全退出 ZCode，含托盘，否则改动会被回写覆盖）
------------------------------------------------------------
    python zcode/zcode_add_glm53.py --check     # 只看现状
    python zcode/zcode_add_glm53.py             # 补条目
    python zcode/zcode_add_glm53.py --revert    # 撤销（恢复只留 Flash）

判读
------------------------------------------------------------
- 重启后界面能选到 "GLM-5.3" 且发消息正常 → 套餐支持，问题解决。
- 界面里没有 → 服务端同步的模型清单覆盖了本地，说明套餐真的只给 Flash，
  只能走充值 / 换 key 的路（见报告 NEXT 段）。
- 能选但发消息报错（如 1005 / 1113 / 3012）→ 套餐不含该模型。
"""
import io, json, os, shutil, subprocess, sys, time

CFG = r"C:\Users\86157\.zcode\v2\config.json"
PID = "builtin:zai-start-plan"
DONOR = "builtin:zai-coding-plan"
MODEL = "GLM-5.3"


def zcode_procs():
    """返回 ZCode.exe 进程数（tasklist 输出是 GBK，别用 text=True）。"""
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ZCode.exe", "/FO", "CSV"],
                           capture_output=True, timeout=20)
        return r.stdout.decode("gbk", "replace").count("ZCode.exe")
    except Exception:
        return -1


def load():
    with io.open(CFG, "r", encoding="utf-8") as f:
        return json.load(f)


def state(d):
    ms = (d["provider"][PID].get("models") or {})
    return list(ms.keys())


def check():
    d = load()
    print("配置文件:", CFG)
    print("  mtime:", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(CFG))))
    print(f"\n{PID}")
    for k in state(d):
        r = (d["provider"][PID]["models"][k].get("reasoning") or {})
        print(f"   - {k:16s} reasoning.defaultVariant = {r.get('defaultVariant')}")
    print(f"\n{DONOR}（供体）")
    for k in (d["provider"][DONOR].get("models") or {}):
        print(f"   - {k}")
    print("\nZCode 进程:", end=" ")
    n = zcode_procs()
    if n < 0:
        print("(检测失败)")
    elif n:
        print(f"{n} 个   ⚠ 运行中 —— 它退出时会回写 config.json，"
              f"改动可能被覆盖；请完全退出后重跑本脚本")
    else:
        print("0 个   ✔ 已退出")


def apply():
    d = load()
    if MODEL in state(d):
        print(f"已存在 {MODEL}，无需操作（幂等）")
        return
    donor_models = d["provider"][DONOR].get("models") or {}
    if MODEL not in donor_models:
        print(f"FAIL: 供体 {DONOR} 里也没有 {MODEL}，不能复制结构"); sys.exit(1)
    bak = CFG + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(CFG, bak)
    entry = json.loads(json.dumps(donor_models[MODEL]))   # 深拷贝
    # 套餐段端点用 zcode.z.ai，供体是 api.z.ai —— 只复制模型定义，别带端点痕迹
    d["provider"][PID]["models"][MODEL] = entry
    with io.open(CFG, "w", encoding="utf-8") as f:
        f.write(json.dumps(d, ensure_ascii=False, indent=2))
    print(f"OK  已补 {MODEL} → {PID}")
    print("    现有模型:", state(load()))
    print("    备份:", bak)


def revert():
    d = load()
    if MODEL not in state(d):
        print(f"{MODEL} 不存在，无需撤销")
        return
    bak = CFG + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(CFG, bak)
    del d["provider"][PID]["models"][MODEL]
    with io.open(CFG, "w", encoding="utf-8") as f:
        f.write(json.dumps(d, ensure_ascii=False, indent=2))
    print(f"OK  已移除 {MODEL}")
    print("    现有模型:", state(load()))


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--check" in a:
        check()
    elif "--revert" in a:
        revert()
    else:
        apply()
