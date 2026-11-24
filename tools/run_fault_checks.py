"""Execute ten isolated mutation checks against independent regression tests."""
import argparse
import json
import os
from pathlib import Path
from shutil import copy2
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = [
    ('M01', 'metrics.py', 'cost = 0 if ci == hyp[j - 1] else 1', 'cost = 0',
     'test_oracles.NumericalOracleTests.test_edit_distance_matches_independent_library', '字符替换成本被错误置零'),
    ('M02', 'metrics.py', '"cer_fixed": tot_err_fx / tot_fx,', '"cer_fixed": tot_err_fx / (tot_fx + 1),',
     'test_oracles.NumericalOracleTests.test_aggregation_uses_total_errors_and_lengths', '语料级分母被错误增加'),
    ('M03', 'protocol.py', 'out = rule(out)', 'out = out',
     'test_oracles.NumericalOracleTests.test_normalization_expected_equivalences', '归一化规则被跳过'),
    ('M04', 'runner.py', 'if _margin(a, b) < MARGIN_THRESHOLD:', 'if _margin(a, b) <= MARGIN_THRESHOLD:',
     'test_oracles.VerdictOracleTests.test_margin_equal_one_allows_tie_without_reversal', 'margin 等于一被错误判为脆弱'),
    ('M05', 'runner.py', 'if _overlap(a, b) is True:', 'if False:',
     'test_oracles.VerdictOracleTests.test_ci_overlap_takes_precedence_over_observed_reversal', '区间重叠优先级被移除'),
    ('M06', 'runner.py', '        return n\n\n    def _overlap', '        return 0\n\n    def _overlap',
     'test_oracles.VerdictOracleTests.test_hand_calculated_case_reverses_under_casefold', '实际逆序判定分支被移除'),
    ('M07', 'runner.py', 'models = {name: models[name] for name in sorted(selected)}', 'models = models',
     'test_oracles.VerdictOracleTests.test_single_model_has_no_adjacent_pair', '模型选择被忽略'),
    ('M08', 'segmatch.py', 'return lines', 'return [raw]',
     'test_oracles.NumericalOracleTests.test_block_alignment_keeps_suffix_after_extra_line', '行切分退化为整页'),
    ('M09', 'runner.py', '"bootstrap_seed": bootstrap_seed,', '"bootstrap_seed": 0,',
     'test_oracles.VerdictOracleTests.test_seed_is_recorded_in_all_metadata', '随机种子记录失真'),
    ('M10', 'runner.py', 'minimum_difference, minimum_group = min(differences, key=lambda item: item[0])',
     'minimum_difference, minimum_group = max(differences, key=lambda item: item[0])',
     'test_oracles.VerdictOracleTests.test_hand_calculated_case_reverses_under_casefold', '最小分差被错误取为最大值'),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    records = []
    with tempfile.TemporaryDirectory(prefix='rankstab_mutations_') as temporary:
        temporary = Path(temporary)
        for number, filename, original, mutated, target, description in MUTATIONS:
            folder = temporary / number
            (folder / 'src').mkdir(parents=True)
            (folder / 'tests').mkdir()
            for path in (ROOT / 'src').glob('*.py'):
                copy2(path, folder / 'src' / path.name)
            for path in (ROOT / 'tests').glob('*.py'):
                copy2(path, folder / 'tests' / path.name)
            path = folder / 'src' / filename
            text = path.read_text(encoding='utf-8')
            if text.count(original) < 1:
                raise ValueError('%s anchor count %d' % (number, text.count(original)))
            path.write_text(text.replace(original, mutated, 1), encoding='utf-8')
            process = subprocess.run([sys.executable, '-m', 'unittest', target], cwd=folder / 'tests',
                                     capture_output=True, encoding='utf-8', errors='replace',
                                     env=dict(os.environ, PYTHONUTF8='1'), timeout=30)
            killed = process.returncode != 0 and 'AssertionError' in process.stderr
            records.append({'id': number, 'description': description, 'test': target,
                            'detected': killed, 'exit_code': process.returncode,
                            'evidence': process.stderr[-2500:]})
            print(number, 'DETECTED' if killed else 'SURVIVED', flush=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if all(record['detected'] for record in records) else 1


if __name__ == '__main__':
    sys.exit(main())
