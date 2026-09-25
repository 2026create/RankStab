# -*- coding: utf-8 -*-
"""把文本文件的行尾统一为 LF。

    python tools/normalize_eol.py <路径 或 目录> [更多路径...]
    python tools/normalize_eol.py --check <路径...>     # 只检查，不修改

**为什么需要它**：本项目的可复现性契约要求所有产物与真值使用 **LF** 行尾
（见 `SPEC.md` 第 2.4 节与自检断言 E4）。

但实际编辑中它会反复被破坏：Windows 上的编辑器、Office 中转、部分写文件接口
都会悄悄写入 CRLF。而 **CRLF 会多出一个 `\\r` 字符** —— 它会直接进入编辑距离计算，
**污染 CER 且不报错**。这类"静默污染"正是本项目最要防的一类缺陷。

**为什么要与 verify.py 分开**：`verify.py` 是**验收装置**，必须是只读的
（它连产物都不许改）。本工具是**修复装置**，会写文件。两者职责不同，不可合并 ——
一个会改动被验收对象的验收器，其结论没有意义。

行为：只改行尾，不动其他任何字节；`--check` 模式只报告并以退出码表示结果。
"""

from __future__ import annotations

import argparse
import os
import sys

SUFFIXES = (".md", ".txt", ".csv", ".json", ".py", ".html", ".yaml", ".yml",
            ".toml", ".cfg", ".ini", ".gitignore", "NOTICE")
SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", "07_上游复现"}


def _is_text(name: str) -> bool:
    return name.lower().endswith(SUFFIXES) or name in ("NOTICE", "LICENSE")


def _collect(paths) -> list:
    out = []
    for p in paths:
        if os.path.isfile(p):
            out.append(p)
            continue
        for root, dirs, files in os.walk(p):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for n in sorted(files):
                if _is_text(n):
                    out.append(os.path.join(root, n))
    return sorted(set(out))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser(description="统一行尾为 LF")
    ap.add_argument("paths", nargs="+", help="文件或目录")
    ap.add_argument("--check", action="store_true", help="只检查，不修改")
    args = ap.parse_args()

    files = _collect(args.paths)
    if not files:
        print("没有找到可处理的文本文件", file=sys.stderr)
        return 2

    bad = []
    for p in files:
        with open(p, "rb") as f:
            raw = f.read()
        if b"\r\n" not in raw and b"\r" not in raw:
            continue
        bad.append(p)
        if not args.check:
            with open(p, "wb") as f:
                f.write(raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n"))

    print("检查 %d 个文本文件；含 CR 的 %d 个" % (len(files), len(bad)))
    for p in bad:
        print("  " + ("[需修复] " if args.check else "[已统一] ") + p)

    if args.check and bad:
        print("\n[FAIL] 存在非 LF 行尾的文件（用不带 --check 的方式修复）")
        return 1
    print("\n[OK] 全部为 LF")
    return 0


if __name__ == "__main__":
    sys.exit(main())
