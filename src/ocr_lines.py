# -*- coding: utf-8 -*-
"""把行级 OCR 的检测框还原成有阅读顺序的文本。

**为什么需要单独一层**：行级 OCR 引擎给出的是「一堆带坐标的文本框」，
**顺序不保证**。直接按返回顺序拼接会让阅读顺序错乱，
而阅读顺序错误会以「编辑距离暴涨」的形式污染 CER ——
那是与协议敏感性完全无关的噪声。

本模块只做**顺序还原**，不做任何字符级归一 ——
全角/半角/繁简属于 `protocol.py` 六档协议的职责，两处都做会让协议效应无法归因。
"""

from __future__ import annotations

from typing import Iterable, Sequence, Tuple


def cluster_lines(items: Sequence[dict]) -> list:
    """把行级检测框按垂直中心聚类成文本行，再按水平位置拼成一行。

    `items` 每项需含 `x`（左边界）、`y`（上边界）、`h`（高度）、`text`。
    """
    lines: list = []
    for it in sorted(items, key=lambda d: (d["y"], d["x"])):
        cy = it["y"] + it["h"] / 2.0
        for ln in lines:
            lcy = ln["y"] + ln["h"] / 2.0
            # 垂直中心相差不超过较高者高度的 60% 视为同一行
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


def boxes_to_markdown(boxes: Iterable, texts: Iterable) -> str:
    """(boxes, texts) → 按阅读顺序排列的 Markdown 文本。

    `boxes` 每项为 4 个点的坐标序列，`texts` 为对应的识别文字。
    """
    items = []
    for box, text in zip(boxes, texts):
        if not text:
            continue
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        items.append({"x": min(xs), "y": min(ys),
                      "h": max(ys) - min(ys), "text": text})
    if not items:
        return ""
    return "\n".join(cluster_lines(items))


def line_box_stats(boxes: Sequence, texts: Sequence) -> Tuple[int, int]:
    """返回 (输入框数, 还原后的行数)，用于确认聚类确实合并了同一行的碎片。"""
    n_box = sum(1 for t in texts if t)
    md = boxes_to_markdown(boxes, texts)
    return n_box, (md.count("\n") + 1 if md else 0)
