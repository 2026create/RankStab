"""Complete the six-configuration run using exact unchanged precomputed scores."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import runner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--cached', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    cached_path = args.cached / 'sample_scores.csv'
    with cached_path.open(encoding='utf-8-sig', newline='') as handle:
        cached_rows = list(csv.DictReader(handle))
    cache = {}
    for row in cached_rows:
        reference = (args.data / 'gt' / (row['sample_id'] + '.md')).read_text(encoding='utf-8')
        prediction = (args.data / 'models_out' / row['model'] / (row['sample_id'] + '.md')).read_text(encoding='utf-8')
        values = {key: float(value) for key, value in row.items()
                  if key not in ('sample_id', 'seg_mode', 'level', 'model')}
        for field in ('ref_len', 'fixed_denom', 'overflow'):
            values[field] = int(values[field])
        key = (reference, prediction, row['seg_mode'], row['level'], values['fixed_denom'])
        if key in cache and cache[key] != values:
            raise ValueError('Inconsistent cached statistics')
        cache[key] = values
    original_score = runner._score_one
    hits = 0
    misses = 0

    def score(reference, prediction, mode, level, fixed_denom=None, **options):
        nonlocal hits, misses
        key = (reference, prediction, mode, level, fixed_denom)
        if key in cache:
            hits += 1
            return cache[key].copy()
        misses += 1
        return original_score(reference, prediction, mode, level, fixed_denom, **options)

    models = ['ppocr-v6', 'cnocr', 'easyocr', 'windows_ocr', 'docling_public', 'tensorlake_public']
    with patch.object(runner, '_score_one', side_effect=score):
        result = runner.run(str(args.data / 'gt'), str(args.data / 'models_out'), str(args.out),
                            n_boot=2000, ref_seg='whole', model_names=models,
                            bootstrap_seed=20260921, bootstrap_cluster_field='bootstrap_cluster',
                            edit_cell_limit=16_000_000, make_html_report=True)
    source = dict(cache_sha256=hashlib.sha256(cached_path.read_bytes()).hexdigest(),
                  exact_cache_hits=hits, exact_new_scores=misses,
                  cache_key='Entire reference and prediction text, segmentation, level, denominator',
                  configurations=models)
    (args.out / 'execution.json').write_text(json.dumps(source, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result['verdicts'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
