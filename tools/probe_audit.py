# -*- coding: utf-8 -*-
"""探针集特征覆盖审计 —— 回答「这批样本到底覆盖了哪些协议敏感特征」。

    python tools/probe_audit.py --texts <文本目录> [--out <csv>]

设计动因：样本的 `sensitive_attrs` 若靠人凭印象填，必然出错且不可复现。
本项目已经吃过一次亏（凭记忆填的清单把一条数学题写成了聊天记录）。
改为从**文本本身**机械检测 —— 结论可复现，也能被第三方复核。

检测逻辑直接复用 `src/protocol.py` 的规则表：**判据与评分口径同源**，
不另起一套，避免"审计说覆盖了、评分时却没反应"这种自相矛盾。

注意：输入通常是 OCR 初稿，初稿本身有错字，故检测结果偏保守
（漏检多于误检）。真值定稿后应重跑一次。
"""

from __future__ import annotations

import argparse
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from src.protocol import PUNCT_MAP, TRAD_TO_SIMP  # noqa: E402

# 只在繁体侧存在、简体侧不同的字 —— 出现即说明文本含繁体特征
TRAD_ONLY = {c for c, s in TRAD_TO_SIMP.items() if c != s}

FULLWIDTH_DIGIT = {chr(c) for c in range(0xFF10, 0xFF1A)}
FULLWIDTH_ALPHA = {chr(c) for c in range(0xFF21, 0xFF3B)} | \
                  {chr(c) for c in range(0xFF41, 0xFF5B)}
FULLWIDTH_PUNCT = set(PUNCT_MAP.keys())
ASCII_PUNCT = set(",.;:!?()[]{}<>\"'")

EMOJI_RANGES = ((0x1F300, 0x1FAFF), (0x2600, 0x27BF), (0xFE0F, 0xFE0F))


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF


def _is_ascii_alpha(ch: str) -> bool:
    return ("a" <= ch <= "z") or ("A" <= ch <= "Z")


def _is_emoji(ch: str) -> bool:
    o = ord(ch)
    return any(a <= o <= b for a, b in EMOJI_RANGES)


def audit(text: str) -> dict:
    chars = set(text)
    lines = text.split("\n")

    has_cjk = any(_is_cjk(c) for c in chars)
    has_latin = any(_is_ascii_alpha(c) for c in chars)

    # 中文断行处多出的空格：CJK + 空格 + CJK
    cjk_space = False
    for i in range(1, len(text) - 1):
        if text[i] in " \t" and _is_cjk(text[i - 1]) and _is_cjk(text[i + 1]):
            cjk_space = True
            break

    multi_space = any("  " in ln or "\t" in ln for ln in lines)
    indent = any(ln[:1] in (" ", "\t") for ln in lines if ln.strip())

    fullwidth_punct_hit = sorted(chars & FULLWIDTH_PUNCT)
    ascii_punct_hit = sorted(chars & ASCII_PUNCT)

    return {
        "n_chars": len(text),
        "n_lines": len(lines),
        "trad_only": len(chars & TRAD_ONLY),
        "fullwidth_digit": len(chars & FULLWIDTH_DIGIT),
        "fullwidth_alpha": len(chars & FULLWIDTH_ALPHA),
        "cjk_latin_mix": int(has_cjk and has_latin),
        "fullwidth_punct": len(fullwidth_punct_hit),
        "ascii_punct": len(ascii_punct_hit),
        "punct_mixed": int(bool(fullwidth_punct_hit) and bool(ascii_punct_hit)),
        "cjk_space": int(cjk_space),
        "multi_space": int(multi_space),
        "indent": int(indent),
        "emoji": int(any(_is_emoji(c) for c in chars)),
    }


# 特征名 → 触发它的协议档位（用于回答「这批样本能不能测出该档的效果」）
ATTR_TO_LEVEL = {
    "fullwidth_digit": "P3 宽度宽容",
    "fullwidth_alpha": "P3 宽度宽容",
    "punct_mixed": "P4 标点宽容",
    "trad_only": "P5 字形宽容",
    "cjk_latin_mix": "P1/P5",
    "cjk_space": "P5 drop_cjk_space",
    "multi_space": "P1 空白折叠",
    "indent": "P1 空白折叠",
    "emoji": "各档（特殊符号）",
}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser(description="探针集特征覆盖审计")
    ap.add_argument("--texts", required=True, help="文本目录（OCR 初稿或真值）")
    ap.add_argument("--out", default="", help="输出 CSV 路径")
    args = ap.parse_args()

    names = sorted(n for n in os.listdir(args.texts) if n.endswith(".md"))
    if not names:
        print("目录下没有 .md：%s" % args.texts, file=sys.stderr)
        return 1

    rows = []
    for n in names:
        with open(os.path.join(args.texts, n), "r", encoding="utf-8") as f:
            r = audit(f.read())
        r["sample_id"] = os.path.splitext(n)[0]
        rows.append(r)

    cols = ["sample_id", "n_chars", "n_lines", "trad_only", "fullwidth_digit",
            "fullwidth_alpha", "cjk_latin_mix", "fullwidth_punct", "ascii_punct",
            "punct_mixed", "cjk_space", "multi_space", "indent", "emoji"]

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)

    # ---- 汇总：每个特征有多少样本命中 ----
    print("探针集覆盖审计  (样本 %d 条，共 %d 字)" % (
        len(rows), sum(r["n_chars"] for r in rows)))
    print("=" * 58)
    print("%-18s %6s   %s" % ("特征", "命中", "关系到哪一档"))
    print("-" * 58)
    weak = []
    for attr, level in ATTR_TO_LEVEL.items():
        hit = sum(1 for r in rows if r[attr])
        flag = ""
        if hit == 0:
            flag = "  <== 空白"
            weak.append(attr)
        elif hit < 3:
            flag = "  <== 偏少"
            weak.append(attr)
        print("%-18s %5d/%-3d %s%s" % (attr, hit, len(rows), level, flag))

    print("-" * 58)
    if weak:
        print("[!] 覆盖不足的特征：%s" % ", ".join(weak))
        print("    这些档位在这批样本上可能测不出位移 —— 需补样本或声明为已知局限。")
    else:
        print("[OK] 全部特征均 >=3 条样本覆盖。")
    if args.out:
        print("明细已写入 %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
