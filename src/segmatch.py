# -*- coding: utf-8 -*-
"""三种切分/匹配方式（对齐上游提供的 no_split / simple_match / quick_match）。

上游 README 明确提供三种匹配方法并承认结果不同，但从未公开量化差异。
本模块把三种方法固化为可复现的实现，作为实验的一个自变量。

    S1  whole       整篇作为一个块（对应上游 no_split）
    S2  para        按空行切段，一对一顺序匹配（对应上游 simple_match）
    S3  para_merge  先切段，再合并疑似被截断的段（对应上游 quick_match）

**重要边界**：本实现是按其公开描述的**等价重建**，不是上游源码的复制。
G0 阶段必须与上游源码逐条对齐，对齐前所有结论需标注
`segmode=reconstructed`。这是报告里必须照实写的一句话。

`whole` 模式的一个已知特性：上游同名方法**不输出属性级结果**，
因此最终矩阵中只有 `para` / `para_merge` 可以做属性切片，即 2x6。
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

SEG_MODES: List[str] = ["whole", "para", "para_merge"]

SEG_DESC: Dict[str, str] = {
    "whole": "整篇单块（对应上游 no_split）",
    "para": "按空行切段，一对一匹配（对应上游 simple_match）",
    "para_merge": "切段 + 截断合并（对应上游 quick_match）",
}

# 块尾若带这些终止标点，视为「完整块」，不参与合并
_TERMINATORS = set("。．.!?！？；;:：”\"）)》」』]】")

# 块首若是 Markdown 结构标记，视为新块起点，不参与合并
_STRUCT_HEADS = ("#", "|", "-", "*", ">", "```", "$$", "![")


def split_blocks(text: str, mode: str) -> List[str]:
    """按指定方式把整篇文本切成块。"""
    if mode not in SEG_MODES:
        raise ValueError("unknown seg mode: %r" % mode)

    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []
    if mode == "whole":
        return [raw]

    paras = [p.strip() for p in re.split(r"\n\s*\n+", raw) if p.strip()]
    if mode == "para":
        return paras
    return merge_truncated(paras)


def _is_head(block: str) -> bool:
    if not block:
        return True
    if block.startswith(_STRUCT_HEADS):
        return True
    # 单行且很短，可能是标题
    first = block.split("\n", 1)[0].strip()
    return len(block.split("\n")) == 1 and len(first) <= 12 and not first.endswith(tuple(_TERMINATORS))


def _needs_merge(prev: str, nxt: str) -> bool:
    """上一块未以终止标点收尾，且下一块不像新起点 -> 判为被截断，应合并。"""
    if not prev or not nxt:
        return False
    tail = prev.rstrip()[-1:]
    if tail in _TERMINATORS:
        return False
    if _is_head(nxt):
        return False
    return True


def merge_truncated(paras: List[str]) -> List[str]:
    """把疑似被截断的相邻段合并（对应上游 quick_match 的截断合并）。"""
    out: List[str] = []
    for p in paras:
        if out and _needs_merge(out[-1], p):
            out[-1] = out[-1] + "\n" + p
        else:
            out.append(p)
    return out


def align(ref_blocks: List[str], hyp_blocks: List[str]) -> Tuple[List[Tuple[str, str]], List[str], List[str]]:
    """顺序一对一匹配。

    返回 (匹配对, 未被匹配的 ref 块, 未被匹配的 hyp 块)
    —— 未匹配的 ref 块全部算漏字(D)，未匹配的 hyp 块全部算多字(I)。
    """
    pairs: List[Tuple[str, str]] = []
    k = min(len(ref_blocks), len(hyp_blocks))
    for i in range(k):
        pairs.append((ref_blocks[i], hyp_blocks[i]))
    return pairs, ref_blocks[k:], hyp_blocks[k:]
