# -*- coding: utf-8 -*-
"""六档文本归一化协议 P0..P5。

设计原则
--------
档位数字 = 对被测评测模型的「宽容度」。数字越大越宽容：

    P0  字面严格   只去首尾空白，其余一字不改
    P1  空白宽容   + 连续空白折叠为单空格
    P2  大小写宽容 + 英文大小写不敏感
    P3  宽度宽容   + 全角字母/数字/符号 -> 半角
    P4  标点宽容   + 中英标点归一
    P5  字形宽容   + 繁体 -> 简体，并删除 CJK 字符之间的残留空格

每一档都是在前一档基础上**追加**规则，因此规则集合单调递增。

关于单调性（重要，且是本项目第一个实质性发现）
--------------------------------------------
直觉上「宽容度越高，错误率越低」，但**这只在固定分母口径下成立**。

若按标准口径 `CER = 编辑距离 / len(归一化后的参考文本)`，
P1 折叠空白会让参考文本变短、分母变小，
于是即使错误数没变，CER 反而**上升**——测试 D7 第一次运行就抓到了这个现象。

这不是 bug，而是一个真实的方法论问题：
**「空白是否计入分母」本身就是一个协议选择，而且它的影响方向与其他规则相反。**

因此本模块输出的两个口径各有用途：
* `cer`       —— 标准口径，用于和公开榜单对齐
* `cer_fixed` —— 固定分母口径（见 metrics.py），用于干净地隔离「协议效应」

单调性断言只对 `cer_fixed` 成立。详见 tests/test_all.py 与 SPEC.md 第 2 节。

与标注工作台的口径一致
----------------------
`PUNCT_MAP` 与 `中文OCR标注工作台.html` 中的实现逐条对应。
工作台用于单人快速查看，本模块用于批量评测，两者必须同口径。
"""

from __future__ import annotations

import re
from typing import Callable, Dict, List

# --------------------------------------------------------------------------
# 档位定义
# --------------------------------------------------------------------------

LEVELS: List[str] = ["P0", "P1", "P2", "P3", "P4", "P5"]

LEVEL_DESC: Dict[str, str] = {
    "P0": "字面严格：仅去首尾空白",
    "P1": "空白宽容：折叠连续空白",
    "P2": "大小写宽容：英文大小写不敏感",
    "P3": "宽度宽容：全角字母数字符号转半角",
    "P4": "标点宽容：中英标点归一",
    "P5": "字形宽容：繁体转简体、删除 CJK 间空格",
}

# 每档新增的规则名，用于报告里解释档位差异
LEVEL_ADDS: Dict[str, str] = {
    "P0": "strip",
    "P1": "collapse_ws",
    "P2": "casefold",
    "P3": "fullwidth_to_halfwidth",
    "P4": "punct_unify",
    "P5": "trad_to_simp + drop_cjk_space",
}

# 规则分类：区分「映射型」与「长度型」。长度型规则会改变分母，
# 是上文所述单调性陷阱的来源，报告中必须单独说明。
RULE_KIND: Dict[str, str] = {
    "strip": "boundary",
    "collapse_ws": "length",
    "casefold": "mapping",
    "fullwidth_to_halfwidth": "mapping",
    "punct_unify": "mapping",
    "trad_to_simp": "mapping",
    "drop_cjk_space": "length",
}

# --------------------------------------------------------------------------
# P4 中英标点归一表（与标注工作台同口径）
# --------------------------------------------------------------------------

PUNCT_MAP: Dict[str, str] = {
    "\uff0c": ",",   # ，
    "\u3002": ".",   # 。
    "\u3001": ",",   # 、
    "\uff1b": ";",   # ；
    "\uff1a": ":",   # ：
    "\uff01": "!",   # ！
    "\uff1f": "?",   # ？
    "\uff08": "(",   # （
    "\uff09": ")",   # ）
    "\u3010": "[",   # 【
    "\u3011": "]",   # 】
    "\u300a": "<",   # 《
    "\u300b": ">",   # 》
    "\u3008": "<",   # 〈
    "\u3009": ">",   # 〉
    "\u201c": '"',   # “
    "\u201d": '"',   # ”
    "\u2018": "'",   # ‘
    "\u2019": "'",   # ’
    "\u2014": "-",   # —
    "\u2015": "-",   # ―
    # 注意：这里必须保持 1:1 映射。
    # 早期实现把「…」展开成 "..."，虽然更"像"它，但会改变文本长度，
    # 从而让 P4 从「映射型规则」变成「长度型规则」，
    # 污染档位间的分数比较（tests 直接抓到了由此产生的单调性违规）。
    "\u2026": ".",   # …
    "\u00b7": ".",   # ·
    "\uff5e": "~",   # ～
    "\u301c": "~",   # 〜
}

# --------------------------------------------------------------------------
# P5 繁体 -> 简体
#   评测一律使用 src/trad_table.py 的【离线冻结快照】（3751 条 t2s 映射）。
#
#   【为什么不再优先用 OpenCC —— 这是一次刻意的可复现性修复，请勿改回】
#   早期实现是「运行时探测环境」：装上 opencc 就用它，装不上才退回快照。
#   后果：同一份代码在两台机器上产出【不同的字节】，而 meta.json 只是默默
#   记了一笔 trad_backend，不会有任何报错。实测于 2026-09-21 触发：
#   提交产物时该环境装有 opencc（trad_backend=opencc），
#   事后复跑时环境无 opencc（trad_backend=snapshot_table），
#   scores.csv / rank_matrix.csv 等 6 个文件因此全部变成 M。
#   对一个主张「可复现」的项目，这是致命缺陷——评委换机器验证必然踩到。
#
#   另有一条独立理由：OpenCC 自身跨版本会改动转换表，
#   若直接依赖它，多年后重跑会悄悄改变已发布的分数。
#   冻结快照则永久稳定。
#
#   因此：快照是【唯一评测路径】。opencc 若恰好存在，仅用于一致性交叉校验
#   （供 tests 使用），【绝不】参与评分路径。
# --------------------------------------------------------------------------

from .trad_table import TRAD_TO_SIMP as _SNAPSHOT_TRAD_TO_SIMP  # noqa: E402

# 评分路径恒定使用快照；该字段不再随环境变化。
TRAD_BACKEND = "snapshot_table"

TRAD_TO_SIMP: Dict[str, str] = dict(_SNAPSHOT_TRAD_TO_SIMP)


def _load_opencc_for_crosscheck():
    """仅用于测试的交叉校验入口：验证快照与 OpenCC 结果一致。

    刻意不参与评分路径，避免把环境差异引入不可复现的评分结果。
    返回 (converter, version) 或 (None, None)。
    """
    try:
        import opencc as _opencc  # type: ignore

        return _opencc.OpenCC("t2s"), getattr(_opencc, "__version__", "unknown")
    except Exception:  # noqa: BLE001
        return None, None


TRAD_TO_SIMP_COVERAGE = {
    "backend": TRAD_BACKEND,
    "size": len(TRAD_TO_SIMP),
    "source": "OpenCC t2s（离线冻结快照）",
    "frozen": True,
    "note": (
        "评测恒定使用离线冻结快照，不随环境变化；"
        "报告中须照实声明该快照由 OpenCC t2s 生成、共 3751 条映射。"
    ),
}

# CJK 空白删除：中文与中文之间的空白视为断行残留
_CJK = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_RE_CJK_SPACE = re.compile(r"(?<=[" + _CJK + r"])[ \t]+(?=[" + _CJK + r"])")


# --------------------------------------------------------------------------
# 单条规则
# --------------------------------------------------------------------------

def r_strip(s: str) -> str:
    return s.strip()


def r_collapse_ws(s: str) -> str:
    """连续空白折叠为单空格。等价于按空白切分再以单空格连接。"""
    return " ".join(s.split())


def r_casefold(s: str) -> str:
    return s.casefold()


def r_fullwidth_to_halfwidth(s: str) -> str:
    """全角 -> 半角。逐字符处理，避免 NFKC 误伤其他字符。"""
    out = []
    for ch in s:
        cp = ord(ch)
        if 0xFF01 <= cp <= 0xFF5E:
            out.append(chr(cp - 0xFEE0))
        elif cp == 0x3000:
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def r_punct_unify(s: str) -> str:
    return "".join(PUNCT_MAP.get(ch, ch) for ch in s)


def r_trad_to_simp(s: str) -> str:
    # 恒定走冻结快照 —— 不依赖运行环境（见上方可复现性说明）
    return "".join(TRAD_TO_SIMP.get(ch, ch) for ch in s)


def r_drop_cjk_space(s: str) -> str:
    return _RE_CJK_SPACE.sub("", s)


# 档位 -> 累计启用的规则序列（顺序有语义，不许调换）
_LEVEL_RULES: Dict[str, List[Callable[[str], str]]] = {
    "P0": [r_strip],
    "P1": [r_strip, r_collapse_ws],
    "P2": [r_strip, r_collapse_ws, r_casefold],
    "P3": [r_strip, r_collapse_ws, r_casefold, r_fullwidth_to_halfwidth],
    "P4": [r_strip, r_collapse_ws, r_casefold, r_fullwidth_to_halfwidth, r_punct_unify],
    "P5": [
        r_strip,
        r_collapse_ws,
        r_casefold,
        r_fullwidth_to_halfwidth,
        r_punct_unify,
        r_trad_to_simp,
        r_drop_cjk_space,
    ],
}


def normalize(s: str, level: str) -> str:
    """按指定档位归一化文本。

    >>> normalize("  甲  乙  ", "P0")
    '甲  乙'
    >>> normalize("  甲  乙  ", "P1")
    '甲 乙'
    """
    if level not in _LEVEL_RULES:
        raise ValueError("unknown protocol level: %r (expect one of %s)" % (level, LEVELS))
    if s is None:
        return ""
    out = str(s)
    for rule in _LEVEL_RULES[level]:
        out = rule(out)
    return out


def enabled_rules(level: str) -> List[str]:
    """返回该档累计启用的规则名，用于报告与协议文档。"""
    idx = LEVELS.index(level)
    return [LEVEL_ADDS[LEVELS[i]] for i in range(idx + 1)]


def punct_map_is_length_preserving() -> bool:
    """P4 的映射表是否全部为 1:1。

    这个性质是「P4 属于映射型规则」的前提，也是档位可比性的前提。
    一旦有人在 PUNCT_MAP 里加了会改变长度的映射（例如把「…」展开成 "..."），
    RULE_KIND 的标注就会失真，本函数与 tests 的 A26/A27 会一起报警。
    """
    return all(len(k) == len(v) for k, v in PUNCT_MAP.items())


def length_affecting_rules(level: str) -> List[str]:
    """返回该档中会改变文本长度的规则（单调性陷阱的来源）。

    level_adds 里的值是展示用的短语（可能形如 `a + b`），
    这里按 token 逐个查 RULE_KIND，避免复合规则被漏掉。
    """
    out: List[str] = []
    for item in enabled_rules(level):
        for token in item.replace("+", " ").split():
            if RULE_KIND.get(token) == "length" and token not in out:
                out.append(token)
    return out
