"""Independent numerical oracles, alignment enumeration and verdict boundaries."""
from collections import Counter
from functools import lru_cache
from itertools import product
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from rapidfuzz.distance import Levenshtein

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import metrics, protocol, runner, segmatch


class NumericalOracleTests(unittest.TestCase):
    def test_edit_distance_matches_independent_library(self):
        words = [''.join(chars) for n in range(4) for chars in product('a甲B', repeat=n)]
        for reference, prediction in product(words, repeat=2):
            operations = metrics.edit_ops(reference, prediction)
            self.assertEqual(operations['dist'], Levenshtein.distance(reference, prediction))
            self.assertEqual(operations['dist'], operations['S'] + operations['D'] + operations['I'])
            self.assertEqual(len(reference) - operations['D'] + operations['I'], len(prediction))

    def test_unique_substitution_deletion_insertion(self):
        for reference, prediction, expected in [('ABC', 'AXC', (1, 0, 0)),
                                                 ('ABC', 'AC', (0, 1, 0)),
                                                 ('AC', 'ABC', (0, 0, 1))]:
            result = metrics.edit_ops(reference, prediction)
            self.assertEqual(tuple(result[key] for key in ('S', 'D', 'I')), expected)

    def test_block_alignment_matches_exhaustive_paths(self):
        sequences = [list(items) for n in range(3) for items in product(('a', 'b', 'ab'), repeat=n)]
        for reference, prediction in product(sequences, repeat=2):
            @lru_cache(None)
            def optimum(i, j):
                if i == len(reference):
                    return sum(map(len, prediction[j:]))
                if j == len(prediction):
                    return sum(map(len, reference[i:]))
                return min(len(reference[i]) + optimum(i + 1, j),
                           len(prediction[j]) + optimum(i, j + 1),
                           Levenshtein.distance(reference[i], prediction[j]) + optimum(i + 1, j + 1))
            pairs, deleted, inserted = segmatch.align(reference, prediction)
            actual = sum(Levenshtein.distance(a, b) for a, b in pairs)
            actual += sum(map(len, deleted)) + sum(map(len, inserted))
            self.assertEqual(actual, optimum(0, 0))
            self.assertEqual(Counter(a for a, b in pairs) + Counter(deleted), Counter(reference))
            self.assertEqual(Counter(b for a, b in pairs) + Counter(inserted), Counter(prediction))

    def test_block_alignment_keeps_suffix_after_extra_line(self):
        self.assertEqual(segmatch.split_blocks('甲\n乙', 'para'), ['甲', '乙'])
        pairs, deleted, inserted = segmatch.align(['甲', '乙', '丙'], ['甲', '额外一行', '乙', '丙'])
        self.assertEqual(pairs, [('甲', '甲'), ('乙', '乙'), ('丙', '丙')])
        self.assertEqual(deleted, [])
        self.assertEqual(inserted, ['额外一行'])

    def test_merge_preserves_structured_headings(self):
        for head in ('# 标题', '| 表格 |', '- 列表', '* 列表', '> 引用', '```代码', '$$公式', '![图片]'):
            self.assertEqual(segmatch.merge_truncated(['一段尚未结束的较长文本', head]),
                             ['一段尚未结束的较长文本', head])
        self.assertEqual(segmatch._needs_merge('', '甲'), False)
        self.assertEqual(segmatch._is_head(''), True)
        self.assertEqual(segmatch._default_cost('', '甲'), 1)
        self.assertEqual(segmatch._default_cost('甲', ''), 1)

    def test_normalization_expected_equivalences(self):
        cases = [('P0', ' ABC ', 'ABC'), ('P1', 'A \n\t B', 'A B'),
                 ('P2', 'ABC', 'abc'), ('P3', 'Ａ１２', 'a12'),
                 ('P4', '中文，句号。', '中文,句号.'), ('P5', '網 絡', '网络')]
        for level, text, expected in cases:
            self.assertEqual(protocol.normalize(text, level), expected)

    def test_aggregation_uses_total_errors_and_lengths(self):
        rows = [runner._score_one('A', 'B', 'whole', 'P0', 1),
                runner._score_one('A' * 9, 'A' * 9, 'whole', 'P0', 9)]
        self.assertAlmostEqual(metrics.corpus_cer(rows)['cer_fixed'], .1)
        self.assertEqual(metrics.corpus_cer([])['cer'], 0)
        self.assertEqual(metrics.corpus_cer([{}])['cer'], 0)

    def test_unmatched_blocks_account_for_all_characters(self):
        self.assertEqual(runner._score_one('甲\n乙', '', 'para', 'P0', 2)['del_rate'], 1)
        self.assertEqual(runner._score_one('', '甲\n乙', 'para', 'P0', 1)['ins_rate'], 2)

    def test_bootstrap_reproducible_and_pair_reversal(self):
        a = [runner._score_one('AB', text, 'whole', 'P0', 2) for text in ('AB', 'AC', 'ZZ', '')]
        b = [runner._score_one('AB', text, 'whole', 'P0', 2) for text in ('AB', 'AB', 'AB', 'AC')]
        self.assertEqual(metrics.bootstrap_ci(a, n_boot=100, seed=7),
                         metrics.bootstrap_ci(a, n_boot=100, seed=7))
        lo, hi = metrics.bootstrap_diff_ci(a, b, n_boot=100, seed=7)
        reverse_lo, reverse_hi = metrics.bootstrap_diff_ci(b, a, n_boot=100, seed=7)
        self.assertAlmostEqual(lo, -reverse_hi)
        self.assertAlmostEqual(hi, -reverse_lo)

    def test_cluster_aggregation_preserves_weighted_score(self):
        rows = [runner._score_one('AB', value, 'whole', 'P0', 2) for value in ('AB', 'AC', '')]
        grouped = metrics.cluster_rows(rows, ['doc1', 'doc1', 'doc2'])
        self.assertEqual(len(grouped), 2)
        self.assertEqual(grouped[0]['fixed_denom'], 4)
        self.assertEqual(metrics.corpus_cer(rows), metrics.corpus_cer(grouped))
        with self.assertRaises(ValueError):
            metrics.cluster_rows(rows, ['doc1'])

    def test_explicit_edit_limit_keeps_exact_counts(self):
        with self.assertRaises(ValueError):
            metrics.edit_ops('ABC', 'AXC', max_cells=8)
        result = metrics.edit_ops('ABC', 'AXC', max_cells=9)
        self.assertEqual((result['dist'], result['S']), (1, 1))


class VerdictOracleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.gt = self.root / 'gt'
        self.gt.mkdir()
        (self.gt / 'index.json').write_text(json.dumps([{'sample_id': 's1'}, {'sample_id': 's2'}]), encoding='utf-8')
        for sample in ('s1', 's2'):
            (self.gt / (sample + '.md')).write_text('AB', encoding='utf-8')
        self.models = self.root / 'models'
        for name, text in [('model_a', 'AC'), ('model_b', 'ab')]:
            folder = self.models / name
            folder.mkdir(parents=True)
            for sample in ('s1', 's2'):
                (folder / (sample + '.md')).write_text(text, encoding='utf-8')

    def run_fixture(self, **options):
        defaults = dict(n_boot=40, ref_seg='whole', make_html_report=True)
        defaults.update(options)
        return runner.run(str(self.gt), str(self.models), str(self.root / 'out'), **defaults)

    def run_matrix(self, a, b, **options):
        def score(reference, prediction, mode, level, fixed_denom=None, **kwargs):
            value = (a if prediction == 'AC' else b)[protocol.LEVELS.index(level)]
            return dict(cer=value, cer_fixed=value, sub_rate=value, del_rate=0,
                        ins_rate=0, ref_len=2, fixed_denom=2, overflow=0)
        with patch.object(runner, '_score_one', side_effect=score):
            return self.run_fixture(**options)

    def test_hand_calculated_case_reverses_under_casefold(self):
        result = self.run_fixture()
        pair = result['verdicts'][0]
        self.assertEqual(pair['verdict'], 'FRAGILE')
        self.assertEqual(pair['verdict_reason'], 'observed_reversal')
        self.assertEqual(pair['flipped_groups'], 12)
        self.assertEqual(pair['min_pair_difference'], -.5)
        self.assertEqual(pair['min_difference_group'], 'whole/P2')
        scores = {(x['level'], x['model']): x['cer_fixed'] for x in result['scores'] if x['seg_mode'] == 'whole'}
        self.assertEqual([scores['P0', 'model_a'], scores['P0', 'model_b']], [.5, 1])
        self.assertEqual([scores['P2', 'model_a'], scores['P2', 'model_b']], [.5, 0])

    def test_stable_with_positive_gap_and_zero_spans(self):
        pair = self.run_matrix([0] * 6, [.5] * 6)['verdicts'][0]
        self.assertEqual(pair['verdict'], 'STABLE')
        self.assertEqual(pair['margin'], 'inf')
        self.assertEqual(pair['min_pair_difference'], .5)
        self.assertEqual(pair['verdict_reason'], 'sufficient_margin')

    def test_margin_equal_one_allows_tie_without_reversal(self):
        pair = self.run_matrix([0, .25, .25, .25, .25, .25], [.25] * 6)['verdicts'][0]
        self.assertEqual(pair['margin'], 1)
        self.assertEqual(pair['verdict'], 'STABLE')
        self.assertEqual(pair['flipped_groups'], 0)
        self.assertEqual(pair['min_pair_difference'], 0)

    def test_common_score_shift_can_leave_margin_insufficient(self):
        pair = self.run_matrix([0, .5, .5, .5, .5, .5], [.25, .75, .75, .75, .75, .75])['verdicts'][0]
        self.assertEqual(pair['verdict'], 'FRAGILE')
        self.assertEqual(pair['verdict_reason'], 'insufficient_margin')
        self.assertEqual(pair['flipped_groups'], 0)
        self.assertEqual(pair['min_pair_difference'], .25)

    def test_ci_overlap_takes_precedence_over_observed_reversal(self):
        with patch.object(runner, 'bootstrap_ci', return_value=(0, 1)):
            pair = self.run_fixture()['verdicts'][0]
        self.assertEqual(pair['verdict'], 'TIE')
        self.assertEqual(pair['verdict_reason'], 'reference_ci_overlap')
        self.assertEqual(pair['flipped_groups'], 12)

    def test_equal_scores_and_zero_spans_are_tie(self):
        pair = self.run_matrix([.25] * 6, [.25] * 6)['verdicts'][0]
        self.assertEqual(pair['margin'], 0)
        self.assertEqual(pair['verdict'], 'TIE')

    def test_single_model_has_no_adjacent_pair(self):
        result = self.run_fixture(model_names=['model_a'])
        self.assertEqual(result['verdicts'], [])
        self.assertEqual(result['stability'][0]['verdict'], 'N/A')

    def test_seed_is_recorded_in_all_metadata(self):
        result = self.run_fixture(bootstrap_seed=19)
        self.assertEqual(result['meta']['bootstrap_seed'], 19)
        self.assertEqual(result['meta']['paired_diff']['seed'], 19)

    def test_cluster_bootstrap_records_unit_count(self):
        self.gt.joinpath('index.json').write_text(json.dumps([
            {'sample_id': 's1', 'document_id': 'doc'},
            {'sample_id': 's2', 'document_id': 'doc'}]), encoding='utf-8')
        result = self.run_fixture(bootstrap_cluster_field='document_id')
        self.assertEqual(result['meta']['bootstrap_n_units'], 1)
        self.assertEqual(result['meta']['bootstrap_unit'], 'cluster')
        self.assertEqual(result['verdicts'][0]['ci_overlap'], '')
        html = self.root.joinpath('out/report.html').read_text(encoding='utf-8')
        self.assertIn('--cluster-field', html)
        self.assertIn('--seed 20260921', html)

    def test_edit_limit_is_checked_and_recorded(self):
        with self.assertRaises(ValueError):
            self.run_fixture(edit_cell_limit=0)
        result = self.run_fixture(edit_cell_limit=9)
        self.assertEqual(result['meta']['long_text_fallback']['trigger_threshold_product'], 9)


if __name__ == '__main__':
    unittest.main()
