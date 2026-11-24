"""Persist controlled score cases through the ordinary runner."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import runner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    scenarios = [('stable', ['AB', 'AB'], ['ZZ', 'ZZ']),
                 ('reversal', ['AC', 'AC'], ['ab', 'ab']),
                 ('tie', ['AB', 'ZZ'], ['AB', 'ZZ'])]
    summary = []
    with tempfile.TemporaryDirectory(prefix='rankstab_cases_') as temporary:
        root = Path(temporary)
        gt = root / 'gt'
        gt.mkdir()
        (gt / 'index.json').write_text(json.dumps([{'sample_id': 's1'}, {'sample_id': 's2'}]), encoding='utf-8')
        for sample in ('s1', 's2'):
            (gt / (sample + '.md')).write_text('AB', encoding='utf-8')
        for name, a, b in scenarios:
            models = root / name
            for model, outputs in [('A', a), ('B', b)]:
                folder = models / model
                folder.mkdir(parents=True)
                for sample, text in zip(('s1', 's2'), outputs):
                    (folder / (sample + '.md')).write_text(text, encoding='utf-8')
            result = runner.run(str(gt), str(models), str(args.out / name), n_boot=2000,
                                ref_seg='whole', make_html_report=True)
            summary.append(dict(case=name, source='controlled literal text', reference='AB',
                                predictions=dict(A=a, B=b), verdict=result['verdicts'][0]))
        # This case isolates the decision rule with deliberately supplied scores.
        def common_shift(reference, prediction, mode, level, fixed_denom=None, **unused):
            value = (0 if prediction == 'AC' else .25) + (0 if level == 'P0' else .5)
            return dict(cer=value, cer_fixed=value, sub_rate=value, del_rate=0,
                        ins_rate=0, ref_len=2, fixed_denom=2, overflow=0)
        with patch.object(runner, '_score_one', side_effect=common_shift):
            result = runner.run(str(gt), str(root / 'reversal'), str(args.out / 'common_shift'),
                                n_boot=2000, ref_seg='whole', make_html_report=True)
        summary.append(dict(case='common_shift', source='injected score matrix for rule isolation',
                            A=[0, .5, .5, .5, .5, .5], B=[.25, .75, .75, .75, .75, .75],
                            verdict=result['verdicts'][0]))
    (args.out / 'cases.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print([(row['case'], row['verdict']['verdict']) for row in summary])


if __name__ == '__main__':
    main()
