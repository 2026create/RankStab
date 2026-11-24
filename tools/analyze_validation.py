"""Reaggregate frozen per-page scores for sample, protocol and bootstrap controls."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import random
from shutil import copy2
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import runner


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--result', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    entries = json.loads((args.data / 'gt/index.json').read_text(encoding='utf-8'))
    sample_rows = read_csv(args.result / 'sample_scores.csv')
    models = sorted({row['model'] for row in sample_rows})
    references = {entry['sample_id']: (args.data / 'gt' / (entry['sample_id'] + '.md')).read_text(encoding='utf-8') for entry in entries}
    predictions = {(model, sample): (args.data / 'models_out' / model / (sample + '.md')).read_text(encoding='utf-8')
                   for model in models for sample in references}
    cache = {}
    for record in sample_rows:
        row = {key: float(value) for key, value in record.items()
               if key not in ('sample_id', 'seg_mode', 'level', 'model')}
        for field in ('ref_len', 'fixed_denom', 'overflow'):
            row[field] = int(row[field])
        key = (references[record['sample_id']], predictions[record['model'], record['sample_id']],
               record['seg_mode'], record['level'], row['fixed_denom'])
        if key in cache and row != cache[key]:
            raise ValueError('Conflicting cached scores')
        cache[key] = row

    def cached_score(reference, prediction, mode, level, fixed_denom=None, **unused):
        return cache[reference, prediction, mode, level, fixed_denom].copy()

    args.out.mkdir(parents=True, exist_ok=True)
    ordering = sorted(entries, key=lambda entry: entry['sample_id'])
    random.Random(20261122).shuffle(ordering)
    groups = [('size50', ordering[:50]), ('size100', ordering[:100]), ('size200', ordering)]
    groups += [(name, [entry for entry in entries if entry['split'] == name]) for name in ('development', 'validation')]
    records = []
    with tempfile.TemporaryDirectory(prefix='rankstab_subsets_') as temporary:
        temporary = Path(temporary)
        with patch.object(runner, '_score_one', side_effect=cached_score):
            for name, selected in groups:
                gt = temporary / name
                gt.mkdir()
                # Keep original order for full-size numerical reproducibility.
                selected_ids = {entry['sample_id'] for entry in selected}
                selected = [entry for entry in entries if entry['sample_id'] in selected_ids]
                (gt / 'index.json').write_text(json.dumps(selected, ensure_ascii=False), encoding='utf-8')
                for entry in selected:
                    copy2(args.data / 'gt' / (entry['sample_id'] + '.md'), gt)
                configurations = [(2000, 20260921, ['whole', 'para', 'para_merge'])]
                if name == 'size200':
                    configurations += [(count, seed, ['whole', 'para', 'para_merge'])
                                       for count in (200, 1000, 2000) for seed in (20260921, 7, 42)
                                       if (count, seed) != (2000, 20260921)]
                    configurations.append((2000, 20260921, ['whole']))
                for count, seed, modes in configurations:
                    label = '%s_boot%d_seed%d_%dgroups' % (name, count, seed, len(modes) * 6)
                    result = runner.run(str(gt), str(args.data / 'models_out'), str(args.out / label),
                                        n_boot=count, ref_seg='whole', seg_modes=modes,
                                        bootstrap_seed=seed, bootstrap_cluster_field='bootstrap_cluster',
                                        edit_cell_limit=16_000_000, model_names=models, make_html_report=False)
                    for pair in result['verdicts']:
                        records.append(dict(experiment=label, pages=len(selected), bootstrap=count,
                                            seed=seed, groups=len(modes) * 6, **pair))
                    print(label, [(pair['model_a'], pair['verdict'], pair['flipped_groups'])
                                  for pair in result['verdicts']], flush=True)
                    if name == 'size200' and count == 2000 and seed == 20260921 and len(modes) == 3:
                        expected = read_csv(args.result / 'scores.csv')
                        actual = read_csv(args.out / label / 'scores.csv')
                        if expected != actual:
                            raise AssertionError('Cached full-size aggregation differs from the main run')
    with (args.out / 'comparisons.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(records)
    metadata = dict(sample_score_sha256=hashlib.sha256((args.result / 'sample_scores.csv').read_bytes()).hexdigest(),
                    sample_order_seed=20261122, nested_sizes=[50, 100, 200],
                    resampling='Known-document clusters, unknown pages use page identifiers',
                    score_reuse='Exact saved per-page statistics; full-size aggregate equality asserted')
    (args.out / 'meta.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
