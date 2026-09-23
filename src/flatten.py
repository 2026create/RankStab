# -*- coding: utf-8 -*-
r"""Markdown → 纯文本降级 —— 「分层口径」的基础件。

**为什么需要它**（问题来自实测，不是设想）：

在上游 demo 数据集上做首跑时，`para_merge` 口径下 CER 高达 1.66、
`del_rate = 0.94` —— 可是 rapidocr 在某一页上输出 9084 字、真值 8981 字，
**长度几乎相同却被判 94% 漏字**。

根因不是识别差，而是**两侧的文本范式不同**：

    OmniDocBench 真值 ：带 # 标题、<table> 表格、$公式$ 的 Markdown，按空行切出几十个块
    行级 OCR 输出     ：没有段落结构的一整块文本

两者块数悬殊 → 一对一配对大量失败 → GT 的块被整块计为漏字。

**结论**：行级 OCR 与结构化 Markdown 真值**不可直接比对**。
必须先让两侧落到同一范式：把 Markdown 降级为纯文本。

这也正是 `D-003` 边界声明的落地 —— 当时只是说"行级 OCR 只跑文本子任务"，
本模块把"文本子任务"具体化为可执行的一步。

**降级规则**（每条都对应一类会污染编辑距离的标记）：

| 输入 | 输出 | 理由 |
|---|---|---|
| ` ```lang ... ``` ` | 围栏去掉，内容保留 | 围栏本身不是文档内容 |
| `<td>` / `</td>` | 制表符 | 表格拍平为一行一“记录” |
| `<tr>` / `</tr>` | 换行 | 同上 |
| `<br>` | 换行 | |
| 其余 HTML 标签 | 删除（保留标签内文本） | |
| `![alt](url)` | `alt` | 图片本体没有文字，alt 是它唯一的文本 |
| `[text](url)` | `text` | URL 不是文档正文 |
| 行首 `#` `>` `-` `*` `+` `1.` | 删除 | 结构标记 |
| `**粗**` `__粗__` `*斜*` `` `码` `` | 去掉标记保留文字 | |
| `\(x\)` `$x$` `$$x$$` | 去掉定界符保留内容 | 公式本体是文本的一部分 |
| 多余空行 | 压成单空行 | |

**明确不做的事**：
- **不做 Unicode 归一**（全角/半角、繁简）—— 那是 `protocol.py` 六档协议的职责，
  两处都做会让协议效应无法归因。本模块只处理"标记"，不处理"字符"。
- **不删除公式内容** —— 删除会让长公式页面凭空变短，同样污染编辑距离。
"""

from __future__ import annotations

import re

# HTML 里需要保留结构信息的标签：表格按行列拍平，换行标签转真换行
_TD = re.compile(r"</t[dh]>\s*", re.I)
_TR = re.compile(r"</tr>\s*", re.I)
_BR = re.compile(r"<br\s*/?>\s*", re.I)
_ANY_TAG = re.compile(r"<[^>]+>")

# Markdown 结构标记
_FENCE = re.compile(r"^\s*(```|~~~)[^\n]*$", re.M)
_IMG = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HEAD = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_QUOTE = re.compile(r"^\s{0,3}>\s?", re.M)
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", re.M)
_HR = re.compile(r"^\s*(?:[-*_]\s*){3,}$", re.M)
_EMPH = re.compile(r"(\*\*|__|\*|_|`)(.+?)\1", re.S)

# 公式定界符：只去壳，不吃内容
_MATH1 = re.compile(r"\\\((.+?)\\\)", re.S)
_MATH2 = re.compile(r"\\\[(.+?)\\\]", re.S)
_MATH3 = re.compile(r"\$\$(.+?)\$\$", re.S)
_MATH4 = re.compile(r"(?<!\$)\$([^$\n]+?)\$(?!\$)")
_MATH5 = re.compile(r"\\begin\{(\w+)\}(.+?)\\end\{\1\}", re.S)

_MULTI_BLANK = re.compile(r"\n{3,}")
_TRAIL_SPACE = re.compile(r"[ \t]+$", re.M)


def md_to_plain(text: str) -> str:
    """把 Markdown（含内嵌 HTML 表格）降级为纯文本。

    只处理「标记」，不处理「字符」—— 全角/半角/繁简的归一是六档协议的职责。
    """
    if not text:
        return ""

    s = text

    # 1) 代码围栏：去掉围栏行，保留其中内容
    s = _FENCE.sub("", s)

    # 2) HTML：先处理有结构含义的标签，再清掉其余
    s = _BR.sub("\n", s)
    s = _TD.sub("\t", s)
    s = _TR.sub("\n", s)
    s = _ANY_TAG.sub("", s)

    # 3) 公式定界符（先长后短，避免 $$ 被 $ 规则截断）
    s = _MATH3.sub(r"\1", s)
    s = _MATH2.sub(r"\1", s)
    s = _MATH1.sub(r"\1", s)
    s = _MATH5.sub(r"\2", s)
    s = _MATH4.sub(r"\1", s)

    # 4) 链接与图片
    s = _IMG.sub(r"\1", s)
    s = _LINK.sub(r"\1", s)

    # 5) 行级结构标记
    s = _HR.sub("", s)
    s = _HEAD.sub("", s)
    s = _QUOTE.sub("", s)
    s = _BULLET.sub("", s)

    # 6) 行内强调标记
    for _ in range(3):                      # 嵌套时反复剥离，如 **`x`**
        new = _EMPH.sub(r"\2", s)
        if new == s:
            break
        s = new

    # 7) 空白整理
    s = _TRAIL_SPACE.sub("", s)
    s = _MULTI_BLANK.sub("\n\n", s)
    return s.strip()


def plain_stats(text: str) -> dict:
    """降级前后的规模对比，用于确认降级确实生效。"""
    p = md_to_plain(text)
    return {
        "raw_chars": len(text),
        "plain_chars": len(p),
        "raw_lines": text.count("\n") + 1,
        "plain_lines": p.count("\n") + 1,
    }
