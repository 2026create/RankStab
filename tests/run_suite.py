"""Run legacy assertions and unittest cases through one auditable entry point."""
import argparse
import json
from pathlib import Path
import runpy
import sys
import time
import unittest


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))


class RecordingResult(unittest.TextTestResult):
    def startTest(self, test):
        super().startTest(test)
        self.started = time.perf_counter()

    def addSuccess(self, test):
        super().addSuccess(test)
        self.records.append({'case': test.id(), 'status': 'PASS',
                             'seconds': round(time.perf_counter() - self.started, 4)})

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.records.append({'case': test.id(), 'status': 'FAIL',
                             'detail': self._exc_info_to_string(err, test)})

    def addError(self, test, err):
        super().addError(test, err)
        self.records.append({'case': test.id(), 'status': 'ERROR',
                             'detail': self._exc_info_to_string(err, test)})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    legacy = runpy.run_path(str(HERE / 'test_all.py'), run_name='__main__')
    suite = unittest.defaultTestLoader.discover(str(HERE), pattern='test_*.py')
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordingResult).run(suite)
    report = {'legacy_assertions': legacy['_PASS'], 'unit_tests': result.testsRun,
              'failures': len(result.failures), 'errors': len(result.errors),
              'skipped': len(result.skipped), 'cases': result.records}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('SUITE %d assertions / %d tests / %d failures / %d errors' % (
        report['legacy_assertions'], result.testsRun, len(result.failures), len(result.errors)))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
