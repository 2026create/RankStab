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
from functools import lru_cache, partial
import json
import os
import shutil
import tempfile
from typing import Dict, List, Sequence, Tuple

from .metrics import bootstrap_ci, bootstrap_diff_ci, cluster_rows, corpus_cer, edit_ops
from .protocol import LEVELS, TRAD_BACKEND, normalize
from .segmatch import SEG_MODES, align, split_blocks

BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 20260921

# 参考组（发布口径）：名次、gap、bootstrap 区间都在此组下计算。
# 保留早期默认值，正式实验通过 --ref-seg 显式选择并记录参考组。
REF_SEG = "para_merge"
REF_LEVEL = "P0"

# margin 阈值。取 1.0 不是拍脑袋：margin = gap / (span_a + span_b)，
# 等于 1 就是「领先幅度正好等于协议能把两者各自掀起的极限」——
# >= 1 仅保证已观测参与组内不严格翻转；< 1 不证明存在翻转。
MARGIN_THRESHOLD = 1.0


def base_length(ref_md: str, seg_mode: str) -> int:
    """参考文本在 P0 下的字符数——该样本在该切分方式下的「固定分母」。

    关键：分母取自 P0（最严格档），所有档位共用，
    这样档位间的差异完全来自比对规则，不含文本长度变化这一无关因素。
    """
    return sum(len(normalize(b, "P0")) for b in split_blocks(ref_md, seg_mode))


def _score_one(ref_md: str, hyp_md: str, seg_mode: str, level: str,
               fixed_denom: int | None = None, operations=None) -> Dict[str, float]:
    """单样本在 (切分, 协议) 组合下的错误统计。"""
    ref_blocks = split_blocks(ref_md, seg_mode)
    hyp_blocks = split_blocks(hyp_md, seg_mode)
    calculate_operations = operations or edit_ops

    # 配对决策固定用 P0 归一化后的编辑距离：配对不随档位变化，协议只影响计分，
    # 否则同一对块在不同档位下会被判成不同的配对，产物难以解释。
    def _pair_cost(a: str, b: str) -> float:
        return float(calculate_operations(normalize(a, "P0"), normalize(b, "P0"))["dist"])

    pairs, ref_left, hyp_left = align(ref_blocks, hyp_blocks, _pair_cost)

    dist = sub = dele = ins = 0
    ref_len = 0
    overflow = 0

    for rb, hb in pairs:
        rn, hn = normalize(rb, level), normalize(hb, level)
        ops = calculate_operations(rn, hn)
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
    if not isinstance(entries, list) or not entries:
        raise ValueError("真值索引必须是非空数组：%s" % idx_path)
    seen = set()
    for e in entries:
        if not isinstance(e, dict) or not isinstance(e.get("sample_id"), str):
            raise ValueError("真值索引中的 sample_id 必须是字符串：%s" % idx_path)
        sid = e["sample_id"].strip()
        if not sid or sid != e["sample_id"] or sid in seen:
            raise ValueError("真值索引含空白或重复 sample_id：%r" % e.get("sample_id"))
        if sid in (".", "..") or "/" in sid or "\\" in sid or os.path.isabs(sid):
            raise ValueError("sample_id 不能包含路径片段：%r" % sid)
        seen.add(sid)
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


def pair_protocol_details(scores: List[Dict], verdicts: List[Dict],
                          modes: Sequence[str], levels: Sequence[str]) -> List[Dict]:
    """记录参考组相邻模型对在各协议中的分差和相邻档位变化。"""
    lookup = {(r['seg_mode'], r['level'], r['model']): r['cer_fixed'] for r in scores}
    details = []
    for pair in verdicts:
        a, b = pair['model_a'], pair['model_b']
        ref_delta = pair['cer_b'] - pair['cer_a']
        for mode in modes:
            previous = None
            for level in levels:
                delta = lookup[(mode, level, b)] - lookup[(mode, level, a)]
                sign = (delta > 0) - (delta < 0)
                ref_sign = (ref_delta > 0) - (ref_delta < 0)
                relative = ('参考组平分' if ref_sign == 0 else
                            '平分' if sign == 0 else
                            '同向' if sign == ref_sign else '逆序')
                transition = ('起始' if previous is None else
                              '涉及平分' if previous == 0 or sign == 0 else
                              '换位' if previous != sign else '未换位')
                details.append({'model_a': a, 'model_b': b, 'seg_mode': mode,
                                'level': level, 'difference_b_minus_a': delta,
                                'relative_to_reference': relative,
                                'transition_from_previous_level': transition})
                previous = sign
    return details


def validate_output_path(gt_dir: str, models_out: str, out_dir: str) -> None:
    """Reject output paths that overlap input data, even through symlinks."""
    output_path = os.path.normcase(os.path.realpath(out_dir))
    for input_dir in (gt_dir, models_out):
        input_path = os.path.normcase(os.path.realpath(input_dir))
        try:
            common = os.path.commonpath((output_path, input_path))
        except ValueError:
            continue
        if common in (output_path, input_path):
            raise ValueError("输出目录与输入目录不能重叠：%s / %s" % (out_dir, input_dir))


def _publish_results(out_dir: str, result: Dict, force: bool, make_html_report: bool) -> None:
    """Write all results before swapping the output directory into place."""
    out_dir = os.path.abspath(out_dir)
    parent = os.path.dirname(out_dir)
    os.makedirs(parent, exist_ok=True)
    staging_root = tempfile.mkdtemp(prefix=".rankstab-", dir=parent)
    staged = os.path.join(staging_root, "new")
    backup = os.path.join(staging_root, "previous")
    lock = out_dir + ".rankstab.lock"
    lock_acquired = False
    preserve_backup = False
    try:
        os.mkdir(staged)
        for name, rows in (("scores", result["scores"]), ("rank_matrix", result["ranks"]),
                           ("verdicts", result["verdicts"]), ("stability", result["stability"]),
                           ("protocol_curve", result["curve"]),
                           ("pair_protocol_details", result.get("pair_protocol_details", [])),
                           ("sample_scores", result.get("sample_scores", []))):
            if rows:
                _write_csv(os.path.join(staged, name + ".csv"), rows)
        with open(os.path.join(staged, "meta.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(result["meta"], f, ensure_ascii=False, indent=2)
        if make_html_report:
            from . import make_report
            make_report.build(result, os.path.join(staged, "report.html"))

        try:
            os.mkdir(lock)
            lock_acquired = True
        except FileExistsError as exc:
            raise RuntimeError("输出目录正在被其他评测写入：%s" % out_dir) from exc
        if os.path.islink(out_dir):
            raise ValueError("输出目录不能是符号链接：%s" % out_dir)
        if os.path.exists(out_dir):
            if not os.path.isdir(out_dir):
                raise NotADirectoryError("输出路径不是目录：%s" % out_dir)
            if os.listdir(out_dir) and not force:
                raise FileExistsError("输出目录已非空；已有产物不代表本次成功：%s" % out_dir)
            keep = os.path.join(out_dir, ".gitkeep")
            if os.path.isfile(keep):
                shutil.copy2(keep, os.path.join(staged, ".gitkeep"))
            os.rename(out_dir, backup)
        try:
            os.rename(staged, out_dir)
        except Exception:
            if os.path.exists(backup):
                try:
                    os.rename(backup, out_dir)
                except OSError:
                    preserve_backup = True
            raise
    finally:
        if lock_acquired:
            os.rmdir(lock)
        if not preserve_backup:
            shutil.rmtree(staging_root, ignore_errors=True)
        else:
            print("旧输出恢复失败，原目录保留在：%s" % backup)


def run(gt_dir: str, models_out: str, out_dir: str, n_boot: int = BOOTSTRAP_N,
        ref_seg: str | None = None, ref_level: str | None = None,
        seg_modes: Sequence[str] | None = None, force: bool = False,
        make_html_report: bool = False, model_names: Sequence[str] | None = None,
        bootstrap_seed: int = BOOTSTRAP_SEED,
        bootstrap_cluster_field: str | None = None,
        edit_cell_limit: int = 4_000_000) -> Dict:
    """跑协议空间重算。默认 3 种切分 × 6 档 = 18 组。

    `seg_modes` 限定**本层适用的切分方式**。这不是可有可无的选项，而是分层评测的前提：

      行级 OCR  ：只有 `whole` 适用 → 1 × 6 = 6 组
      端到端解析：3 种都适用       → 3 × 6 = 18 组

    为什么必须限定：`span`（协议能造成的分数抖动）是在所有参与组上算的。
    实测教训——把段落口径一并算进去后，行级 OCR 的 `span` 被撑到 1.5 以上，
    于是 `margin` 全部跌到 0.02 附近，**任何对比都退化为 TIE**，
    判定失去了区分能力。限定之后判定才有意义。

    `ref_seg` / `ref_level` 指定「参考组」——名次、区间与判定都在该组下计算。
    参考组必须落在 `seg_modes` 之内。二者都记入 `meta.json`，不做运行时探测。
    """
    validate_output_path(gt_dir, models_out, out_dir)
    if not isinstance(n_boot, int) or isinstance(n_boot, bool) or n_boot < 1:
        raise ValueError("bootstrap 次数必须是正整数：%r" % (n_boot,))
    if not isinstance(bootstrap_seed, int) or isinstance(bootstrap_seed, bool):
        raise ValueError("bootstrap 随机种子必须是整数：%r" % (bootstrap_seed,))
    if os.path.exists(out_dir) and not os.path.isdir(out_dir):
        raise NotADirectoryError("输出路径不是目录：%s" % out_dir)
    if os.path.isdir(out_dir) and os.listdir(out_dir):
        if not force:
            raise FileExistsError(
                "输出目录非空或不是目录：%s；本次评测未执行，"
                "已有产物不代表本次成功。请换一个空目录，或显式加 --force 覆盖（覆盖会先清空该目录）。"
                % out_dir)
    requested_modes = list(seg_modes) if seg_modes else list(SEG_MODES)
    invalid_modes = [m for m in requested_modes if m not in SEG_MODES]
    if invalid_modes:
        raise ValueError("未知切分方式 %r，可选值为 %s" % (invalid_modes, ", ".join(SEG_MODES)))
    modes = requested_modes
    if len(modes) != len(set(modes)):
        raise ValueError("切分方式不能重复：%r" % modes)
    if not modes:
        raise ValueError("seg_modes 为空或全部非法：%r" % (seg_modes,))
    ref_seg = ref_seg if ref_seg is not None else (REF_SEG if REF_SEG in modes else modes[0])
    ref_lvl = ref_level if ref_level is not None else REF_LEVEL
    if ref_seg not in modes:
        raise ValueError("参考切分方式 %r 不在参与计算的切分方式 %s 中" % (ref_seg, modes))
    if ref_lvl not in LEVELS:
        raise ValueError("未知参考归一化档位 %r，可选值为 %s" % (ref_lvl, ", ".join(LEVELS)))
    if edit_cell_limit < 1:
        raise ValueError('edit_cell_limit must be positive')
    gt = load_gt(gt_dir)
    sids = [e["sample_id"] for e in gt]
    clusters = [str(entry.get(bootstrap_cluster_field, entry['sample_id'])) for entry in gt]
    models = load_predictions(models_out, sids)
    if not models:
        raise RuntimeError("models_out 下没有任何模型目录，见 SPEC.md 第 3 节数据格式。")
    if model_names is not None:
        selected = list(model_names)
        if not selected or len(set(selected)) != len(selected):
            raise ValueError("模型选择不能为空或包含重复项：%r" % selected)
        unknown = [name for name in selected if name not in models]
        if unknown:
            raise ValueError("模型目录不存在：%s" % ", ".join(unknown))
        models = {name: models[name] for name in sorted(selected)}
    missing_predictions = {
        model: [sid for sid in sids
                if not os.path.isfile(os.path.join(models_out, model, sid + ".md"))]
        for model in models
    }

    # 固定分母：逐 (样本, 切分方式) 预先算好
    base = {(e["sample_id"], seg): base_length(e["_ref_md"], seg)
            for e in gt for seg in modes}

    # ---- 逐组打分 ----
    score_rows: List[Dict] = []
    # 逐样本行保留下来：配对 bootstrap 要求两条序列按同一样本、同一顺序对齐。
    per_sample: Dict[Tuple[str, str, str], List[Dict]] = {}
    ci_map: Dict[Tuple[str, str, str], Tuple] = {}
    for seg in modes:
        for model, preds in models.items():
            for lvl in LEVELS:
                per_sample[(seg, lvl, model)] = []
            for e in gt:
                sid = e['sample_id']
                # A bounded cache is shared across the six rules for this one sample.
                # It preserves the existing edit path and S/D/I tie-breaking.
                cached_operations = lru_cache(maxsize=4096)(partial(edit_ops, max_cells=edit_cell_limit))
                for lvl in LEVELS:
                    row = _score_one(e['_ref_md'], preds.get(sid, ''), seg, lvl,
                                     fixed_denom=base[(sid, seg)], operations=cached_operations)
                    row['sample_id'] = sid
                    per_sample[(seg, lvl, model)].append(row)
    for seg in modes:
        for lvl in LEVELS:
            for model, preds in models.items():
                rows = per_sample[(seg, lvl, model)]
                agg = corpus_cer(rows)
                resampling_rows = cluster_rows(rows, clusters) if bootstrap_cluster_field else rows
                ci = bootstrap_ci(resampling_rows, n_boot=n_boot, seed=bootstrap_seed)
                ci_map[(seg, lvl, model)] = ci
                n_ovf = sum(1 for r in rows if r.get("overflow"))
                score_rows.append({
                    "seg_mode": seg, "level": lvl, "model": model,
                    "cer": agg["cer"],
                    "cer_fixed": agg["cer_fixed"],
                    "sub_rate": agg["sub_rate"],
                    "del_rate": agg["del_rate"],
                    "ins_rate": agg["ins_rate"],
                    "ref_len": agg["ref_len"],
                    # 样本不足 2 条时区间不可估计，写空值而不是 0。
                    "ci_lo": "" if ci[0] is None else ci[0],
                    "ci_hi": "" if ci[1] is None else ci[1],
                    # 兼容旧输出字段；超限已抛出异常，成功组恒为零。
                    "overflow_samples": n_ovf,
                })

    # ---- 名次矩阵（按主口径 cer_fixed 排名） ----
    groups: Dict[Tuple[str, str], List[Dict]] = {}
    for r in score_rows:
        groups.setdefault((r["seg_mode"], r["level"]), []).append(r)

    rank_rows: List[Dict] = []
    for (seg, lvl), items in sorted(groups.items()):
        ordered = sorted(items, key=lambda x: (x["cer_fixed"], x["model"]))
        rank = 0
        for i, it in enumerate(ordered, 1):
            if i == 1 or it["cer_fixed"] != ordered[i - 2]["cer_fixed"]:
                rank = i
            rank_rows.append({
                "seg_mode": seg, "level": lvl, "model": it["model"],
                "rank": rank, "cer_fixed": it["cer_fixed"], "cer": it["cer"],
            })

    # ---- 名次可信度判定（SPEC v1.1 第 4.5 节）----
    # 这一步回答一个此前没人回答的问题：**「稳定」到底以什么为标准**。
    # 绝对的名次位移没有意义 —— 位移 1 位，若两者差 5 分是稳的，差 0.01 分是脆的。
    # 因此所有判据都相对「名次间距」定义，而不是相对名次的绝对变化。
    ref_key = (ref_seg, ref_lvl)
    ref_group_name = "%s/%s" % (ref_seg, ref_lvl)

    ref_score: Dict[str, float] = {}
    ref_ci: Dict[str, Tuple] = {}
    span: Dict[str, float] = {}
    for m in models:
        rs = [r for r in score_rows if r["model"] == m]
        ref_row = next(r for r in rs if (r["seg_mode"], r["level"]) == ref_key)
        ref_score[m] = ref_row["cer_fixed"]
        # 直接用 bootstrap 的原始返回值（float 或 None）；CSV 里的空串只用于展示。
        ref_ci[m] = ci_map[(ref_seg, ref_lvl, m)]
        vals = [r["cer_fixed"] for r in rs]
        # span：协议能把该模型的分数掀起的最大幅度。
        # 注意这是参与组离散采样下的**下界** —— 可能存在未采到的协议让抖动更大。
        span[m] = max(vals) - min(vals)

    ref_order = sorted(models.keys(), key=lambda m: (ref_score[m], m))

    def _margin(a: str, b: str) -> float:
        """margin = gap / (span_a + span_b)。

        >= 1：参考组包含于参与组时，仅保证已观测协议集合内不严格翻转。
             span 可能随未采样协议而增大，不能外推此保证。
        < 1 ：未满足不翻转的充分条件，不证明存在可翻转协议。
        """
        s = span[a] + span[b]
        if s <= 0:
            return 0.0 if ref_score[a] == ref_score[b] else float("inf")
        return abs(ref_score[a] - ref_score[b]) / s

    def _flips(a: str, b: str) -> int:
        """参与组里有多少组，两者的先后顺序与参考组相反。这是**实证证据**（下界）。"""
        direction = ref_score[a] - ref_score[b]
        if direction == 0:
            return 0
        n = 0
        for _k, items in groups.items():
            d = {it["model"]: it["cer_fixed"] for it in items}
            if (direction < 0 and d[a] > d[b]) or (direction > 0 and d[a] < d[b]):
                n += 1
        return n

    def _overlap(a: str, b: str):
        """参考组下两者的 bootstrap 95% 区间是否重叠。

        返回 True / False；区间不可估计（有效样本 < 2）时返回 None，
        此时不能把「无法估计」解释成「重叠」，因此也不判 TIE。
        """
        la, ha = ref_ci[a]
        lb, hb = ref_ci[b]
        if la is None or ha is None or lb is None or hb is None:
            return None
        return not (ha < lb - 1e-12 or hb < la - 1e-12)

    def _verdict(a: str, b: str) -> str:
        # 判定优先级：先确认「有没有赢」，再谈「稳不稳」。
        if _overlap(a, b) is True:
            return "TIE"          # 同一套规则下就分不出胜负，名次本身无意义
        if _flips(a, b) > 0:
            return "FRAGILE"      # 参与组里已经翻过 —— 实证下界
        if _margin(a, b) < MARGIN_THRESHOLD:
            return "FRAGILE"      # 未达到已观测集合内不翻转的充分条件
        return "STABLE"           # 仅在已观测参与组内保证不严格翻转

    def _fmt_margin(x: float):
        return "inf" if x == float("inf") else round(x, 6)

    pair_rows: List[Dict] = []
    for i in range(len(ref_order) - 1):
        a, b = ref_order[i], ref_order[i + 1]
        ov = _overlap(a, b)
        # 配对差值区间：仅作诊断，不参与 verdict。
        reference_rows_a = per_sample[(ref_seg, ref_lvl, a)]
        reference_rows_b = per_sample[(ref_seg, ref_lvl, b)]
        if bootstrap_cluster_field:
            reference_rows_a = cluster_rows(reference_rows_a, clusters)
            reference_rows_b = cluster_rows(reference_rows_b, clusters)
        d_lo, d_hi = bootstrap_diff_ci(
            reference_rows_a, reference_rows_b,
            n_boot=n_boot, seed=bootstrap_seed,
        )
        differences = []
        for (seg, lvl), items in groups.items():
            values = {item['model']: item['cer_fixed'] for item in items}
            differences.append((values[b] - values[a], '%s/%s' % (seg, lvl)))
        minimum_difference, minimum_group = min(differences, key=lambda item: item[0])
        verdict = _verdict(a, b)
        reason = 'sufficient_margin'
        if ov is True:
            reason = 'reference_ci_overlap'
        elif _flips(a, b) > 0:
            reason = 'observed_reversal'
        elif _margin(a, b) < MARGIN_THRESHOLD:
            reason = 'insufficient_margin'
        pair_rows.append({
            "rank": i + 1,
            "model_a": a, "model_b": b,
            "cer_a": ref_score[a], "cer_b": ref_score[b],
            "gap": round(ref_score[b] - ref_score[a], 6),
            "span_a": round(span[a], 6), "span_b": round(span[b], 6),
            "margin": _fmt_margin(_margin(a, b)),
            "flipped_groups": _flips(a, b),
            "ci_overlap": "" if ov is None else int(ov),
            "paired_diff_lo": "" if d_lo is None else round(d_lo, 6),
            "paired_diff_hi": "" if d_hi is None else round(d_hi, 6),
            "verdict": verdict,
            "ref_group": ref_group_name,
            "verdict_reason": reason,
            "min_pair_difference": minimum_difference,
            "min_difference_group": minimum_group,
            "max_pair_difference": max(item[0] for item in differences),
        })

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
            # 区间不可估计时写空值，避免把 (0, 0) 当成真实估计。
            "ref_ci_lo": "" if ref_ci[model][0] is None else round(ref_ci[model][0], 6),
            "ref_ci_hi": "" if ref_ci[model][1] is None else round(ref_ci[model][1], 6),
            "gap_nearest": round(abs(ref_score[nb] - ref_score[model]), 6) if nb else "",
            "flipped_groups_nearest": _flips(model, nb) if nb else 0,
            "margin": _fmt_margin(_margin(model, nb)) if nb else "",
            "verdict": _verdict(model, nb) if nb else "N/A",
        })
    stab_rows.sort(key=lambda x: (-x["rank_shift"], x["model"]))

    # ---- 宽容度曲线：模型 x 档位（**参考组切分**下的固定分母口径） ----
    # 多个切分参与时不能跨切分求均值：切分会成倍改变 CER（50 页实测同一模型
    # 整页 0.103 / 按行 1.032 / 合并 0.214），均值是没有对应实物的数。
    # 单一参与切分时（如主实验早期只跑 whole），本式与「均值」逐位相同，
    # 历史产物字节不变。
    curve_rows: List[Dict] = []
    for model in models:
        row: Dict = {"model": model}
        for lvl in LEVELS:
            vs = [r["cer_fixed"] for r in score_rows
                  if r["model"] == model and r["level"] == lvl
                  and r["seg_mode"] == ref_seg]
            row[lvl] = round(sum(vs) / len(vs), 6) if vs else 0.0
        row["drop_P0_to_P5"] = round(row["P0"] - row["P5"], 6)
        curve_rows.append(row)

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
        "missing_predictions": {m: ids for m, ids in missing_predictions.items() if ids},
        "seg_modes": modes,
        "seg_modes_all": list(SEG_MODES),
        "levels": LEVELS,
        "n_groups": len(modes) * len(LEVELS),
        "primary_metric": "cer_fixed",
        "bootstrap_n": n_boot,
        "bootstrap_seed": bootstrap_seed,
        "bootstrap_unit": 'cluster' if bootstrap_cluster_field else 'sample',
        "bootstrap_cluster_field": bootstrap_cluster_field,
        "bootstrap_n_units": len(set(clusters)) if bootstrap_cluster_field else len(gt),
        "trad_backend": TRAD_BACKEND,
        "monotonicity_violations": violations,
        # 已知边界：固定分母消除分母变化，但归一化（尤其是 P5 的繁简映射与
        # CJK 间空格删除）仍会改变字符等价关系与最优对齐，因此不保证单调。
        "monotonicity_note": (
            "固定分母只消除分母变化；P5 的繁简 1:1 映射与按上下文删除 CJK "
            "间空格会改变对齐，可能使个别样本编辑距离上升。可复现最小反例见 "
            "tests/test_all.py 的 A29。"
        ),
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
        "bootstrap_ci": {
            "level": 0.95,
            "min_valid_samples": 2,
            "not_estimable_groups": sum(1 for r in score_rows if r["ci_lo"] == ""),
            "policy": "有效样本少于 2 条时区间不可估计并写空值，不据此判 TIE",
        },
        "paired_diff": {
            "role": "diagnostic only",
            "difference": "cer_fixed(model_b) - cer_fixed(model_a)",
            "resampling": "cluster-level paired bootstrap" if bootstrap_cluster_field else "sample-level paired bootstrap",
            "seed": bootstrap_seed,
            "note": "仅用于诊断；verdict 仍按参考组各自区间是否重叠判定",
        },
        "long_text_fallback": {
            "trigger_threshold_product": edit_cell_limit,
            "groups_with_overflow": ovf_groups,
            "samples_with_overflow_total": ovf_total,
            "policy": "fail_closed",
            "note": "编辑距离乘积超限即抛出异常，本次不输出评分产物；兼容统计字段在成功运行时为零。",
        },
    }
    if model_names is not None:
        meta["model_selection"] = sorted(models.keys())
    pair_details = pair_protocol_details(score_rows, pair_rows, modes, LEVELS)
    sample_scores = []
    for (seg, lvl, model), rows in per_sample.items():
        for row in rows:
            sample_scores.append(dict(seg_mode=seg, level=lvl, model=model, **row))
    result = {"scores": score_rows, "ranks": rank_rows,
              "stability": stab_rows, "curve": curve_rows,
              "verdicts": pair_rows, "pair_protocol_details": pair_details,
              "sample_scores": sample_scores, "meta": meta}
    _publish_results(out_dir, result, force, make_html_report)
    return result
