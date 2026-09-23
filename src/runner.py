# -*- coding: utf-8 -*-
"""18 组重算主流程：3 种切分方式 x 6 档归一化协议。

输出（全部写入 out/）：
    scores.csv          每个 (模型, 切分, 协议) 的语料级 CER 及 S/D/I（双口径）
    rank_matrix.csv     18 组下每个模型的名次
    stability.csv       每个模型的名次区间 / 位移 / 标准差 + 名次可信度摘要
    verdicts.csv        ★ 相邻名次对的可信度判定（STABLE / FRAGILE / TIE）
    protocol_curve.csv  模型 x 档位 的宽容度曲线（固定分母口径）
    meta.json           运行元信息（样本数、模型数、种子、繁简后端、单调性自检）

**核心主张**：名次相对评分细则的稳定度，是本次实验的产出物。
"""

from __future__ import annotations

import csv
import json
import os
from typing import Dict, List, Sequence, Tuple

from .metrics import bootstrap_ci, corpus_cer, edit_ops
from .protocol import LEVELS, TRAD_BACKEND, normalize
from .segmatch import SEG_MODES, align, split_blocks

BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 20260921

# 参考组（发布口径）：名次、gap、bootstrap 区间都在此组下计算。
# para_merge 对应上游推荐的 quick_match；P0 是最严档，作为对外公布的基准口径。
REF_SEG = "para_merge"
REF_LEVEL = "P0"

# margin 阈值。取 1.0 不是拍脑袋：margin = gap / (span_a + span_b)，
# 等于 1 就是「领先幅度正好等于协议能把两者各自掀起的极限」——
# 超过 1 数学上不可能翻转，低于 1 存在某个协议能翻动。阈值来自定义本身。
MARGIN_THRESHOLD = 1.0


def base_length(ref_md: str, seg_mode: str) -> int:
    """参考文本在 P0 下的字符数——该样本在该切分方式下的「固定分母」。

    关键：分母取自 P0（最严格档），所有档位共用，
    这样档位间的差异完全来自比对规则，不含文本长度变化这一无关因素。
    """
    return sum(len(normalize(b, "P0")) for b in split_blocks(ref_md, seg_mode))


def _score_one(ref_md: str, hyp_md: str, seg_mode: str, level: str,
               fixed_denom: int | None = None) -> Dict[str, float]:
    """单样本在 (切分, 协议) 组合下的错误统计。"""
    ref_blocks = split_blocks(ref_md, seg_mode)
    hyp_blocks = split_blocks(hyp_md, seg_mode)
    pairs, ref_left, hyp_left = align(ref_blocks, hyp_blocks)

    dist = sub = dele = ins = 0
    ref_len = 0
    overflow = 0

    for rb, hb in pairs:
        rn, hn = normalize(rb, level), normalize(hb, level)
        ops = edit_ops(rn, hn)
        dist += ops["dist"]; sub += ops["S"]; dele += ops["D"]; ins += ops["I"]
        ref_len += len(rn)
        if ops.get("overflow"):
            # 该块的"编辑距离"实为长度差。必须让它可见：一旦被当作真实距离
            # 参与汇总，整个语料级指标就失真，而且不会报错。
            overflow += 1

    # 未匹配的 ref 块：整块漏字
    for b in ref_left:
        rn = normalize(b, level)
        dist += len(rn); dele += len(rn); ref_len += len(rn)
    # 未匹配的 hyp 块：整块多字
    for b in hyp_left:
        hn = normalize(b, level)
        dist += len(hn); ins += len(hn)

    d_std = max(1, ref_len)
    d_fx = max(1, int(fixed_denom)) if fixed_denom else d_std
    return {
        "cer": dist / d_std,
        "cer_fixed": dist / d_fx,
        "sub_rate": sub / d_fx,
        "del_rate": dele / d_fx,
        "ins_rate": ins / d_fx,
        "ref_len": ref_len,
        "fixed_denom": d_fx,
        "overflow": overflow,
    }


def load_gt(gt_dir: str) -> List[Dict]:
    """读取真值索引。期望 gt_dir/index.json 存在，格式见 SPEC.md 第 3 节。"""
    idx_path = os.path.join(gt_dir, "index.json")
    if not os.path.exists(idx_path):
        raise FileNotFoundError(
            "缺少 %s。真值索引格式见 SPEC.md 第 3 节。" % idx_path
        )
    with open(idx_path, "r", encoding="utf-8") as f:
        entries = json.load(f)
    for e in entries:
        md = os.path.join(gt_dir, e["sample_id"] + ".md")
        if not os.path.exists(md):
            raise FileNotFoundError("样本 %s 缺少真值文件 %s" % (e["sample_id"], md))
        with open(md, "r", encoding="utf-8") as f:
            e["_ref_md"] = f.read()
    return entries


def load_predictions(models_out: str, sample_ids: Sequence[str]) -> Dict[str, Dict[str, str]]:
    """读取各模型输出：models_out/<model>/<sample_id>.md

    缺失的输出记为全篇漏字（空串），照实计分——不做任何"跳过"处理，
    因为"某条样本没跑出来"本身就是评测结论的一部分。
    """
    models: Dict[str, Dict[str, str]] = {}
    if not os.path.isdir(models_out):
        return models
    for name in sorted(os.listdir(models_out)):
        d = os.path.join(models_out, name)
        if not os.path.isdir(d):
            continue
        preds: Dict[str, str] = {}
        for sid in sample_ids:
            p = os.path.join(d, sid + ".md")
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    preds[sid] = f.read()
            else:
                preds[sid] = ""
        models[name] = preds
    return models


def _write_csv(path: str, rows: List[Dict]) -> None:
    # newline="" + lineterminator="\n"：两处都必须显式指定，缺一不可。
    #   newline=""        —— 关闭 Python 的换行翻译，否则 Windows 上 \n 会被写成 \r\n
    #   lineterminator="\n" —— csv 模块**默认**用 \r\n，与平台无关，必须手动覆盖
    # 目标：同一份代码在 Windows / Linux / macOS 上产出**逐字节相同**的文件。
    # 否则「可复现」只在同一平台上成立，跨平台会因行尾差异对不上。
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def run(gt_dir: str, models_out: str, out_dir: str, n_boot: int = BOOTSTRAP_N) -> Dict:
    gt = load_gt(gt_dir)
    sids = [e["sample_id"] for e in gt]
    models = load_predictions(models_out, sids)
    if not models:
        raise RuntimeError("models_out 下没有任何模型目录，见 SPEC.md 第 3 节数据格式。")

    os.makedirs(out_dir, exist_ok=True)

    # 固定分母：逐 (样本, 切分方式) 预先算好
    base = {(e["sample_id"], seg): base_length(e["_ref_md"], seg)
            for e in gt for seg in SEG_MODES}

    # ---- 逐组打分 ----
    score_rows: List[Dict] = []
    for seg in SEG_MODES:
        for lvl in LEVELS:
            for model, preds in models.items():
                rows: List[Dict] = []
                for e in gt:
                    sid = e["sample_id"]
                    r = _score_one(e["_ref_md"], preds.get(sid, ""), seg, lvl,
                                   fixed_denom=base[(sid, seg)])
                    r["sample_id"] = sid
                    rows.append(r)
                agg = corpus_cer(rows)
                ci = bootstrap_ci(rows, n_boot=n_boot)
                n_ovf = sum(1 for r in rows if r.get("overflow"))
                score_rows.append({
                    "seg_mode": seg, "level": lvl, "model": model,
                    "cer": round(agg["cer"], 6),
                    "cer_fixed": round(agg["cer_fixed"], 6),
                    "sub_rate": round(agg["sub_rate"], 6),
                    "del_rate": round(agg["del_rate"], 6),
                    "ins_rate": round(agg["ins_rate"], 6),
                    "ref_len": agg["ref_len"],
                    "ci_lo": round(ci[0], 6),
                    "ci_hi": round(ci[1], 6),
                    # 该组有多少条样本因长文本保护退化为长度差。
                    # 只要 > 0，本行的 cer/cer_fixed 就不是纯编辑距离，不得当作真实 CER 引用。
                    "overflow_samples": n_ovf,
                })

    _write_csv(os.path.join(out_dir, "scores.csv"), score_rows)

    # ---- 名次矩阵（按主口径 cer_fixed 排名） ----
    groups: Dict[Tuple[str, str], List[Dict]] = {}
    for r in score_rows:
        groups.setdefault((r["seg_mode"], r["level"]), []).append(r)

    rank_rows: List[Dict] = []
    for (seg, lvl), items in sorted(groups.items()):
        ordered = sorted(items, key=lambda x: (x["cer_fixed"], x["model"]))
        for i, it in enumerate(ordered, 1):
            rank_rows.append({
                "seg_mode": seg, "level": lvl, "model": it["model"],
                "rank": i, "cer_fixed": it["cer_fixed"], "cer": it["cer"],
            })
    _write_csv(os.path.join(out_dir, "rank_matrix.csv"), rank_rows)

    # ---- 名次可信度判定（SPEC v1.1 第 4.5 节）----
    # 这一步回答一个此前没人回答的问题：**「稳定」到底以什么为标准**。
    # 绝对的名次位移没有意义 —— 位移 1 位，若两者差 5 分是稳的，差 0.01 分是脆的。
    # 因此所有判据都相对「名次间距」定义，而不是相对名次的绝对变化。
    ref_seg = REF_SEG if REF_SEG in SEG_MODES else SEG_MODES[0]
    ref_lvl = REF_LEVEL if REF_LEVEL in LEVELS else LEVELS[0]
    ref_key = (ref_seg, ref_lvl)
    ref_group_name = "%s/%s" % (ref_seg, ref_lvl)

    ref_score: Dict[str, float] = {}
    ref_ci: Dict[str, Tuple[float, float]] = {}
    span: Dict[str, float] = {}
    for m in models:
        rs = [r for r in score_rows if r["model"] == m]
        ref_row = next(r for r in rs if (r["seg_mode"], r["level"]) == ref_key)
        ref_score[m] = ref_row["cer_fixed"]
        ref_ci[m] = (ref_row["ci_lo"], ref_row["ci_hi"])
        vals = [r["cer_fixed"] for r in rs]
        # span：协议能把该模型的分数掀起的最大幅度。
        # 注意这是 18 组离散采样下的**下界** —— 可能存在未采到的协议让抖动更大。
        span[m] = max(vals) - min(vals)

    ref_order = sorted(models.keys(), key=lambda m: (ref_score[m], m))

    def _margin(a: str, b: str) -> float:
        """margin = gap / (span_a + span_b)。

        >= 1：即使协议把两者各自推到最有利/最不利的极端，也追不上它们的差距，
             数学上不可能翻转 —— 这是**保证**，不依赖采样是否完备。
        < 1 ：存在某个协议能让它翻转 —— 这是**可能**，是否真翻要看实证。
        """
        s = span[a] + span[b]
        if s <= 0:
            return float("inf")
        return abs(ref_score[a] - ref_score[b]) / s

    def _flips(a: str, b: str) -> int:
        """18 组里有多少组，两者的先后顺序与参考组相反。这是**实证证据**（下界）。"""
        n = 0
        for _k, items in groups.items():
            d = {it["model"]: it["cer_fixed"] for it in items}
            if d[b] < d[a] - 1e-12:
                n += 1
        return n

    def _overlap(a: str, b: str) -> bool:
        """参考组下两者的 bootstrap 95% 区间是否重叠 —— 重叠意味着连「谁赢」都没定。"""
        la, ha = ref_ci[a]
        lb, hb = ref_ci[b]
        return not (ha < lb - 1e-12 or hb < la - 1e-12)

    def _verdict(a: str, b: str) -> str:
        # 判定优先级：先确认「有没有赢」，再谈「稳不稳」。
        if _overlap(a, b):
            return "TIE"          # 同一套规则下就分不出胜负，名次本身无意义
        if _flips(a, b) > 0:
            return "FRAGILE"      # 18 组里已经翻过 —— 实证下界
        if _margin(a, b) < MARGIN_THRESHOLD:
            return "FRAGILE"      # 界内可翻，但本次 18 组未观测到
        return "STABLE"           # 界内不可翻 —— 已定义协议范围内站得住

    def _fmt_margin(x: float):
        return "inf" if x == float("inf") else round(x, 6)

    pair_rows: List[Dict] = []
    for i in range(len(ref_order) - 1):
        a, b = ref_order[i], ref_order[i + 1]
        pair_rows.append({
            "rank": i + 1,
            "model_a": a, "model_b": b,
            "cer_a": ref_score[a], "cer_b": ref_score[b],
            "gap": round(ref_score[b] - ref_score[a], 6),
            "span_a": round(span[a], 6), "span_b": round(span[b], 6),
            "margin": _fmt_margin(_margin(a, b)),
            "flipped_groups": _flips(a, b),
            "ci_overlap": int(_overlap(a, b)),
            "verdict": _verdict(a, b),
            "ref_group": ref_group_name,
        })
    if pair_rows:
        _write_csv(os.path.join(out_dir, "verdicts.csv"), pair_rows)

    # ---- 稳定度（含每个模型相对最近邻的可信度摘要）----
    pos = {m: i for i, m in enumerate(ref_order)}
    stab_rows: List[Dict] = []
    for model in models:
        ranks = [r["rank"] for r in rank_rows if r["model"] == model]
        vals = [r["cer_fixed"] for r in score_rows if r["model"] == model]
        cers = [r["cer"] for r in score_rows if r["model"] == model]
        mean_rank = sum(ranks) / len(ranks)
        std_rank = (sum((x - mean_rank) ** 2 for x in ranks) / len(ranks)) ** 0.5

        i = pos[model]
        cands: List[str] = []
        if i > 0:
            cands.append(ref_order[i - 1])
        if i < len(ref_order) - 1:
            cands.append(ref_order[i + 1])
        nb = min(cands, key=lambda m: abs(ref_score[m] - ref_score[model])) if cands else None

        stab_rows.append({
            "model": model,
            "n_groups": len(ranks),
            "rank_min": min(ranks), "rank_max": max(ranks),
            "rank_shift": max(ranks) - min(ranks),
            "rank_mean": round(mean_rank, 3),
            "rank_std": round(std_rank, 3),
            "cer_fixed_min": round(min(vals), 6),
            "cer_fixed_max": round(max(vals), 6),
            "cer_fixed_spread": round(max(vals) - min(vals), 6),
            "cer_min": round(min(cers), 6),
            "cer_max": round(max(cers), 6),
            "ref_cer_fixed": round(ref_score[model], 6),
            "ref_ci_lo": round(ref_ci[model][0], 6),
            "ref_ci_hi": round(ref_ci[model][1], 6),
            "gap_nearest": round(abs(ref_score[nb] - ref_score[model]), 6) if nb else "",
            "margin": _fmt_margin(_margin(model, nb)) if nb else "",
            "verdict": _verdict(model, nb) if nb else "N/A",
        })
    stab_rows.sort(key=lambda x: (-x["rank_shift"], x["model"]))
    _write_csv(os.path.join(out_dir, "stability.csv"), stab_rows)

    # ---- 宽容度曲线：模型 x 档位（跨切分方式取均值） ----
    curve_rows: List[Dict] = []
    for model in models:
        row: Dict = {"model": model}
        for lvl in LEVELS:
            vs = [r["cer_fixed"] for r in score_rows
                  if r["model"] == model and r["level"] == lvl]
            row[lvl] = round(sum(vs) / len(vs), 6)
        row["drop_P0_to_P5"] = round(row["P0"] - row["P5"], 6)
        curve_rows.append(row)
    _write_csv(os.path.join(out_dir, "protocol_curve.csv"), curve_rows)

    # ---- 单调性自检（协议实现正确性的硬约束，用固定分母口径） ----
    violations: List[Dict] = []
    keys = sorted({(r["seg_mode"], r["model"]) for r in score_rows})
    for seg, model in keys:
        series = [r for r in score_rows if r["seg_mode"] == seg and r["model"] == model]
        series.sort(key=lambda x: LEVELS.index(x["level"]))
        for a, b in zip(series, series[1:]):
            if b["cer_fixed"] > a["cer_fixed"] + 1e-12:
                violations.append({
                    "seg_mode": seg, "model": model,
                    "from": a["level"], "to": b["level"],
                    "cer_fixed_from": a["cer_fixed"], "cer_fixed_to": b["cer_fixed"],
                })

    # 长文本保护触发统计。这不是元信息，而是**结论有效性的前提**：
    # 静默降级曾让 18 组里三分之一的样本把长度差当成编辑距离报出去。
    ovf_groups = sum(1 for r in score_rows if r["overflow_samples"] > 0)
    ovf_total = sum(r["overflow_samples"] for r in score_rows)

    meta = {
        "n_samples": len(gt),
        "n_models": len(models),
        "sample_ids": sids,
        "models": sorted(models.keys()),
        "seg_modes": SEG_MODES,
        "levels": LEVELS,
        "n_groups": len(SEG_MODES) * len(LEVELS),
        "primary_metric": "cer_fixed",
        "bootstrap_n": n_boot,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "trad_backend": TRAD_BACKEND,
        "monotonicity_violations": violations,
        "segmode_impl": "reconstructed (pending upstream source alignment at G0)",
        "data_sources": sorted({e.get("source", "unknown") for e in gt}),
        # 名次可信度判定的全部自由度都登记在此，复现时可逐项核对
        "ref_group": {"seg_mode": ref_seg, "level": ref_lvl},
        "verdict_rule": {
            "margin_threshold": MARGIN_THRESHOLD,
            "margin_formula": "gap / (span_a + span_b)",
            "ci_level": 0.95,
            "precedence": ["TIE", "FRAGILE(observed flip)",
                           "FRAGILE(margin<1)", "STABLE"],
            "span_is_lower_bound": True,
        },
        "long_text_fallback": {
            "trigger_threshold_product": 4_000_000,
            "groups_with_overflow": ovf_groups,
            "samples_with_overflow_total": ovf_total,
            "note": ("overflow_samples>0 的组，其 cer/cer_fixed 为长度差估算而非"
                     "编辑距离，不得作为真实 CER 引用"),
        },
    }
    # newline="\n"：见 _write_csv 的说明。meta.json 是复现入口，必须跨平台一致。
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return {"scores": score_rows, "ranks": rank_rows,
            "stability": stab_rows, "curve": curve_rows,
            "verdicts": pair_rows, "meta": meta}
