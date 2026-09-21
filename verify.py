# -*- coding: utf-8 -*-
"""可复现性验收脚本 —— 评委无需阅读代码，跑这一条命令即可验证全部主张。

    python verify.py

依次验证四件事：

  1. 自检全过          度量实现本身没有问题（85 项断言）
  2. 流水线可跑        18 组重算能完整执行并产出全部文件
  3. 结果可重复        连跑两次，产物**逐字节一致**（sha256 对比）
  4. 提交状态干净      （若在 git 仓库内）工作区无未提交的产物改动

第 3 项是最关键、也最容易被口头声称却很少被真正验证的一项。
这里不复述任何承诺，而是直接对 out/ 目录做两次 sha256 快照再逐文件比对。
它**不依赖 git**，因此下载 zip 解压运行的人也照样能验证。

退出码：0 = 全部通过；1 = 有失败项。
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
OUT = os.path.join(HERE, "out")

# 断言总数下限。只允许随新增断言上调；下调意味着有断言被删掉了。
MIN_ASSERTIONS = 85

# 流水线必须产出的文件，缺一不可（只看退出码不够，脚本可能静默半途而废）
REQUIRED = ["scores.csv", "rank_matrix.csv", "stability.csv",
            "protocol_curve.csv", "meta.json", "report.html"]

USE_COLOR = not (os.name == "nt" and not os.environ.get("WT_SESSION"))
GREEN = "\033[92m" if USE_COLOR else ""
RED = "\033[91m" if USE_COLOR else ""
DIM = "\033[2m" if USE_COLOR else ""
RESET = "\033[0m" if USE_COLOR else ""


def _run(args: list[str]) -> tuple[int, str]:
    r = subprocess.run(
        args, cwd=HERE, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _snapshot(folder: str) -> dict[str, str]:
    """返回 {相对路径: sha256}，忽略 .gitkeep 与子目录占位。"""
    snaps: dict[str, str] = {}
    if not os.path.isdir(folder):
        return snaps
    for name in sorted(os.listdir(folder)):
        p = os.path.join(folder, name)
        if not os.path.isfile(p) or name == ".gitkeep":
            continue
        with open(p, "rb") as f:
            snaps[name] = hashlib.sha256(f.read()).hexdigest()
    return snaps


# --------------------------------------------------------------------- 步骤
def step_tests() -> tuple[bool, str]:
    code, out = _run([PY, os.path.join("tests", "test_all.py")])
    m = re.search(r"通过\s*(\d+)\s*/\s*(\d+)", out)
    if not m:
        return False, "未能解析自检输出（tests/test_all.py 可能异常退出）"
    passed, total = int(m.group(1)), int(m.group(2))
    if passed != total:
        return False, "有断言失败：通过 %d / %d" % (passed, total)
    if total < MIN_ASSERTIONS:
        # 防止「删掉失败的断言让它变绿」——这个下限只允许上调
        return False, "断言总数 %d 少于约定下限 %d，可能有断言被删除" % (total, MIN_ASSERTIONS)
    return code == 0, "通过 %d / %d" % (passed, total)


def step_run_twice() -> tuple[bool, str]:
    """跑两次，比对两次产物的 sha256。这是本脚本的核心。"""
    code1, out1 = _run([PY, "run.py", "--demo"])
    if code1 != 0:
        tail = "\n".join(out1.strip().splitlines()[-3:])
        return False, "第一次运行失败：\n         " + tail.replace("\n", "\n         ")

    missing = [f for f in REQUIRED if not os.path.exists(os.path.join(OUT, f))]
    if missing:
        return False, "缺少产物: " + ", ".join(missing)

    snap1 = _snapshot(OUT)

    code2, out2 = _run([PY, "run.py", "--demo"])
    if code2 != 0:
        tail = "\n".join(out2.strip().splitlines()[-3:])
        return False, "第二次运行失败：\n         " + tail.replace("\n", "\n         ")

    snap2 = _snapshot(OUT)

    # 先比文件集合，再比内容——两者是不同性质的失败
    only1 = sorted(set(snap1) - set(snap2))
    only2 = sorted(set(snap2) - set(snap1))
    if only1 or only2:
        return False, "两次运行产出的文件集合不同：仅第一次有 %s；仅第二次有 %s" % (only1, only2)

    differ = [n for n in snap1 if snap1[n] != snap2[n]]
    if differ:
        detail = []
        for n in differ[:3]:
            detail.append("%s (%s -> %s)" % (n, snap1[n][:10], snap2[n][:10]))
        return False, "两次运行结果不一致，共 %d 个文件：%s" % (len(differ), "; ".join(detail))

    return True, "连跑两次，%d 个产物 sha256 全部一致（%s）" % (
        len(snap1), snap1.get("stability.csv", "")[:12] + "…")


def step_git_clean() -> tuple[bool, str]:
    code, out = _run(["git", "rev-parse", "--is-inside-work-tree"])
    if code != 0 or out.strip() != "true":
        return True, "跳过（非 git 仓库；本项仅作附加确认，不影响上一步结论）"

    code, out = _run(["git", "status", "--porcelain", "--", "out/"])
    if code != 0:
        return True, "跳过（git 命令不可用）"

    dirty = [l for l in out.splitlines() if l.strip()]
    if not dirty:
        return True, "out/ 与已提交版本一致"
    changed = [l.split(maxsplit=1)[-1] for l in dirty]
    return False, "out/ 有未提交改动（%d 个）：%s" % (len(changed), ", ".join(changed[:4]))


STEPS = [
    ("1  自检 85 项断言", step_tests),
    ("2  连跑两次，产物逐字节一致", step_run_twice),
    ("3  out/ 与提交版本一致", step_git_clean),
]


def main() -> int:
    print("=" * 66)
    print("RankStab 可复现性验收")
    print("=" * 66)
    print()

    results = []
    for name, fn in STEPS:
        try:
            ok, detail = fn()
        except Exception as e:  # 验收脚本自身出错也必须显式暴露，不能静默算过
            ok, detail = False, "脚本异常: %s: %s" % (type(e).__name__, e)
        results.append((name, ok, detail))
        tag = GREEN + "PASS" + RESET if ok else RED + "FAIL" + RESET
        print("  [%s] %s" % (tag, name))
        print("         %s%s%s" % (DIM, detail, RESET))
        print()

    failed = [n for n, ok, _ in results if not ok]
    print("-" * 66)
    if failed:
        print("%s未通过：%d / %d%s" % (RED, len(failed), len(results), RESET))
        for n in failed:
            print("   - " + n)
        print()
        print("排查建议：")
        print("  · 两次结果不一致 -> 源码里存在未固定种子的随机源，或读写了时间/环境变量")
        print("  · 产物缺失       -> 检查 run.py 是否中途异常退出")
        print("  · out/ 有改动    -> 改过源码但未重新提交，或手工编辑过产物")
        return 1

    print("%s全部通过：%d / %d%s" % (GREEN, len(results), len(results), RESET))
    print()
    print("这说明：度量实现正确、流水线可运行、结果可重复。")
    print("复现命令：  python verify.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
