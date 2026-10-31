"""The benchmark rejects incomparable exports and scores labeled outcomes."""

import pytest
from scripts.eval_matching.benchmark_accuracy import (
    REQUIRED_CHALLENGES,
    SYSTEMS,
    evaluate,
)


def sample_exports():
    cases = [
        {
            'id': challenge,
            'sha256': f'hash-{challenge}',
            'challenges': [challenge],
            'truth': {'artist': 'Artist', 'title': 'Title', 'date': '2001', 'genre': ''},
        }
        for challenge in sorted(REQUIRED_CHALLENGES)
    ]
    results = [
        {
            'id': case['id'],
            'sha256': case['sha256'],
            'decision': 'matched',
            'tags': case['truth'],
            'runtime_ms': 10,
            'provider_requests': {'MusicBrainz': 1},
        }
        for case in cases
    ]
    runs = {
        name: {'version': 'test', 'configuration': {}, 'results': [dict(item) for item in results]} for name in SYSTEMS
    }
    return {'cases': cases}, runs


def test_benchmark_metrics_and_request_counts():
    manifest, runs = sample_exports()
    runs['dlmtags_bulk_auto_tag']['results'][0] = {
        **runs['dlmtags_bulk_auto_tag']['results'][0],
        'tags': {'artist': 'Wrong', 'title': 'Title', 'date': '2001', 'genre': ''},
    }
    report = evaluate(manifest, runs)
    assert report['picard_scan']['precision'] == 1
    assert report['picard_scan']['coverage'] == 1
    assert report['picard_scan']['runtime_ms'] == 80
    assert report['picard_scan']['provider_requests'] == {'MusicBrainz': 8}
    assert report['dlmtags_bulk_auto_tag']['incorrect_artist_title_year'] == {'artist': 1}
    assert report['dlmtags_bulk_auto_tag']['precision'] < 1


def test_benchmark_rejects_different_input_audio():
    manifest, runs = sample_exports()
    runs['picard_scan']['results'][0]['sha256'] = 'different'
    with pytest.raises(ValueError, match='input hash differs'):
        evaluate(manifest, runs)
