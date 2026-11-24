"""Freeze a stratified 200-page validation set and import attributed public outputs."""
import argparse
from collections import Counter, defaultdict
import hashlib
import io
import json
from pathlib import Path
import random
import re
from shutil import copy2
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.flatten import md_to_plain


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / '03_data'


def document_id(filename):
    match = re.match(r'(.+?)(?:_page_\d+|\.pdf_\d+)\.[^.]+$', filename)
    return (match.group(1), 'filename_document') if match else (filename, 'page_identifier_only')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive-parts', type=Path, nargs=3, required=True)
    args = parser.parse_args()
    archive_bytes = b''.join(path.read_bytes() for path in args.archive_parts)
    archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
    archive_path = DATA / 'model_outputs/tensorlake_omnidocbench_public.zip'
    archive_path.write_bytes(archive_bytes)
    annotations_path = DATA / 'omnidocbench/OmniDocBench.json'
    annotations = json.loads(annotations_path.read_text(encoding='utf-8'))
    old = DATA / 'omnidocbench_zh'
    old_selection = json.loads((old / 'index.json').read_text(encoding='utf-8'))['pages']
    old_entries = {entry['sample_id']: entry for entry in json.loads((old / 'gt/index.json').read_text(encoding='utf-8'))}
    available = set(archive.namelist())
    by_image = {page['page_info']['image_path']: page for page in annotations}
    records = []
    for previous in old_selection:
        member = 'results_omnidocbench/docling/%s.md' % Path(previous['image_path']).stem
        other = 'results_omnidocbench/tensorlake/%s.md' % Path(previous['image_path']).stem
        if member not in available or other not in available:
            continue
        page = by_image[previous['image_path']]
        attrs = page['page_info']['page_attribute']
        records.append({'sample_id': previous['id'], 'image_path': previous['image_path'],
                        'source': 'OmniDocBench', 'doc_type': attrs['data_source'],
                        'language': attrs['language'], 'layout': attrs['layout'],
                        'split': 'development', 'original': previous['id']})

    old_images = {record['image_path'] for record in records}
    old_documents = {document_id(name)[0] for name in old_images}
    pool = defaultdict(list)
    for page in annotations:
        info = page['page_info']
        name = info['image_path']
        if name in old_images or document_id(name)[0] in old_documents:
            continue
        blocks = [block for block in page['layout_dets'] if not block.get('ignore')]
        if any(block.get('category_type') in ('table', 'equation_isolated') for block in blocks):
            continue
        text_blocks = [block for block in blocks if block.get('text')]
        text_blocks.sort(key=lambda block: (block.get('order') is None, block.get('order') or 0))
        text = '\n'.join(block['text'] for block in text_blocks)
        if not 100 <= len(text) <= 1600 or '$' in text or '\\(' in text:
            continue
        public_paths = ['results_omnidocbench/%s/%s.md' % (model, Path(name).stem)
                        for model in ('docling', 'tensorlake')]
        if any(path not in available for path in public_paths):
            continue
        attrs = info['page_attribute']
        key = (attrs['language'], attrs['layout'], attrs['data_source'])
        pool[key].append((page, text))
    rng = random.Random(20261122)
    for pages in pool.values():
        rng.shuffle(pages)
    keys = sorted(pool)
    rng.shuffle(keys)
    selected_documents = set(old_documents)
    while len(records) < 200 and any(pool.values()):
        for key in keys:
            if not pool[key] or len(records) == 200:
                continue
            page, text = pool[key].pop()
            info = page['page_info']
            name = info['image_path']
            doc_id, status = document_id(name)
            if doc_id in selected_documents:
                continue
            selected_documents.add(doc_id)
            records.append({'sample_id': 'val_%03d' % (len(records) + 1), 'image_path': name,
                            'source': 'OmniDocBench', 'doc_type': key[2], 'language': key[0],
                            'layout': key[1], 'split': 'validation', 'text': text})
    if len(records) != 200:
        raise ValueError('Eligible pages after frozen filters: %d' % len(records))
    destination = DATA / 'validation200'
    (destination / 'gt').mkdir(parents=True, exist_ok=True)
    (destination / 'images').mkdir(exist_ok=True)
    missing_images = []
    for record in records:
        sample = record['sample_id']
        name = record['image_path']
        record['document_id'], record['document_id_status'] = document_id(name)
        if record.get('original'):
            text = (old / 'gt' / (sample + '.md')).read_text(encoding='utf-8')
            copy2(old / 'images' / (sample + '.png'), destination / 'images' / (sample + '.png'))
            for model in ('ppocr-v6', 'cnocr', 'easyocr', 'windows_ocr'):
                folder = destination / 'models_out' / model
                folder.mkdir(parents=True, exist_ok=True)
                copy2(old / 'models_out' / model / (sample + '.md'), folder / (sample + '.md'))
        else:
            text = record.pop('text')
            missing_images.append({'sample_id': sample,
                                   'url': 'https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/main/images/' + name})
        (destination / 'gt' / (sample + '.md')).write_text(text, encoding='utf-8')
        record['gt_sha256'] = hashlib.sha256(text.encode('utf-8')).hexdigest()
        record['characters'] = len(text)
        for model in ('docling', 'tensorlake'):
            member = 'results_omnidocbench/%s/%s.md' % (model, Path(name).stem)
            raw = archive.read(member).decode('utf-8-sig')
            raw_folder = destination / 'public_raw' / model
            converted_folder = destination / 'models_out' / (model + '_public')
            raw_folder.mkdir(parents=True, exist_ok=True)
            converted_folder.mkdir(parents=True, exist_ok=True)
            (raw_folder / (sample + '.md')).write_text(raw, encoding='utf-8')
            (converted_folder / (sample + '.md')).write_text(md_to_plain(raw), encoding='utf-8')
    for model in ('docling', 'tensorlake'):
        manifest = {'model_name': model + '_public', 'n_samples': len(records),
                    'source': 'https://huggingface.co/datasets/tensorlake/OmniDocBench-eval-outputs',
                    'archive_sha256': hashlib.sha256(archive_bytes).hexdigest(),
                    'source_role': 'public saved outputs, reproduced scoring locally',
                    'producer_model_version': 'not declared by publisher',
                    'conversion': 'src.flatten.md_to_plain, raw files retained in public_raw'}
        (destination / 'models_out' / (model + '_public') / '_manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    (destination / 'gt/index.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    (destination / 'image_downloads.json').write_text(json.dumps(missing_images, ensure_ascii=True), encoding='utf-8')
    summary = {'n_samples': len(records), 'seed': 20261122,
               'annotation_sha256': hashlib.sha256(annotations_path.read_bytes()).hexdigest(),
               'languages': dict(Counter(record['language'] for record in records)),
               'layouts': dict(Counter(record['layout'] for record in records)),
               'document_types': dict(Counter(record['doc_type'] for record in records)),
               'split': dict(Counter(record['split'] for record in records)),
               'document_id_status': dict(Counter(record['document_id_status'] for record in records)),
               'filters': 'Retain 38 baseline pages with public outputs; add 162 pages without tables/display equations, 100-1600 characters, one page per inferred original document; round-robin language/layout/document-type strata. Original 50-page baseline retained separately.',
               'upstream_dataset': 'https://huggingface.co/datasets/opendatalab/OmniDocBench',
               'public_output_archive_sha256': hashlib.sha256(archive_bytes).hexdigest()}
    (destination / 'selection.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
