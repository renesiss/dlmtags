#!/usr/bin/env python3
"""Score labeled, read-only Picard and DLMtags scan exports.

The runner is deliberately separate from the applications: every run must use
the same immutable audio inputs and record its own version and request counts.
No audio is opened or written by this script.
"""

import argparse
from collections import Counter
import json
from pathlib import Path


SYSTEMS = ('picard_scan', 'dlmtags_smart_scan', 'dlmtags_bulk_auto_tag')
FIELDS = ('artist', 'title', 'date', 'genre')
REQUIRED_CHALLENGES = frozenset(
    {'live', 'remix', 'cover', 'compilation', 'missing_tags', 'incorrect_tags', 'malformed_filename', 'non_latin'}
)


def _normal(value):
    return ' '.join((value or '').casefold().split())


def evaluate(manifest, runs):
    """Compare the three exports against one labeled manifest.

    Each result is a proposed output, including cases left for review. A
    field-level false positive is a nonempty value when ground truth is empty.
    Wrong nonempty values for nonempty truth count as both FP and FN.
    """
    cases = manifest['cases']
    ids = {case['id'] for case in cases}
    if len(ids) != len(cases) or not cases:
        raise ValueError('Manifest needs unique labeled cases')
    challenges = {challenge for case in cases for challenge in case['challenges']}
    if not REQUIRED_CHALLENGES <= challenges:
        raise ValueError('Manifest is missing required challenge categories')
    expected_hashes = {case['id']: case['sha256'] for case in cases}
    if set(runs) != set(SYSTEMS):
        raise ValueError('All three systems are required')

    reports = {}
    for system in SYSTEMS:
        run = runs[system]
        if not run.get('version') or not isinstance(run.get('configuration'), dict):
            raise ValueError(f'{system}: version and configuration are required')
        results = run['results']
        by_id = {item['id']: item for item in results}
        if len(by_id) != len(results) or set(by_id) != ids:
            raise ValueError(f'{system}: result IDs differ from the manifest')
        counts = Counter()
        runtime_ms = 0
        requests = Counter()
        incorrect = Counter()
        for case in cases:
            result = by_id[case['id']]
            if result['sha256'] != expected_hashes[case['id']]:
                raise ValueError(f'{system}: input hash differs for {case["id"]}')
            if result['decision'] not in {'matched', 'needs_review', 'no_match', 'error'}:
                raise ValueError(f'{system}: invalid decision for {case["id"]}')
            counts['covered'] += result['decision'] == 'matched'
            counts['needs_review'] += result['decision'] == 'needs_review'
            runtime_ms += result['runtime_ms']
            requests.update(result['provider_requests'])
            for field in FIELDS:
                truth = _normal(case['truth'].get(field))
                output = _normal(result['tags'].get(field)) if result['decision'] == 'matched' else ''
                if truth and output == truth:
                    counts['tp'] += 1
                elif truth:
                    counts['fn'] += 1
                    if output:
                        counts['fp'] += 1
                        if field in {'artist', 'title', 'date'}:
                            incorrect[field] += 1
                elif output:
                    counts['fp'] += 1
                    counts['negative_fp'] += 1
                else:
                    counts['tn'] += 1
        tp, fp, fn, tn = (counts[key] for key in ('tp', 'fp', 'fn', 'tn'))
        reports[system] = {
            'version': run['version'],
            'configuration': run['configuration'],
            'precision': tp / (tp + fp) if tp + fp else None,
            'recall': tp / (tp + fn) if tp + fn else None,
            'coverage': counts['covered'] / len(cases),
            'false_positive_rate': counts['negative_fp'] / (counts['negative_fp'] + tn)
            if counts['negative_fp'] + tn
            else None,
            'needs_review_rate': counts['needs_review'] / len(cases),
            'incorrect_artist_title_year': dict(incorrect),
            'runtime_ms': runtime_ms,
            'provider_requests': dict(requests),
        }
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('picard_scan', type=Path)
    parser.add_argument('dlmtags_smart_scan', type=Path)
    parser.add_argument('dlmtags_bulk_auto_tag', type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    runs = {system: json.loads(getattr(args, system).read_text(encoding='utf-8')) for system in SYSTEMS}
    print(json.dumps(evaluate(manifest, runs), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
