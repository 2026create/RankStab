"""Regression checks for forced output replacement."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import runner


class OutputSafetyTests(unittest.TestCase):
    def test_failed_run_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / 'output'
            output.mkdir()
            previous = output / 'previous.txt'
            previous.write_text('keep', encoding='utf-8')
            with self.assertRaises(FileNotFoundError):
                runner.run(str(Path(root) / 'missing'), str(Path(root) / 'models'),
                           str(output), force=True)
            self.assertEqual(previous.read_text(encoding='utf-8'), 'keep')

    def test_publication_failures_preserve_previous_output(self):
        result = {'scores': [{'value': 1}], 'ranks': [], 'verdicts': [],
                  'stability': [], 'curve': [], 'meta': {}}
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / 'output'
            output.mkdir()
            (output / 'previous.txt').write_text('keep', encoding='utf-8')
            original_rename = os.rename

            def fail_new_directory(source, destination):
                if Path(source).name == 'new':
                    raise OSError('injected publication failure')
                return original_rename(source, destination)

            failures = [
                patch.object(runner, '_write_csv', side_effect=OSError('write failed')),
                patch('src.make_report.build', side_effect=OSError('report failed')),
                patch.object(runner.os, 'rename', side_effect=fail_new_directory),
            ]
            for index, failure in enumerate(failures):
                with self.subTest(failure=failure), failure:
                    with self.assertRaises(OSError):
                        runner._publish_results(str(output), result, True, index == 1)
                    self.assertEqual((output / 'previous.txt').read_text(encoding='utf-8'), 'keep')
            self.assertFalse(Path(str(output) + '.rankstab.lock').exists())

    def test_lock_and_gitkeep(self):
        result = {'scores': [{'value': 1}], 'ranks': [], 'verdicts': [],
                  'stability': [], 'curve': [], 'meta': {}}
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / 'output'
            output.mkdir()
            (output / '.gitkeep').write_text('keep', encoding='utf-8')
            lock = Path(str(output) + '.rankstab.lock')
            lock.mkdir()
            with self.assertRaises(RuntimeError):
                runner._publish_results(str(output), result, True, False)
            self.assertEqual((output / '.gitkeep').read_text(encoding='utf-8'), 'keep')
            lock.rmdir()
            runner._publish_results(str(output), result, True, False)
            self.assertEqual((output / '.gitkeep').read_text(encoding='utf-8'), 'keep')
            self.assertTrue((output / 'scores.csv').is_file())

    def test_output_cannot_overlap_input(self):
        with tempfile.TemporaryDirectory() as root:
            gt = Path(root) / 'gt'
            gt.mkdir()
            marker = gt / 'marker.txt'
            marker.write_text('keep', encoding='utf-8')
            for output in (gt, gt / 'nested', Path(root)):
                with self.subTest(output=output):
                    with self.assertRaises(ValueError):
                        runner.run(str(gt), str(Path(root) / 'models'), str(output), force=True)
                    self.assertTrue(marker.exists())


if __name__ == '__main__':
    unittest.main()
