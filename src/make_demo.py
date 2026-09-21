# -*- coding: utf-8 -*-
"""Synthetic probe set + model outputs generator.

用途
----
在真实截图采集完成之前，让整条流水线**现在就能跑通并被验证**。
所有合成样本在自己仓库内自带真值，因此：

1. 真值精度 100%，零标注成本（对应方案里的「生成 + 退化」策略）
2. 刻意注入协议敏感特征（全角数字 / 繁体混排 / 断行残留空格 / 中英标点混用）
3. 四个"模型"分别模拟真实工具的四类典型输出风格

纪律
----
`source` 字段一律标 `generated`，**不得**混入真实样本统计。
合成数据只能证明"方法可行 + 协议敏感性存在"，不能证明"某工具在真实中文文档上的水平"。
报告里这两句话必须分开写。
"""

from __future__ import annotations

import json
import os
import random
from typing import Dict, List

from .protocol import TRAD_TO_SIMP

_SEED = 20260921

_SIMP_TO_TRAD = {v: k for k, v in TRAD_TO_SIMP.items()}

try:
    import opencc as _oc

    _S2T = _oc.OpenCC("s2t")
except Exception:  # noqa: BLE001
    _S2T = None

_FRAGMENTS = [
    "本次实验选取了三类中文文档作为测试对象，分别为移动端聊天界面截图、公众号长图以及扫描版教材页面。",
    "在预处理阶段，所有样本统一转换为 PNG 格式，分辨率保持在  1080 像素宽度，以保证与线上场景一致。",
    "评测结果显示，表格结构与公式区域的还原质量明显低于纯文本区域……这一点在多栏排版中尤为突出。",
    "当文档中出现全角数字与半角数字混排时，不同工具的输出差异被进一步放大，导致指标波动。",
    "为排除随机性影响，每条样本重复推理三次，取其中位数作为最终输出，并记录推理耗时。",
    "阅读顺序错误是本次观察到的第二大类问题，常见于分栏排版与图文环绕的场景。",
]

_HEADINGS = ["一、实验设置", "二、结果分析", "三、问题讨论", "四、结论与展望", "2.1 数据构成", "3.2 误差来源"]


def _sample_texts(n: int, rng: random.Random) -> List[str]:
    docs = []
    for i in range(n):
        parts = [rng.choice(_HEADINGS)]
        for _ in range(rng.randint(2, 3)):
            parts.append(rng.choice(_FRAGMENTS) + rng.choice(_FRAGMENTS))
        docs.append("\n\n".join(parts))
    return docs


def _to_fullwidth(s: str) -> str:
    out = []
    for ch in s:
        cp = ord(ch)
        if 0x21 <= cp <= 0x7E:
            out.append(chr(cp + 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def _to_traditional(s: str) -> str:
    if _S2T is not None:
        return _S2T.convert(s)
    return "".join(_SIMP_TO_TRAD.get(ch, ch) for ch in s)


def _flatten(s: str) -> str:
    """把段落分隔符压成单换行——模拟「段落结构崩坏」的一类模型输出。

    这类输出在 para / para_merge 切分下会被判成整块漏字，
    但在 whole 切分下分数正常。它专门用来演示「切分方式也是有效自变量」。
    """
    return s.replace("\n\n", "\n")


def _insert_cjk_spaces(s: str, rng: random.Random, p: float = 0.06) -> str:
    out = []
    for ch in s:
        out.append(ch)
        if "\u4e00" <= ch <= "\u9fff" and rng.random() < p:
            out.append(" ")
    return "".join(out)


def _noise(s: str, rng: random.Random, k: int) -> str:
    chars = list(s)
    idxs = [i for i, c in enumerate(chars) if "\u4e00" <= c <= "\u9fff"]
    rng.shuffle(idxs)
    for i in idxs[:k]:
        chars[i] = "□"
    return "".join(chars)


def _upper_latin(s: str) -> str:
    out = []
    for ch in s:
        out.append(ch.upper() if "a" <= ch <= "z" else ch)
    return "".join(out)


def generate(gt_dir: str, models_out: str, n_samples: int = 10) -> Dict:
    """生成合成探针集与四个模型的输出。幂等：同种子多次运行结果一致。"""
    rng = random.Random(_SEED)
    texts = _sample_texts(n_samples, rng)

    sids = ["gen_%04d" % (i + 1) for i in range(n_samples)]
    doc_types = ["chat_screenshot", "official_longimage", "scanned_textbook"]

    os.makedirs(gt_dir, exist_ok=True)
    os.makedirs(models_out, exist_ok=True)

    index = []
    for i, (sid, txt) in enumerate(zip(sids, texts)):
        dt = doc_types[i % len(doc_types)]
        with open(os.path.join(gt_dir, sid + ".md"), "w", encoding="utf-8", newline="\n") as f:
            f.write(txt)
        index.append({
            "sample_id": sid,
            "doc_type": dt,
            "source": "generated",
            "sensitive_attrs": ["fullwidth_digit", "trad_simp_mix", "cjk_linebreak_space"],
            "license": "self-authored, CC-BY-4.0",
        })
    with open(os.path.join(gt_dir, "index.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    # ---- 五种典型输出风格 ----
    # 每种风格只对应一类真实工具的常见偏差，便于把「协议敏感性」归因到具体行为。
    # 用列表而非 dict，保证生成顺序确定。
    variants = [
        ("model_clean", lambda s, r: _noise(s, r, 1)),
        ("model_fullwidth", lambda s, r: _to_fullwidth(s)),
        ("model_spacy", lambda s, r: _insert_cjk_spaces(_upper_latin(s), r)),
        ("model_traditional", lambda s, r: _to_traditional(s)),
        ("model_flatten", lambda s, r: _noise(_flatten(s), r, 1)),
    ]

    counts = {}
    for idx, (name, fn) in enumerate(variants):
        d = os.path.join(models_out, name)
        os.makedirs(d, exist_ok=True)
        # 种子由序号派生，不用 hash()——hash() 对字符串按进程随机化，
        # 会破坏「同一种子多次运行结果一致」的承诺。
        r = random.Random(_SEED + idx * 977)
        for sid, txt in zip(sids, texts):
            with open(os.path.join(d, sid + ".md"), "w", encoding="utf-8", newline="\n") as f:
                f.write(fn(txt, r))
        counts[name] = len(sids)

    return {"n_samples": n_samples, "models": counts, "gt_dir": gt_dir, "models_out": models_out}


if __name__ == "__main__":
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    info = generate(os.path.join(here, "data", "gt"), os.path.join(here, "models_out"))
    print(json.dumps(info, ensure_ascii=False, indent=2))
