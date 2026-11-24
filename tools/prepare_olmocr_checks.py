"""Freeze 50 externally annotated PDF pages for fact-based OCR checks."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random


ROOT = Path(__file__).resolve().parents[2]


def main():
    folder = ROOT / '03_data/olmocr50'
    groups = defaultdict(list)
    annotation_hashes = {}
    for path in sorted((folder / 'annotations').glob('*.jsonl')):
        annotation_hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        for line in path.read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            if row['type'] in ('present', 'absent', 'order'):
                groups[row['pdf']].append(row)
    candidates = defaultdict(list)
    for name, tests in groups.items():
        candidates[name.split('/')[0]].append(name)
    rng = random.Random(20261122)
    for values in candidates.values():
        values.sort()
        rng.shuffle(values)
    quotas = {'multi_column': 15, 'old_scans': 15, 'headers_footers': 10, 'long_tiny_text': 10}
    selected = []
    checks = []
    downloads = []
    for category, quota in quotas.items():
        for name in candidates[category][:quota]:
            sample = 'olm_%03d' % (len(selected) + 1)
            selected.append({'sample_id': sample, 'pdf': name, 'category': category,
                             'source': 'https://huggingface.co/datasets/allenai/olmOCR-bench'})
            checks.extend(dict(row, sample_id=sample) for row in groups[name])
            downloads.append({'sample_id': sample,
                              'url': 'https://huggingface.co/datasets/allenai/olmOCR-bench/resolve/main/bench_data/pdfs/' + name})
    (folder / 'index.json').write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding='utf-8')
    (folder / 'checks.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')
    (folder / 'pdf_downloads.json').write_text(json.dumps(downloads), encoding='utf-8')
    summary = {'seed': 20261122, 'n_pages': len(selected), 'n_checks': len(checks),
               'categories': quotas, 'check_types': dict(Counter(row['type'] for row in checks)),
               'annotation_sha256': annotation_hashes,
               'sampling': 'Fixed seed, sample pages within four categories before model inference; use all text/order checks for each selected page.'}
    (folder / 'selection.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
