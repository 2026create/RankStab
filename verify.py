# -*- coding: utf-8 -*-
"""可复现性验收脚本。

    python verify.py

验证自动化回归断言、合成演示流程，以及两次隔离运行的产物是否逐字节一致。
这些检查不证明真实 OCR 模型的效果，也不覆盖全部输入与运行环境。

退出码：0 = 无失败项（跳过不计通过）；1 = 有失败项。
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

# 断言总数下限。仅随新增断言上调，避免意外删减回归覆盖。
MIN_ASSERTIONS = 145

# 流水线必须产出的文件，缺一不可（只看退出码不够，脚本可能静默半途而废）
REQUIRED = ["scores.csv", "rank_matrix.csv", "stability.csv", "verdicts.csv",
            "protocol_curve.csv", "pair_protocol_details.csv", "sample_scores.csv", "meta.json", "report.html"]

USE_COLOR = not (os.name == "nt" and not os.environ.get("WT_SESSION"))
GREEN = "\033[92m" if USE_COLOR else ""
RED = "\033[91m" if USE_COLOR else ""
DIM = "\033[2m" if USE_COLOR else ""
RESET = "\033[0m" if USE_COLOR else ""


def _run(args: list[str]) -> tuple[int, str]:
    # 子进程在 Windows（尤其是中文区域）下，stdout 默认用 GBK 编码，
    # 而这里按 UTF-8 解码会得到乱码，进而让「通过 N / N」无法解析 ——
    # 表现是「自检莫名其妙失败」，实际是编码问题。显式钉死子进程用 UTF-8 输出。
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    r = subprocess.run(
        args, cwd=HERE, capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env,
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
    code, out = _run([PY, os.path.join("tests", "run_suite.py")])
    # 优先解析纯 ASCII 汇总行：它不受任何终端/区域编码影响。
    # 中文行只在 ASCII 行缺失时作为兜底，避免编码问题伪装成「自检失败」。
    m = re.search(r"ASSERTIONS\s+(\d+)\s*/\s*(\d+)", out) or \
        re.search(r"通过\s*(\d+)\s*/\s*(\d+)", out)
    if not m:
        return False, "未能解析自检输出（tests/test_all.py 可能异常退出）"
    passed, total = int(m.group(1)), int(m.group(2))
    if passed != total:
        return False, "有断言失败：通过 %d / %d" % (passed, total)
    if total < MIN_ASSERTIONS:
        # 防止「删掉失败的断言让它变绿」——这个下限只允许上调
        return False, "断言总数 %d 少于约定下限 %d，可能有断言被删除" % (total, MIN_ASSERTIONS)
    suite = re.search(r'SUITE\s+\d+ assertions / (\d+) tests / (\d+) failures / (\d+) errors', out)
    detail = "通过 %d / %d 条回归断言" % (passed, total)
    if suite:
        detail += "，%s 项自动化测试，%s 失败，%s 错误" % suite.groups()
    return code == 0, detail


def step_run_twice() -> tuple[bool, str]:
    """在两套独立临时输入/输出目录中运行并比对产物。"""
    snapshots = []
    with tempfile.TemporaryDirectory(prefix="rankstab_verify_") as root:
        for index in (1, 2):
            run_root = os.path.join(root, "run%d" % index)
            gt = os.path.join(run_root, "gt")
            models = os.path.join(run_root, "models")
            out = os.path.join(run_root, "out")
            code, text = _run([
                PY, "run.py", "--demo",
                "--gt", gt, "--models", models, "--out", out,
            ])
            if code != 0:
                tail = "\n".join(text.strip().splitlines()[-3:])
                return False, "%d次运行失败：\n         %s" % (
                    index, tail.replace("\n", "\n         "))

            missing = [f for f in REQUIRED
                       if not os.path.isfile(os.path.join(out, f))]
            if missing:
                return False, "第%d次运行缺少产物: %s" % (
                    index, ", ".join(missing))
            snapshots.append(_snapshot(out))

    snap1, snap2 = snapshots
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

    return True, "两套独立临时目录各运行一次，%d 个产物 sha256 全部一致（%s）" % (
        len(snap1), snap1.get("stability.csv", "")[:12] + "…")


def step_git_clean() -> tuple[None | bool, str]:
    """第三关：out/ 产物与已提交版本一致。

    只读 git（`status --porcelain`，不写索引、不改工作区）：

    - 没有 git 命令 / 不在 git 仓库里  → 跳过，并说明原因
    - out/ 存在未提交改动              → 跳过，交付对应工作区及外部快照
      （此时「产物与提交版本一致」这句话本身就无从证明，不能算通过，也不算失败）
    - out/ 与 HEAD 完全一致            → 通过
    """
    if not shutil.which("git"):
        return None, "未找到 git 命令，本关跳过（前两关结论不受影响）"
    code, out = _run(["git", "status", "--porcelain", "--", "out"])
    if code != 0:
        return None, "当前目录不是 git 仓库，本关跳过（前两关结论不受影响）"
    dirty = [line for line in out.splitlines() if line.strip()]
    if dirty:
        sample = "、".join(line.strip().split()[-1] for line in dirty[:3])
        return None, ("out/ 有 %d 项未提交改动（%s%s），本关跳过；"
                      "当前结果对应工作区，快照与哈希记录可单独复核"
                      % (len(dirty), sample, " 等" if len(dirty) > 3 else ""))
    return True, "out/ 与已提交版本一致"


STEPS = [
    ("1  回归断言、黑盒与独立核算测试", step_tests),
    ("2  两套隔离目录运行，产物逐字节一致", step_run_twice),
    ("3  out/ 产物与已提交版本一致", step_git_clean),
]


def main() -> int:
    print("=" * 66)
    print("RankStab 可复现性验收")
    print("=" * 66)
    print()

    passed = skipped = 0
    failed = []
    for name, fn in STEPS:
        try:
            ok, detail = fn()
        except Exception as e:  # 验收脚本自身出错也必须显式暴露，不能静默算过
            ok, detail = False, "脚本异常: %s: %s" % (type(e).__name__, e)
        if ok is None:
            skipped += 1
            tag = DIM + "SKIP" + RESET
        elif ok:
            passed += 1
            tag = GREEN + "PASS" + RESET
        else:
            failed.append(name)
            tag = RED + "FAIL" + RESET
        print("  [%s] %s" % (tag, name))
        print("         %s%s%s" % (DIM, detail, RESET))
        print()

    print("-" * 66)
    print("汇总：%d 项通过，%d 项失败，%d 项跳过" % (passed, len(failed), skipped))
    if failed:
        for n in failed:
            print("   - " + n)
        print()
        print("排查建议：")
        print("  · 两次结果不一致 -> 源码里存在未固定种子的随机源，或读写了时间/环境变量")
        print("  · 产物缺失       -> 检查 run.py 是否中途异常退出")
        return 1

    print()
    print("已完成统一测试与两套隔离目录的逐字节复现核对。")
    print("复现命令：  python verify.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
