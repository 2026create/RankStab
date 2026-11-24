"""Run the frozen official text tests on the selected 50-page subset."""
import ast
import argparse
import csv
from dataclasses import dataclass, fields
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from types import ModuleType
import sys
from typing import Dict, List, Optional, Tuple, Union
import unicodedata

from fuzzysearch import find_near_matches
from rapidfuzz import fuzz


def load_official(source):
    names = {'TestType', 'TestChecked', 'ValidationError', 'normalize_text',
             'BasePDFTest', 'TextPresenceTest', 'TextOrderTest'}
    tree = ast.parse(source.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if getattr(node, 'name', None) in names]
    if {node.name for node in tree.body} != names:
        raise ValueError('Official definitions are incomplete')
    module = ModuleType('rankstab_frozen_official_tests')
    module.__dict__.update(globals())
    module.__name__ = 'rankstab_frozen_official_tests'
    sys.modules[module.__name__] = module
    exec(compile(tree, str(source), 'exec'), module.__dict__)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    official = load_official(args.source)
    checks = json.loads((args.data / 'checks.json').read_text(encoding='utf-8'))
    pages = {row['sample_id']: row for row in json.loads((args.data / 'index.json').read_text(encoding='utf-8'))}
    results = []
    for model in sorted((args.data / 'models_out').iterdir()):
        if not model.is_dir():
            continue
        for check in checks:
            cls = official.TextOrderTest if check['type'] == 'order' else official.TextPresenceTest
            allowed = {field.name for field in fields(cls)}
            case = cls(**{key: value for key, value in check.items() if key in allowed})
            prediction = model / (check['sample_id'] + '.md')
            if not prediction.is_file():
                raise FileNotFoundError(prediction)
            passed, detail = case.run(prediction.read_text(encoding='utf-8'))
            results.append(dict(model=model.name, sample_id=check['sample_id'],
                                id=check['id'], category=pages[check['sample_id']]['category'],
                                type=check['type'], passed=passed, detail=detail))
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / 'checks.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(results[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(results)
    summaries = []
    for model in sorted({row['model'] for row in results}):
        selected = [row for row in results if row['model'] == model]
        for dimension in ('all', 'type', 'category'):
            values = ['all'] if dimension == 'all' else sorted({row[dimension] for row in selected})
            for value in values:
                subset = selected if dimension == 'all' else [row for row in selected if row[dimension] == value]
                passed = sum(row['passed'] for row in subset)
                summaries.append(dict(model=model, dimension=dimension, value=value,
                                      passed=passed, total=len(subset), rate=passed / len(subset)))
    metadata = dict(pages=len(pages), checks=len(checks), results=summaries,
                    official_source_sha256=hashlib.sha256(args.source.read_bytes()).hexdigest(),
                    execution='Unmodified AST-selected official definitions',
                    scope='Selected 50 pages and 226 text checks, unweighted check micro-average')
    (args.out / 'summary.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps([row for row in summaries if row['dimension'] == 'all'], ensure_ascii=False))


if __name__ == '__main__':
    main()
