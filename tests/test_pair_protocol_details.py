import json
from pathlib import Path
import tempfile
import unittest

from src import runner
from src.runner import pair_protocol_details


class PairProtocolDetailsTests(unittest.TestCase):
    def make_scores(self, values):
        scores = []
        for mode, series in values.items():
            for index, delta in enumerate(series):
                for model, value in (("a", 0.0), ("b", delta)):
                    scores.append({"seg_mode": mode, "level": "P%d" % index,
                                   "model": model, "cer_fixed": value})
        return scores

    def test_five_reversed_groups_are_one_transition(self):
        values = {"whole": [1, -1, -2, -3, -4, -5]}
        details = pair_protocol_details(self.make_scores(values),
            [{"model_a": "a", "model_b": "b", "cer_a": 0, "cer_b": 1}],
            ["whole"], ["P%d" % i for i in range(6)])
        self.assertEqual(sum(r["relative_to_reference"] == "逆序" for r in details), 5)
        self.assertEqual(sum(r["transition_from_previous_level"] == "换位" for r in details), 1)

    def test_ties_and_mode_boundaries_are_not_transitions(self):
        values = {"whole": [1, 0, -1], "para": [-1, -1, 1]}
        details = pair_protocol_details(self.make_scores(values),
            [{"model_a": "a", "model_b": "b", "cer_a": 0, "cer_b": 1}],
            ["whole", "para"], ["P0", "P1", "P2"])
        self.assertEqual([r["transition_from_previous_level"] for r in details],
                         ["起始", "涉及平分", "涉及平分", "起始", "未换位", "换位"])

    def test_reference_tie_has_no_direction(self):
        details = pair_protocol_details(self.make_scores({"whole": [0, -1]}),
            [{"model_a": "a", "model_b": "b", "cer_a": 0, "cer_b": 0}],
            ["whole"], ["P0", "P1"])
        self.assertTrue(all(r["relative_to_reference"] == "参考组平分" for r in details))

    def test_selection_and_report_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            gt = root / 'gt'
            models = root / 'models'
            gt.mkdir()
            (gt / 'index.json').write_text(json.dumps([{'sample_id': 'a'}, {'sample_id': 'b'}]), encoding='utf-8')
            for sid in ('a', 'b'):
                (gt / (sid + '.md')).write_text('abc', encoding='utf-8')
            for model in ('a&b', 'other'):
                directory = models / model
                directory.mkdir(parents=True)
                for sid in ('a', 'b'):
                    (directory / (sid + '.md')).write_text('abc', encoding='utf-8')
            output = root / 'out'
            result = runner.run(str(gt), str(models), str(output), seg_modes=['whole'],
                                model_names=['a&b'], n_boot=2, make_html_report=True)
            self.assertEqual(result['meta']['models'], ['a&b'])
            report = (output / 'report.html').read_text(encoding='utf-8')
            self.assertIn('a&amp;b', report)
            self.assertNotIn('a&b', report)
            for invalid in ([], ['missing'], ['other', 'other']):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    runner.run(str(gt), str(models), str(root / 'invalid'),
                               seg_modes=['whole'], model_names=invalid, n_boot=2)

    def test_identical_scores_with_one_sample_are_not_stable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            gt = root / 'gt'
            gt.mkdir()
            (gt / 'index.json').write_text(json.dumps([{'sample_id': 'a'}]), encoding='utf-8')
            (gt / 'a.md').write_text('same', encoding='utf-8')
            models = root / 'models'
            for model in ('first', 'second'):
                directory = models / model
                directory.mkdir(parents=True)
                (directory / 'a.md').write_text('same', encoding='utf-8')
            output = root / 'out'
            result = runner.run(str(gt), str(models), str(output), seg_modes=['whole'],
                                n_boot=2, make_html_report=True)
            self.assertEqual(result['verdicts'][0]['verdict'], 'FRAGILE')
            report = (output / 'report.html').read_text(encoding='utf-8')
            self.assertIn('存在不可估计的置信区间', report)


if __name__ == "__main__":
    unittest.main()
