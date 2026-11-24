"""Run judgment-layer validation for RankStab.

The existing suite validates numerical implementation. This script validates
the label layer with known score profiles, scans the production margin rule,
and checks three metamorphic properties of the public runner entry point.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties


HERE = Path(__file__).resolve()
CODE_ROOT = HERE.parents[1]
PROJECT_ROOT = CODE_ROOT.parent
sys.path.insert(0, str(CODE_ROOT))

from src import protocol, runner  # noqa: E402


ARTIFACT_DIR = PROJECT_ROOT / "04_实验产物/20261123_判定层验证"
PRODUCTION_VERDICTS = (
    PROJECT_ROOT / "09_国赛/复现结果/扩充200_六配置_18组/verdicts.csv"
)
CANONICAL_LEVELS = ["P0", "P1", "P2", "P3", "P4", "P5"]
CHINESE_FONT = FontProperties(fname="C:/Windows/Fonts/msyh.ttc")


def _write_fixture(root: Path, n_samples: int = 24) -> tuple[Path, Path]:
    gt_dir = root / "gt"
    models_dir = root / "models_out"
    gt_dir.mkdir(parents=True)
    for model in ("model_a", "model_b"):
        (models_dir / model).mkdir(parents=True)

    entries = []
    for index in range(n_samples):
        sample_id = f"sample_{index:03d}"
        entries.append({"sample_id": sample_id})
        (gt_dir / f"{sample_id}.md").write_text(
            f"fixture_{index:03d}", encoding="utf-8"
        )
        (models_dir / "model_a" / f"{sample_id}.md").write_text(
            "A", encoding="utf-8"
        )
        (models_dir / "model_b" / f"{sample_id}.md").write_text(
            "B", encoding="utf-8"
        )
    (gt_dir / "index.json").write_text(
        json.dumps(entries, ensure_ascii=False), encoding="utf-8"
    )
    return gt_dir, models_dir


def _profile(kind: str, case_index: int) -> dict[str, list[float]]:
    offset = case_index * 0.001
    if kind == "STABLE":
        base = 0.10 + offset
        return {"model_a": [base] * 6, "model_b": [base + 0.20] * 6}
    if kind == "FRAGILE":
        base = 0.10 + offset
        return {
            "model_a": [base] * 6,
            "model_b": [base + 0.20, base + 0.20, base + 0.20,
                         max(0.0, base - 0.05), max(0.0, base - 0.05),
                         max(0.0, base - 0.05)],
        }
    if kind == "TIE":
        base = 0.20 + offset
        return {"model_a": [base] * 6, "model_b": [base] * 6}
    raise ValueError(kind)


def _fake_score(profile: dict[str, list[float]]):
    def score(reference, prediction, _seg_mode, level, **_kwargs):
        model = "model_a" if prediction == "A" else "model_b"
        sample_index = int(reference.rsplit("_", 1)[-1])
        sample_noise = ((sample_index % 5) - 2) * 0.002
        value = profile[model][CANONICAL_LEVELS.index(level)] + sample_noise
        return {
            "cer": value,
            "cer_fixed": value,
            "sub_rate": value,
            "del_rate": 0.0,
            "ins_rate": 0.0,
            "ref_len": 100,
            "fixed_denom": 100,
            "overflow": 0,
        }

    return score


def _run_profile(
    workspace: Path,
    profile: dict[str, list[float]],
    name: str,
    levels: list[str] | None = None,
):
    gt_dir, models_dir = _write_fixture(workspace / name)
    output_dir = workspace / f"{name}_out"
    with patch.object(runner, "_score_one", side_effect=_fake_score(profile)):
        return runner.run(
            str(gt_dir),
            str(models_dir),
            str(output_dir),
            n_boot=200,
            bootstrap_seed=20261123,
            seg_modes=["whole"],
            ref_seg="whole",
            ref_level="P0",
            force=True,
        )


def run_confusion_matrix(workspace: Path) -> dict:
    expected_kinds = ["STABLE", "FRAGILE", "TIE"]
    records = []
    for kind in expected_kinds:
        for case_index in range(20):
            result = _run_profile(
                workspace, _profile(kind, case_index), f"{kind.lower()}_{case_index:02d}"
            )
            pair = result["verdicts"][0]
            records.append(
                {
                    "case": f"{kind.lower()}_{case_index:02d}",
                    "expected": kind,
                    "predicted": pair["verdict"],
                    "margin": pair["margin"],
                    "flipped_groups": pair["flipped_groups"],
                    "verdict_reason": pair["verdict_reason"],
                }
            )

    matrix = {
        expected: {predicted: 0 for predicted in expected_kinds}
        for expected in expected_kinds
    }
    for record in records:
        matrix[record["expected"]][record["predicted"]] += 1
    correct = sum(matrix[kind][kind] for kind in expected_kinds)
    summary = {
        "pairs": len(records),
        "per_class": 20,
        "labels": expected_kinds,
        "matrix": matrix,
        "accuracy": correct / len(records),
        "records": records,
    }
    return summary


def _read_production_verdicts() -> list[dict[str, str]]:
    with PRODUCTION_VERDICTS.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _label_for_threshold(row: dict[str, str], threshold: float) -> str:
    if row.get("ci_overlap", "") == "1":
        return "TIE"
    if int(row.get("flipped_groups", "0") or 0) > 0:
        return "FRAGILE"
    margin = float(row["margin"])
    return "FRAGILE" if margin < threshold else "STABLE"


def run_threshold_scan() -> dict:
    rows = _read_production_verdicts()
    thresholds = sorted(
        set([round(index / 40, 3) for index in range(81)]
            + [0.001, 0.005, 0.01, 0.02, 0.05, 1.0])
    )
    scan = []
    for threshold in thresholds:
        counts = Counter(_label_for_threshold(row, threshold) for row in rows)
        scan.append(
            {
                "threshold": threshold,
                "STABLE": counts["STABLE"],
                "FRAGILE": counts["FRAGILE"],
                "TIE": counts["TIE"],
                "total": len(rows),
            }
        )

    figure, axis = plt.subplots(figsize=(8.4, 4.2), dpi=180)
    for label, color in (
        ("STABLE", "#0f766e"),
        ("FRAGILE", "#ef6b50"),
        ("TIE", "#1f4e79"),
    ):
        axis.plot(
            [row["threshold"] for row in scan],
            [row[label] for row in scan],
            label=label,
            linewidth=2.2,
        )
    axis.axvline(1.0, color="#d97706", linestyle="--", linewidth=1.6)
    axis.text(1.02, axis.get_ylim()[1] * 0.94, "当前阈值 = 1.0",
              color="#b45309", fontsize=9, fontproperties=CHINESE_FONT)
    axis.set_xlabel("margin 阈值", fontproperties=CHINESE_FONT)
    axis.set_ylabel("相邻模型对数量", fontproperties=CHINESE_FONT)
    axis.set_title("200 页真实结果的阈值敏感性", fontproperties=CHINESE_FONT)
    axis.set_xlim(0, 2.0)
    axis.set_ylim(bottom=0)
    axis.grid(axis="y", alpha=0.25)
    axis.legend(frameon=False, ncol=3, loc="upper left")
    figure.tight_layout()
    figure.savefig(ARTIFACT_DIR / "阈值敏感性曲线.png", facecolor="white")
    plt.close(figure)
    return {
        "source": str(PRODUCTION_VERDICTS.relative_to(PROJECT_ROOT)),
        "scan_range": [0.0, 2.0],
        "included_fine_range": [0.001, 0.05],
        "current_threshold": 1.0,
        "threshold_is_sufficient_condition": True,
        "rows": scan,
    }


def _semantic_csv(path: Path, key_fields: list[str]) -> list[tuple[str, ...]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = csv.DictReader(handle)
        return sorted(tuple(row.get(field, "") for field in key_fields) for row in rows)


def run_metamorphic_tests(workspace: Path) -> dict:
    profile = _profile("FRAGILE", 3)
    baseline = _run_profile(workspace, profile, "metamorphic_baseline")
    swapped = _run_profile(
        workspace,
        {"model_a": profile["model_b"], "model_b": profile["model_a"]},
        "metamorphic_swapped",
    )
    base_pair = baseline["verdicts"][0]
    swapped_pair = swapped["verdicts"][0]
    symmetry = (
        base_pair["verdict"] == swapped_pair["verdict"]
        and base_pair["flipped_groups"] == swapped_pair["flipped_groups"]
    )

    first_dir = workspace / "metamorphic_baseline_out"
    repeat_dir = workspace / "metamorphic_repeat_out"
    gt_dir, models_dir = _write_fixture(workspace / "metamorphic_repeat")
    with patch.object(runner, "_score_one", side_effect=_fake_score(profile)):
        runner.run(str(gt_dir), str(models_dir), str(repeat_dir), n_boot=200,
                   bootstrap_seed=20261123, seg_modes=["whole"],
                   ref_seg="whole", ref_level="P0", force=True)
    file_names = sorted(path.name for path in first_dir.iterdir() if path.is_file())
    repeat_files = sorted(path.name for path in repeat_dir.iterdir() if path.is_file())
    idempotent = file_names == repeat_files and all(
        (first_dir / name).read_bytes() == (repeat_dir / name).read_bytes()
        for name in file_names
    )

    original_levels = list(runner.LEVELS)
    try:
        runner.LEVELS[:] = list(reversed(original_levels))
        reversed_result = _run_profile(
            workspace,
            profile,
            "metamorphic_reversed_levels",
        )
    finally:
        runner.LEVELS[:] = original_levels
    semantic_fields = {
        "scores.csv": ["seg_mode", "level", "model", "cer_fixed", "ci_lo", "ci_hi"],
        "rank_matrix.csv": ["seg_mode", "level", "model", "rank", "cer_fixed"],
        "verdicts.csv": ["model_a", "model_b", "gap", "margin", "flipped_groups", "verdict"],
    }
    order_independent = True
    for file_name, fields in semantic_fields.items():
        left = _semantic_csv(workspace / "metamorphic_baseline_out" / file_name, fields)
        right = _semantic_csv(
            workspace / "metamorphic_reversed_levels_out" / file_name, fields
        )
        order_independent = order_independent and left == right

    return {
        "symmetry": {
            "passed": symmetry,
            "baseline": {
                "verdict": base_pair["verdict"],
                "flipped_groups": base_pair["flipped_groups"],
            },
            "swapped": {
                "verdict": swapped_pair["verdict"],
                "flipped_groups": swapped_pair["flipped_groups"],
            },
        },
        "idempotence": {"passed": idempotent, "byte_identical": idempotent},
        "level_order_independence": {
            "passed": order_independent,
            "compared_files": list(semantic_fields),
        },
        "all_passed": symmetry and idempotent and order_independent,
    }


def main() -> int:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rankstab_verdict_layer_") as temporary:
        workspace = Path(temporary)
        confusion = run_confusion_matrix(workspace)
        metamorphic = run_metamorphic_tests(workspace)
    threshold = run_threshold_scan()

    (ARTIFACT_DIR / "合成数据混淆矩阵.csv").write_text(
        "expected,predicted,count\n"
        + "\n".join(
            f"{expected},{predicted},{confusion['matrix'][expected][predicted]}"
            for expected in confusion["labels"]
            for predicted in confusion["labels"]
        )
        + "\n",
        encoding="utf-8",
    )
    with (ARTIFACT_DIR / "阈值敏感性.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(threshold["rows"][0]))
        writer.writeheader()
        writer.writerows(threshold["rows"])

    summary = {
        "synthetic_confusion_matrix": confusion,
        "threshold_scan": {
            key: value for key, value in threshold.items() if key != "rows"
        },
        "metamorphic_tests": metamorphic,
        "interpretation": {
            "implementation_layer": "existing 145 assertions and 79 automated tests",
            "judgment_layer": "synthetic known-label confusion matrix",
            "reproducibility_layer": "metamorphic properties and fixed-seed byte comparison",
            "threshold_note": (
                "margin=1.0 is the derived sufficient-condition boundary. The scan tests "
                "sensitivity around that boundary and does not tune the threshold by class balance."
            ),
        },
    }
    (ARTIFACT_DIR / "判定层验证汇总.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if confusion["accuracy"] == 1 and metamorphic["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
