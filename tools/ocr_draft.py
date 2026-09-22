# -*- coding: utf-8 -*-
"""用 CPU OCR 为真实截图生成真值**初稿**。

标注工作流是「用机器结果填充再改」，而不是从零手打。本脚本负责前半段：
把截图跑一遍行级 OCR，按阅读顺序拼成 Markdown 草稿。

    python tools/ocr_draft.py --images <图片目录> --out <输出目录>

产出：<out>/<文件名>.md

⚠️ 这些草稿**不是真值**，只是起点。必须逐条人工校对后才能作为 ground truth：
   - OCR 会把繁体识别成简体、把全角识别成半角 —— 而这恰是本项目要测的东西，
     若直接当真值，等于把被测对象写进了标准答案
   - 表格结构、段落归属不会被还原
   - 原文的笔误必须照抄，不得"顺手修正"
   校对入口：tools/中文OCR标注工作台.html

依赖：rapidocr-onnxruntime（纯 CPU，无需 GPU）
"""

from __future__ import annotations

import argparse
import os
import sys
import time

SUPPORTED = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}


def _cluster_lines(items: list) -> list:
    """把行级检测框按垂直中心聚类成文本行，再按水平位置排序拼成一行。

    OCR 引擎给的是行级框，顺序不保证。直接拼接会让阅读顺序错乱，
    而阅读顺序错误会以「编辑距离暴涨」的形式污染 CER —— 那是与协议敏感性无关的噪声。
    """
    lines: list = []
    for it in sorted(items, key=lambda d: (d["y"], d["x"])):
        cy = it["y"] + it["h"] / 2.0
        for ln in lines:
            lcy = ln["y"] + ln["h"] / 2.0
            if abs(cy - lcy) <= max(it["h"], ln["h"]) * 0.6:
                ln["items"].append(it)
                ln["y"] = min(ln["y"], it["y"])
                ln["h"] = max(ln["h"], it["h"])
                break
        else:
            lines.append({"y": it["y"], "h": it["h"], "items": [it]})
    lines.sort(key=lambda ln: ln["y"])
    out = []
    for ln in lines:
        ln["items"].sort(key=lambda d: d["x"])
        out.append(" ".join(d["text"] for d in ln["items"]))
    return out


def ocr_to_markdown(engine, path: str) -> str:
    result, _elapse = engine(path)
    if not result:
        return ""
    items = []
    for box, text, _score in result:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        items.append({"x": min(xs), "y": min(ys), "h": max(ys) - min(ys),
                      "text": text})
    return "\n".join(_cluster_lines(items))


def _force_utf8_stdout() -> None:
    """Windows 中文区域的控制台默认 GBK，输出非 GBK 字符会直接抛 UnicodeEncodeError。

    这与 verify.py 里那处编码缺陷同源。命令行工具的输出编码不该取决于
    运行者的区域设置，因此显式钉死为 UTF-8；失败也不影响主流程。
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


def main() -> int:
    _force_utf8_stdout()
    ap = argparse.ArgumentParser(description="CPU OCR → 真值初稿")
    ap.add_argument("--images", required=True, help="图片目录")
    ap.add_argument("--out", required=True, help="初稿输出目录")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 张（调试用）")
    args = ap.parse_args()

    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        print("缺少依赖：pip install rapidocr-onnxruntime", file=sys.stderr)
        return 2

    names = sorted(n for n in os.listdir(args.images)
                   if os.path.splitext(n)[1].lower() in SUPPORTED)
    if args.limit:
        names = names[:args.limit]
    if not names:
        print("目录下没有图片：%s" % args.images, file=sys.stderr)
        return 1

    os.makedirs(args.out, exist_ok=True)
    engine = RapidOCR()

    t0 = time.time()
    for i, name in enumerate(names, 1):
        src = os.path.join(args.images, name)
        dst = os.path.join(args.out, os.path.splitext(name)[0] + ".md")
        s = time.time()
        md = ocr_to_markdown(engine, src)
        with open(dst, "w", encoding="utf-8", newline="\n") as f:
            f.write(md)
        print("[%2d/%2d] %-18s %5d 字  %5.1fs" % (
            i, len(names), name, len(md), time.time() - s))

    print("-" * 52)
    print("完成 %d 张，用时 %.1fs，输出 %s" % (len(names), time.time() - t0, args.out))
    print("[!] 这是初稿不是真值，须用 tools/中文OCR标注工作台.html 逐条校对。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
