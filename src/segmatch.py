# -*- coding: utf-8 -*-
r"""三种文本切分及单调块匹配。

上游 README 明确提供三种匹配方法并承认结果不同。
本模块按公开描述重建三种方法，作为实验的一个自变量，由本项目量化差异。

    S1  whole       整篇作为一个块
    S2  para        非空行切块，单调全局对齐
    S3  para_merge  按行切块，再启发式合并疑似被截断的块

**重要边界**：本实现按上游公开描述重建，尚未验证与上游源码的等价性。
G0 阶段必须与上游源码逐条对齐，对齐前所有结论需标注
`segmode=reconstructed`。这是报告里必须照实写的一句话。

上游匈牙利指派与本项目单调对齐的受控差异见测试产物中的上游匹配对照。
"""

from __future__ import annotations

from typing import Dict, List, Tuple

SEG_MODES: List[str] = ["whole", "para", "para_merge"]

SEG_DESC: Dict[str, str] = {
    "whole": "整篇单块",
    "para": "非空行切块 + 单调全局对齐",
    "para_merge": "行块启发式合并 + 单调全局对齐",
}

# 块尾若带这些终止标点，视为「完整块」，不参与合并
_TERMINATORS = set("。．.!?！？；;:：”\"）)》」』]】")

# 块首若是 Markdown 结构标记，视为新块起点，不参与合并
_STRUCT_HEADS = ("#", "|", "-", "*", ">", "```", "$$", "![")


def split_blocks(text: str, mode: str) -> List[str]:
    """按指定方式把整篇文本切成块。

    块的基本单位是**非空文本行**，空行只作分隔符、不单独成块。
    这样定义是因为本项目真值与模型输出都是行级 OCR 文本（每行一条识别行，
    行间不以空行分隔）：若按空行切段，整页会退化为一个块，`para` 与 `whole`
    在数据上完全不可区分（50 页实测 24 组分数逐位相同），三种切分只剩一种。
    按行切块与上游 `simple_match` 的逐块匹配也更接近。
    """
    if mode not in SEG_MODES:
        raise ValueError("unknown seg mode: %r" % mode)

    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []
    if mode == "whole":
        return [raw]

    lines = [ln.strip() for ln in raw.split("\n")]
    lines = [ln for ln in lines if ln]
    if mode == "para":
        return lines
    return merge_truncated(lines)


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
    """启发式合并疑似被截断的相邻行块。"""
    out: List[str] = []
    for p in paras:
        if out and _needs_merge(out[-1], p):
            out[-1] = out[-1] + "\n" + p
        else:
            out.append(p)
    return out


def _default_cost(a: str, b: str) -> float:
    """块间替换成本（字符级 Levenshtein）；空串即整块删除/插入成本。"""
    if not a:
        return float(len(b))
    if not b:
        return float(len(a))
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return float(prev[-1])


def align(ref_blocks: List[str], hyp_blocks: List[str],
          cost_fn=None) -> Tuple[List[Tuple[str, str]], List[str], List[str]]:
    """块级单调全局对齐（Needleman–Wunsch），允许整块跳过。

    返回 (匹配对, 未被匹配的 ref 块, 未被匹配的 hyp 块)
    —— 未匹配的 ref 块全部算漏字(D)，未匹配的 hyp 块全部算多字(I)。

    **为什么不是顺序一对一**：行级 OCR 输出与真值的行数经常不相等
    （50 页实测真值 9 行的页面，输出可达 17 行）。顺序一对一从第一个
    多出/缺失的行起，后续全部错位，会把一次行切分差异放大成整页错配，
    使 CER 从 0.10 量级涨到 0.8 以上——那是配对伪影，不是切分效应。
    单调对齐用动态规划选成本最小的「配对 / 跳过」组合，保留了块顺序约束
    （上游 Hungarian 指派不保证单调，见 SPEC 第 3 节的等价性声明）。
    """
    cost = cost_fn or _default_cost
    n, m = len(ref_blocks), len(hyp_blocks)
    if n == 0 or m == 0:
        return [], list(ref_blocks), list(hyp_blocks)

    _cache: Dict[Tuple[int, int], float] = {}

    def c(i: int, j: int) -> float:
        key = (i, j)
        if key not in _cache:
            _cache[key] = cost(ref_blocks[i] if i >= 0 else "",
                               hyp_blocks[j] if j >= 0 else "")
        return _cache[key]

    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = dp[i - 1][0] + c(i - 1, -1)
    for j in range(1, m + 1):
        dp[0][j] = dp[0][j - 1] + c(-1, j - 1)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            dp[i][j] = min(dp[i - 1][j] + c(i - 1, -1),
                           dp[i][j - 1] + c(-1, j - 1),
                           dp[i - 1][j - 1] + c(i - 1, j - 1))

    eps = 1e-12
    pairs: List[Tuple[str, str]] = []
    ref_left: List[str] = []
    hyp_left: List[str] = []
    i, j = n, m
    while i > 0 and j > 0:
        if abs(dp[i][j] - (dp[i - 1][j - 1] + c(i - 1, j - 1))) < eps:
            pairs.append((ref_blocks[i - 1], hyp_blocks[j - 1]))
            i -= 1
            j -= 1
        elif abs(dp[i][j] - (dp[i - 1][j] + c(i - 1, -1))) < eps:
            ref_left.append(ref_blocks[i - 1])
            i -= 1
        else:
            hyp_left.append(hyp_blocks[j - 1])
            j -= 1
    while i > 0:
        ref_left.append(ref_blocks[i - 1])
        i -= 1
    while j > 0:
        hyp_left.append(hyp_blocks[j - 1])
        j -= 1

    pairs.reverse()
    ref_left.reverse()
    hyp_left.reverse()
    return pairs, ref_left, hyp_left
