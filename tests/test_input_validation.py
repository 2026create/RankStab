"""Regression checks for evaluation input and parameter validation."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import make_report, runner


class InputValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.gt = self.root / 'gt'
        self.gt.mkdir()
        self.models = self.root / 'models'
        (self.models / 'model').mkdir(parents=True)
        self.out = self.root / 'out'
        self.write_index(['a', 'b'])
        for sid in ('a', 'b'):
            (self.gt / (sid + '.md')).write_text('中文', encoding='utf-8')

    def write_index(self, sample_ids):
        (self.gt / 'index.json').write_text(json.dumps(
            [{'sample_id': sid} for sid in sample_ids]), encoding='utf-8')

    def test_rejects_empty_duplicate_and_path_ids(self):
        for ids in ([], ['a', 'a'], ['../secret'], ['folder\\secret'], ['']):
            with self.subTest(ids=ids):
                self.write_index(ids)
                with self.assertRaises(ValueError):
                    runner.load_gt(str(self.gt))

    def test_invalid_parameters_do_not_silently_fallback(self):
        cases = ({'seg_modes': ['whole', 'unknown']},
                 {'seg_modes': ['whole', 'whole']},
                 {'seg_modes': ['whole'], 'ref_seg': 'para'},
                 {'ref_level': 'P99'}, {'n_boot': 0})
        for params in cases:
            with self.subTest(params=params):
                with self.assertRaises(ValueError):
                    runner.run(str(self.gt), str(self.models), str(self.out), **params)
                self.assertFalse(self.out.exists())

    def test_missing_file_is_distinct_from_empty_file(self):
        (self.models / 'model' / 'a.md').write_text('', encoding='utf-8')
        result = runner.run(str(self.gt), str(self.models), str(self.out),
                            seg_modes=['whole'], n_boot=10)
        self.assertEqual(result['meta']['missing_predictions'], {'model': ['b']})
        self.assertEqual(result['meta']['ref_group']['seg_mode'], 'whole')
        report = self.out / 'report.html'
        make_report.build(result, str(report))
        self.assertIn('model：b', report.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
