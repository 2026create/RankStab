# -*- coding: utf-8 -*-
"""一键入口。

    python run.py --demo        生成合成探针集并跑完 18 组（无需任何真实数据）
    python run.py               用 data/gt 与 models_out 下的数据跑
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

from src import make_demo, make_report, runner  # noqa: E402


def _print_summary(res, out_dir: str) -> None:
    meta = res["meta"]
    print()
    print("=" * 74)
    print("样本 %d 条 | 模型 %d 个 | 组合 %d 组 | 主口径 %s | 繁简后端 %s"
          % (meta["n_samples"], meta["n_models"], meta["n_groups"],
             meta["primary_metric"], meta["trad_backend"]))
    print("数据来源: %s" % ", ".join(meta["data_sources"]))
    if meta["monotonicity_violations"]:
        print("!! 单调性违规 %d 处（协议实现可能有问题）" % len(meta["monotonicity_violations"]))
    else:
        print("单调性自检: 通过（固定分母口径，18 组内无违规）")

    lt = meta.get("long_text_fallback", {})
    if lt.get("groups_with_overflow"):
        print("!! 长文本保护触发：%d 组、共 %d 条样本的"
              % (lt["groups_with_overflow"], lt["samples_with_overflow_total"]))
        print("   「编辑距离」实为长度差估算 —— 这些组的数字不得作为真实 CER 引用。")
        print("   判断依据见 scores.csv 的 overflow_samples 列与 SPEC 第 4.6 节。")
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
        print("%-7s %-26s %10s %9s %7s %7s %9s"
              % ("名次", "名次对", "gap", "margin", "翻转组", "CI重叠", "判定"))
        for v in verdicts:
            print("%-7s %-26s %10.6f %9s %7d %7d %9s"
                  % ("%d vs %d" % (v["rank"], v["rank"] + 1),
                     "%s / %s" % (v["model_a"], v["model_b"]),
                     v["gap"], v["margin"], v["flipped_groups"],
                     v["ci_overlap"], v["verdict"]))
        n_stable = sum(1 for v in verdicts if v["verdict"] == "STABLE")
        print("  -> %d/%d 个相邻名次对站得住（STABLE）；判据见 SPEC.md 第 4.5 节"
              % (n_stable, len(verdicts)))

    # 注意打印的是实际生效的 --out 目录，不是写死的默认值 ——
    # 自定义 --out 时若仍显示默认目录，会让人跑到错的地方找产物。
    print("\n输出目录: %s" % os.path.abspath(out_dir))
    print("  scores.csv / rank_matrix.csv / stability.csv / verdicts.csv"
          " / protocol_curve.csv / meta.json / report.html")


def main() -> int:
    ap = argparse.ArgumentParser(description="文档解析评测：归一化协议敏感性实验")
    ap.add_argument("--demo", action="store_true",
                    help="先生成合成探针集（打通流程用，无需真实数据）")
    ap.add_argument("--gt", default=os.path.join(HERE, "data", "gt"))
    ap.add_argument("--models", default=os.path.join(HERE, "models_out"))
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("--boot", type=int, default=runner.BOOTSTRAP_N)
    args = ap.parse_args()

    if args.demo:
        info = make_demo.generate(args.gt, args.models)
        print("已生成合成数据:", json.dumps(info, ensure_ascii=False))
        print("注意：合成数据 source=generated，不可用于声称真实工具的水平。")

    res = runner.run(args.gt, args.models, args.out, n_boot=args.boot)
    _print_summary(res, args.out)

    report = make_report.build(res, os.path.join(args.out, "report.html"))
    print("可视化报告: %s" % report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
