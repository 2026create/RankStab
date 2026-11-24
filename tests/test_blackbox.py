"""CLI equivalence classes and boundaries exercised as external processes."""
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BlackBoxTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='rankstab_cli_')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.gt = self.root / '真值'
        self.gt.mkdir()
        self.models = self.root / '模型'
        for name, prediction in [('alpha', 'AC'), ('beta', 'ab')]:
            folder = self.models / name
            folder.mkdir(parents=True)
            for sample in ('one', 'two'):
                (folder / (sample + '.md')).write_text(prediction, encoding='utf-8')
        self.index = self.gt / 'index.json'
        self.index.write_text(json.dumps([{'sample_id': 'one'}, {'sample_id': 'two'}]), encoding='utf-8')
        for sample in ('one', 'two'):
            (self.gt / (sample + '.md')).write_text('AB', encoding='utf-8')
        self.out = self.root / '结果'

    def invoke(self, *arguments, expected=0):
        env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        result = subprocess.run([sys.executable, str(ROOT / 'run.py'),
                                 '--gt', str(self.gt), '--models', str(self.models),
                                 '--out', str(self.out), '--boot', '40', *arguments],
                                capture_output=True, encoding='utf-8', env=env, timeout=45)
        if expected == 0:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((self.out / 'report.html').is_file())
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def read_csv(self, name):
        with (self.out / (name + '.csv')).open(encoding='utf-8-sig', newline='') as handle:
            return list(csv.DictReader(handle))

    def test_bb01_full_eighteen_groups_and_reversal(self):
        self.invoke()
        meta = json.loads((self.out / 'meta.json').read_text(encoding='utf-8'))
        self.assertEqual(meta['n_groups'], 18)
        self.assertEqual(len(self.read_csv('scores')), 36)
        self.assertEqual(int(self.read_csv('verdicts')[0]['flipped_groups']), 12)

    def test_bb02_whole_only_six_groups(self):
        self.invoke('--seg-modes', 'whole')
        self.assertEqual(len(self.read_csv('scores')), 12)

    def test_bb03_para_only_six_groups(self):
        self.invoke('--seg-modes', 'para')
        self.assertEqual(len(self.read_csv('scores')), 12)

    def test_bb04_merge_only_six_groups(self):
        self.invoke('--seg-modes', 'para_merge')
        self.assertEqual(len(self.read_csv('scores')), 12)

    def test_bb05_explicit_reference_level(self):
        self.invoke('--ref-level', 'P2')
        self.assertEqual(self.read_csv('verdicts')[0]['model_a'], 'beta')

    def test_bb06_one_selected_model(self):
        self.invoke('--select-models', 'alpha')
        self.assertEqual(len(self.read_csv('scores')), 18)
        self.assertFalse((self.out / 'verdicts.csv').exists())

    def test_bb07_explicit_seed(self):
        self.invoke('--seed', '9')
        self.assertEqual(json.loads((self.out / 'meta.json').read_text(encoding='utf-8'))['bootstrap_seed'], 9)

    def test_bb08_one_bootstrap_draw(self):
        self.invoke('--boot', '1')

    def test_bb09_missing_prediction_is_recorded(self):
        (self.models / 'alpha/one.md').unlink()
        self.invoke()
        self.assertEqual(json.loads((self.out / 'meta.json').read_text(encoding='utf-8'))['missing_predictions'],
                         {'alpha': ['one']})

    def test_bb10_empty_prediction_is_scored(self):
        (self.models / 'alpha/one.md').write_text('', encoding='utf-8')
        self.invoke()
        self.assertEqual(json.loads((self.out / 'meta.json').read_text(encoding='utf-8'))['missing_predictions'], {})

    def test_bb11_single_sample_unavailable_interval(self):
        self.index.write_text('[{"sample_id":"one"}]', encoding='utf-8')
        self.invoke()
        self.assertEqual(self.read_csv('scores')[0]['ci_lo'], '')

    def test_bb12_empty_reference(self):
        for sample in ('one', 'two'):
            (self.gt / (sample + '.md')).write_text('', encoding='utf-8')
        self.invoke()
        self.assertTrue(all(float(x['cer_fixed']) >= 0 for x in self.read_csv('scores')))

    def test_bb13_duplicate_run_preserves_old_results(self):
        self.invoke()
        old = (self.out / 'scores.csv').read_bytes()
        self.invoke(expected=1)
        self.assertEqual((self.out / 'scores.csv').read_bytes(), old)

    def test_bb14_force_reproduces_all_bytes(self):
        self.invoke()
        old = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.out.iterdir()}
        self.invoke('--force')
        self.assertEqual(old, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.out.iterdir()})

    def test_bb15_crlf_is_accepted(self):
        (self.gt / 'one.md').write_bytes(b'AB\r\n')
        self.invoke()

    def test_bb16_non_bmp_unicode_is_accepted(self):
        (self.gt / 'one.md').write_text('中文😀𠮷', encoding='utf-8')
        self.invoke()

    def test_bb17_long_text_limit_preserves_existing_output(self):
        self.out.mkdir()
        (self.out / 'marker').write_text('old', encoding='utf-8')
        (self.gt / 'one.md').write_text('a' * 2001, encoding='utf-8')
        (self.models / 'alpha/one.md').write_text('a' * 2001, encoding='utf-8')
        self.invoke('--force', expected=1)
        self.assertEqual((self.out / 'marker').read_text(encoding='utf-8'), 'old')

    def test_bb18_missing_ground_truth_file(self):
        (self.gt / 'one.md').unlink()
        self.invoke(expected=1)

    def test_bb19_missing_index(self):
        self.index.unlink()
        self.invoke(expected=1)

    def test_bb20_invalid_json(self):
        self.index.write_text('{broken', encoding='utf-8')
        self.invoke(expected=1)

    def test_bb21_invalid_utf8(self):
        (self.gt / 'one.md').write_bytes(b'\xff')
        self.invoke(expected=1)

    def test_bb22_no_model_directories(self):
        shutil.rmtree(self.models)
        self.models.mkdir()
        self.invoke(expected=1)

    def test_bb23_output_is_file(self):
        self.out.write_text('old', encoding='utf-8')
        self.invoke('--force', expected=1)
        self.assertEqual(self.out.read_text(encoding='utf-8'), 'old')

    def test_bb24_output_overlaps_inputs(self):
        self.invoke('--out', str(self.gt), '--force', expected=1)
        self.assertTrue(self.index.exists())


INVALID_ARGUMENTS = [
    ('bb25_zero_bootstrap', ['--boot', '0']),
    ('bb26_negative_bootstrap', ['--boot', '-1']),
    ('bb27_noninteger_bootstrap', ['--boot', 'abc']),
    ('bb28_invalid_seed', ['--seed', 'abc']),
    ('bb29_unknown_segmentation', ['--seg-modes', 'unknown']),
    ('bb30_duplicate_segmentation', ['--seg-modes', 'whole,whole']),
    ('bb31_reference_outside_selected_modes', ['--seg-modes', 'whole', '--ref-seg', 'para']),
    ('bb32_invalid_reference_level', ['--ref-level', 'P6']),
    ('bb33_unknown_model', ['--select-models', 'unknown']),
    ('bb34_duplicate_model', ['--select-models', 'alpha,alpha']),
    ('bb35_unknown_option', ['--unknown']),
]


def invalid_argument_test(arguments):
    def test(self):
        self.invoke(*arguments, expected=1)
        self.assertFalse(self.out.exists())
    return test


for case_name, arguments in INVALID_ARGUMENTS:
    setattr(BlackBoxTests, 'test_' + case_name, invalid_argument_test(arguments))


def invalid_index_test(content):
    def test(self):
        self.index.write_text(json.dumps(content), encoding='utf-8')
        self.invoke(expected=1)
        self.assertFalse(self.out.exists())
    return test


for case_name, content in [
        ('bb36_empty_index', []), ('bb37_object_index', {}),
        ('bb38_duplicate_sample_id', [{'sample_id': 'one'}, {'sample_id': 'one'}]),
        ('bb39_path_traversal', [{'sample_id': '../outside'}]),
        ('bb40_missing_sample_id', [{}])]:
    setattr(BlackBoxTests, 'test_' + case_name, invalid_index_test(content))


if __name__ == '__main__':
    unittest.main()
