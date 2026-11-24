# -*- coding: utf-8 -*-
"""一键入口。

    python run.py --demo        生成合成探针集并跑完整协议空间（3 切分 × 6 档 = 18 组）
    python run.py               用 data/gt 与 models_out 下的数据跑
    python run.py --seg-modes whole --gt ... --models ... --out ...
                                行级层（主实验结果）的复现方式：只跑 6 档，共 6 组
    python run.py --help

产出全部写入 out/，见 README。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from src import make_demo, runner  # noqa: E402


def _print_summary(res, out_dir: str) -> None:
    meta = res["meta"]
    print()
    print("=" * 74)
    print("样本 %d 条 | 模型 %d 个 | 组合 %d 组 | 主口径 %s | 繁简后端 %s"
          % (meta["n_samples"], meta["n_models"], meta["n_groups"],
             meta["primary_metric"], meta["trad_backend"]))
    print("数据来源: %s" % ", ".join(meta["data_sources"]))
    if meta.get("missing_predictions"):
        print("!! 预测文件缺失，按空预测计分；样本清单见 meta.json：")
        for model, sample_ids in sorted(meta["missing_predictions"].items()):
            print("   %s: %s" % (model, ", ".join(sample_ids)))
    segs = "/".join(meta["seg_modes"])
    print("参与计算的切分: %s（共 %d 组）" % (segs, meta["n_groups"]))
    if meta["n_groups"] >= 18:
        print("提示: 当前参与 3 种切分 × 6 档；分段实现尚未与上游源码对齐。")
    elif meta["n_groups"] == 6:
        print("提示: 当前参与 1 种切分 × 6 档，结论仅适用于本次数据及参与组。")
    if meta["monotonicity_violations"]:
        print("!! 单调性违规 %d 处（归一化不保证 CER 单调，见 SPEC 第 4.4 节）"
              % len(meta["monotonicity_violations"]))
    else:
        print("单调性自检: 本次参与组内无违规（固定分母口径）")

    print("=" * 74)

    print("\n【名次稳定度】按位移排序 —— 这是本项目的核心产出")
    print("%-20s %6s %6s %7s %8s %10s" % ("模型", "最低名", "最高名", "位移", "名次σ", "CER跨度"))
    for r in res["stability"]:
        print("%-20s %6d %6d %7d %8.2f %10.4f"
              % (r["model"], r["rank_min"], r["rank_max"],
                 r["rank_shift"], r["rank_std"], r["cer_fixed_spread"]))

    print("\n【宽容度曲线】固定分母口径，P0 最严 -> P5 最宽")
    print("%-20s %s %10s" % ("模型", "  ".join("%8s" % l for l in meta["levels"]), "P0->P5降幅"))
    for r in res["curve"]:
        print("%-20s %s %10.4f"
              % (r["model"], "  ".join("%8.4f" % r[l] for l in meta["levels"]),
                 r["drop_P0_to_P5"]))

    verdicts = res.get("verdicts", [])
    if verdicts:
        print("\n【名次可信度判定】相邻名次对 —— 这是本项目的结论形态")
        print("%-7s %-26s %10s %9s %7s %8s %9s  %s"
              % ("名次", "名次对", "gap", "margin", "翻转组", "CI重叠", "判定",
                 "配对差值区间(诊断)"))
        for v in verdicts:
            ov = ("不可估计" if v["ci_overlap"] == ""
                  else ("重叠" if v["ci_overlap"] else "不重叠"))
            pd = ("—" if v["paired_diff_lo"] == ""
                  else "[%+.4f, %+.4f]" % (v["paired_diff_lo"], v["paired_diff_hi"]))
            print("%-7s %-26s %10.6f %9s %7d %8s %9s  %s"
                  % ("%d vs %d" % (v["rank"], v["rank"] + 1),
                     "%s / %s" % (v["model_a"], v["model_b"]),
                     v["gap"], v["margin"], v["flipped_groups"],
                     ov, v["verdict"], pd))
        n_stable = sum(1 for v in verdicts if v["verdict"] == "STABLE")
        print("  -> %d/%d 个相邻名次对站得住（STABLE）；判据见 SPEC.md 第 4.5 节"
              % (n_stable, len(verdicts)))
        n_flip = sum(v["flipped_groups"] for v in verdicts)
        if n_flip == 0:
            print("  -> 本次参与组内未观测到翻转：FRAGILE 的含义是「未满足不翻转的"
                  "充分条件」，不等于「已经翻了」")
        else:
            print("  -> 共 %d 个模型对与协议组的组合相对参考组逆序" % n_flip)

    # 注意打印的是实际生效的 --out 目录，不是写死的默认值 ——
    # 自定义 --out 时若仍显示默认目录，会让人跑到错的地方找产物。
    print("\n输出目录: %s" % os.path.abspath(out_dir))
    print("  scores.csv / rank_matrix.csv / stability.csv / verdicts.csv"
          " / protocol_curve.csv / pair_protocol_details.csv / meta.json / report.html")


def main() -> int:
    ap = argparse.ArgumentParser(description="文档解析评测：归一化协议敏感性实验")
    ap.add_argument("--demo", action="store_true",
                    help="先生成合成探针集（打通流程用，无需真实数据）")
    ap.add_argument("--gt", default=os.path.join(HERE, "data", "gt"))
    ap.add_argument("--models", default=os.path.join(HERE, "models_out"))
    ap.add_argument("--select-models", default=None,
                    help="仅评测指定模型目录，名称用逗号分隔；默认使用全部模型。")
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("--boot", type=int, default=runner.BOOTSTRAP_N)
    ap.add_argument("--seed", type=int, default=runner.BOOTSTRAP_SEED,
                    help="bootstrap 随机种子，写入 meta.json。")
    ap.add_argument('--cluster-field', default=None,
                    help='按真值索引中的指定字段进行文档聚类重采样，缺失时使用样本编号。')
    ap.add_argument('--max-edit-cells', type=int, default=4_000_000,
                    help='精确编辑距离矩阵的最大字符长度乘积，超限报错。')
    ap.add_argument("--ref-seg", default=None,
                    help="参考组切分方式（判定基准）。分段实现尚未与上游源码对齐。"
                         "默认选择参与组中的参考切分。")
    ap.add_argument("--ref-level", default=None,
                    help="参考组归一化档位。默认取 runner.REF_LEVEL（P0）。")
    ap.add_argument("--seg-modes", default=None,
                    help="限定本层适用的切分方式，逗号分隔。默认全部三种。"
                         "限定会影响 span 的计算范围，进而影响判定 —— 见 runner.run。")
    ap.add_argument("--force", action="store_true",
                    help="输出目录非空时先清空再写入。默认拒绝覆盖，避免新旧产物混在一起被误读。")
    args = ap.parse_args()

    runner.validate_output_path(args.gt, args.models, args.out)
    if args.demo:
        info = make_demo.generate(args.gt, args.models)
        print("已生成合成数据:", json.dumps(info, ensure_ascii=False))
        print("注意：合成数据 source=generated，不可用于声称真实工具的水平。")

    seg_modes = ([s.strip() for s in args.seg_modes.split(",") if s.strip()]
                 if args.seg_modes else None)
    model_names = ([s.strip() for s in args.select_models.split(",") if s.strip()]
                   if args.select_models is not None else None)
    res = runner.run(args.gt, args.models, args.out, n_boot=args.boot,
                     ref_seg=args.ref_seg, ref_level=args.ref_level,
                     seg_modes=seg_modes, force=args.force, make_html_report=True,
                     model_names=model_names, bootstrap_seed=args.seed,
                     bootstrap_cluster_field=args.cluster_field,
                     edit_cell_limit=args.max_edit_cells)
    _print_summary(res, args.out)

    report = os.path.join(args.out, "report.html")
    print("可视化报告: %s" % report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
