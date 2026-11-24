"""Compare the upstream normalized-cost assignment with our monotonic alignment."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys

import Levenshtein
import numpy as np
from scipy.optimize import linear_sum_assignment

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.segmatch import align


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    tree = ast.parse(args.source.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name == 'compute_edit_distance_matrix_new']
    namespace = dict(np=np, Levenshtein=Levenshtein)
    exec(compile(tree, str(args.source), 'exec'), namespace)
    cases = [('same_order', ['甲乙', '丙丁'], ['甲乙', '丙丁']),
             ('extra_line', ['甲乙', '丙丁'], ['甲乙', '额外', '丙丁']),
             ('reordered', ['甲乙', '丙丁'], ['丙丁', '甲乙'])]
    records = []
    for name, reference, prediction in cases:
        costs = namespace['compute_edit_distance_matrix_new'](reference, prediction)
        rows, columns = linear_sum_assignment(costs)
        assigned = [(reference[i], prediction[j]) for i, j in zip(rows, columns)]
        unmatched_ref = [text for i, text in enumerate(reference) if i not in rows]
        unmatched_pred = [text for i, text in enumerate(prediction) if i not in columns]
        upstream_cost = sum(Levenshtein.distance(a, b) for a, b in assigned)
        upstream_cost += sum(map(len, unmatched_ref + unmatched_pred))
        pairs, left_ref, left_pred = align(reference, prediction)
        monotonic_cost = sum(Levenshtein.distance(a, b) for a, b in pairs)
        monotonic_cost += sum(map(len, left_ref + left_pred))
        records.append(dict(case=name, reference=reference, prediction=prediction,
                            upstream_hungarian_pairs=assigned, upstream_character_error=upstream_cost,
                            monotonic_pairs=pairs, monotonic_character_error=monotonic_cost,
                            same_error=upstream_cost == monotonic_cost))
    result = dict(source_sha256=hashlib.sha256(args.source.read_bytes()).hexdigest(),
                  scope='Upstream cost-matrix function and scipy assignment on explicit text blocks',
                  preprocessing='Identical literal blocks, table/formula preprocessing excluded',
                  cases=records)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print([(row['case'], row['upstream_character_error'], row['monotonic_character_error']) for row in records])


if __name__ == '__main__':
    main()
