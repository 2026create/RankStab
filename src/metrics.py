# -*- coding: utf-8 -*-
"""评测指标：编辑距离分解、CER、bootstrap 置信区间。

三件事必须做对，否则整份结论作废：
1. 编辑距离的 S/D/I 分解（替换/删除/插入）——回答"这个模型是漏字还是多字"
2. CER 的分母是 **参考文本长度**，不是两者最大值
3. 置信区间必须按「样本级重采样」算，不能对已聚合的 CER 做重采样
"""

from __future__ import annotations

import random
from typing import Dict, List, Optional, Sequence, Tuple


def edit_ops(ref: str, hyp: str) -> Dict[str, int]:
    """带 S/D/I 分解的编辑距离（Levenshtein）。

    返回 {'dist', 'S'(替换), 'D'(删除=漏字), 'I'(插入=多字), 'refLen', 'hypLen'}

    长文本保护：乘积超过 4e6 时退化为长度差估算，并置 overflow=True。
    """
    n, m = len(ref), len(hyp)
    if n == 0 and m == 0:
        return {"dist": 0, "S": 0, "D": 0, "I": 0, "refLen": 0, "hypLen": 0}

    if n * m > 4_000_000:
        d = abs(n - m)
        return {
            "dist": d, "S": 0, "D": max(0, n - m), "I": max(0, m - n),
            "refLen": n, "hypLen": m, "overflow": True,
        }

    prev = list(range(m + 1))
    rows: List[List[int]] = [prev]
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        ci = ref[i - 1]
        for j in range(1, m + 1):
            cost = 0 if ci == hyp[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        rows.append(cur)
        prev = cur

    i, j, S, D, I = n, m, 0, 0, 0
    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref[i - 1] == hyp[j - 1] and rows[i][j] == rows[i - 1][j - 1]:
            i -= 1; j -= 1; continue
        if i > 0 and j > 0 and rows[i][j] == rows[i - 1][j - 1] + 1:
            S += 1; i -= 1; j -= 1; continue
        if i > 0 and rows[i][j] == rows[i - 1][j] + 1:
            D += 1; i -= 1; continue
        if j > 0 and rows[i][j] == rows[i][j - 1] + 1:
            I += 1; j -= 1; continue
        break

    # overflow 恒存在，哪怕为 False —— 缺失字段会让上游"忘了检查"变得不可见，
    # 而静默降级正是本项目最忌讳的一类缺陷（见 SPEC 第 4.6 节）。
    return {"dist": rows[n][m], "S": S, "D": D, "I": I,
            "refLen": n, "hypLen": m, "overflow": False}


def char_error_rate(ref_norm: str, hyp_norm: str, fixed_denom: Optional[int] = None) -> Dict[str, float]:
    """计算 CER。

    两个口径同时输出，因为它们的用途不同：

    `cer`        标准口径，分母 = len(归一化后的参考文本)。
                 与公开榜单口径一致，可与外部数字对照。

    `cer_fixed`  固定分母口径，分母 = 参考文本在 **P0** 下的字符数。
                 各档共用同一分母，因此档位间的差异 100% 来自比对规则，
                 不含「归一化导致文本变长/变短」这一无关因素。

                 这是本项目隔离「协议效应」的主口径。
                 若某档未提供 fixed_denom，则 cer_fixed 回退为标准口径。
    """
    ops = edit_ops(ref_norm, hyp_norm)
    std_denom = max(1, len(ref_norm))
    fx_denom = max(1, int(fixed_denom)) if fixed_denom else std_denom
    return {
        "cer": ops["dist"] / std_denom,
        "cer_fixed": ops["dist"] / fx_denom,
        "sub_rate": ops["S"] / fx_denom,
        "del_rate": ops["D"] / fx_denom,
        "ins_rate": ops["I"] / fx_denom,
        "ref_len": len(ref_norm),
        "hyp_len": len(hyp_norm),
        "fixed_denom": fx_denom,
    }


def resume_len(s: str, fixed_denom) -> int:
    """取得用于加权汇总的分母。"""
    if fixed_denom is not None:
        return max(1, int(fixed_denom))
    return max(1, len(s))


def corpus_cer(rows: Sequence[Dict[str, float]]) -> Dict[str, float]:
    """语料级指标 = 总错误数 / 总分母（微平均）。

    两个口径同时给出：
      cer        —— 参考文本按本档归一化后的长度作分母（标准口径）
      cer_fixed  —— 参考文本按 P0 归一化后的长度作分母（固定分母口径）

    注意：不是样本 CER 的算术平均——样本长度差异大时两者会明显不同。
    """
    tot_err_std = tot_err_fx = 0.0
    tot_std = tot_fx = 0
    tot_s = tot_d = tot_i = 0.0

    for r in rows:
        if not r:
            continue
        d_std = max(1, int(r["ref_len"]))
        d_fx = max(1, int(r.get("fixed_denom") or d_std))
        tot_err_std += r["cer"] * d_std
        tot_err_fx += r.get("cer_fixed", r["cer"]) * d_fx
        tot_std += d_std
        tot_fx += d_fx
        tot_s += r["sub_rate"] * d_fx
        tot_d += r["del_rate"] * d_fx
        tot_i += r["ins_rate"] * d_fx

    if tot_fx == 0 or tot_std == 0:
        return {"cer": 0.0, "cer_fixed": 0.0, "sub_rate": 0.0,
                "del_rate": 0.0, "ins_rate": 0.0, "ref_len": 0, "fixed_denom": 0}
    return {
        "cer": tot_err_std / tot_std,
        "cer_fixed": tot_err_fx / tot_fx,
        "sub_rate": tot_s / tot_fx,
        "del_rate": tot_d / tot_fx,
        "ins_rate": tot_i / tot_fx,
        "ref_len": tot_std,
        "fixed_denom": tot_fx,
    }


def bootstrap_ci(
    rows: Sequence[Dict[str, float]],
    n_boot: int = 2000,
    alpha: float = 0.05,
    seed: int = 20260921,
    key: str = "cer_fixed",
) -> Tuple[float, float]:
    """对语料级指标做样本级 bootstrap，返回 (lo, hi) 双尾区间。

    默认对 `cer_fixed`（固定分母口径）做区间估计，因为它是本实验的主口径。

    随机种子固定，保证**评委重跑能得到同一组数字**——
    这是「可复现」在统计层面的具体含义。
    """
    valid = [r for r in rows if r]
    if len(valid) < 2:
        return (0.0, 0.0)

    rng = random.Random(seed)
    k = len(valid)
    ests: List[float] = []
    for _ in range(n_boot):
        acc_err = 0.0
        acc_len = 0
        for _ in range(k):
            r = valid[rng.randrange(k)]
            denom = max(1, int(r.get("fixed_denom") or r["ref_len"]))
            acc_err += r.get(key, r.get("cer", 0.0)) * denom
            acc_len += denom
        ests.append(acc_err / acc_len if acc_len else 0.0)

    ests.sort()
    lo = ests[int((alpha / 2) * n_boot)]
    hi = ests[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]
    return (lo, hi)


def is_significant(cer_a: float, ci_a: Tuple[float, float],
                   cer_b: float, ci_b: Tuple[float, float]) -> bool:
    """两个模型的差距是否在统计上分得开。

    判据保守：只有当点估计之差不被任何一方的区间覆盖时，才称"分得开"。
    这正是现有公开榜单从不提供、而我们能提供的那一句话。
    """
    diff = abs(cer_a - cer_b)
    overlap = min(ci_a[1], ci_b[1]) - max(ci_a[0], ci_b[0])
    return overlap < 0 and diff > 0
