"""Offline business metrics; no provider, service, or database access."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from statistics import median

DECISIONS = {'READY', 'CLARIFY', 'MANUAL'}
KINDS = {'real_model', 'saved_output_replay', 'synthetic'}


def fingerprint(dataset: dict) -> str:
    canonical = json.dumps(dataset, ensure_ascii=False, sort_keys=True,
                           separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def ratio(numerator: int, denominator: int) -> dict:
    return {'numerator': numerator, 'denominator': denominator,
            'rate': numerator / denominator if denominator else None}


def count(value, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f'{name} must be a nonnegative integer')
    return value


def keyed(rows: list, name: str) -> dict:
    if not isinstance(rows, list):
        raise ValueError(f'{name} must be a list')
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id'].strip():
            raise ValueError(f'{name} requires nonempty string IDs')
        if row['id'] in result:
            raise ValueError(f'duplicate {name} ID')
        result[row['id']] = row
    return result


def summarize(dataset: dict, run: dict) -> dict:
    for field in ('cohort', 'label_origin', 'review_status'):
        if not isinstance(dataset.get(field), str) or not dataset[field].strip():
            raise ValueError(f'missing dataset {field}')
    digest = fingerprint(dataset)
    if run.get('dataset_sha256') != digest:
        raise ValueError('dataset fingerprint mismatch')
    kind = run.get('kind')
    if kind not in KINDS:
        raise ValueError('unknown run kind')
    calls = count(run.get('model_calls'), 'model_calls')
    if kind != 'real_model' and calls:
        raise ValueError('replay and synthetic runs cannot claim new model calls')
    versions = run.get('versions')
    if not isinstance(versions, dict) or any(
        not isinstance(versions.get(k), str) or not versions[k].strip()
        for k in ('model', 'prompt', 'source')
    ):
        raise ValueError('model, prompt and source version labels required')
    labels = keyed(dataset.get('cases'), 'dataset')
    outputs = keyed(run.get('cases'), 'run')
    if outputs.keys() - labels.keys():
        raise ValueError('unknown run IDs')
    if kind == 'real_model' and calls < len(outputs):
        raise ValueError('recorded attempts cannot exceed model calls')

    ready = restricted = correct = completed = rejected = released = errors = 0
    field_total = field_correct = checked = changed = reviewed = 0
    latencies = []
    confusion = {d: {p: 0 for p in sorted(DECISIONS)} for d in sorted(DECISIONS)}
    for case_id, label in labels.items():
        expected = label.get('expected_decision')
        if expected not in DECISIONS:
            raise ValueError('unknown expected decision')
        fields = label.get('expected_fields', {})
        if not isinstance(fields, dict):
            raise ValueError('expected_fields must be an object')
        field_total += len(fields)
        ready += expected == 'READY'
        restricted += expected != 'READY'
        row = outputs.get(case_id)
        if row is None:
            continue
        status = row.get('status')
        actual = row.get('decision')
        if status not in {'ok', 'provider_error'}:
            raise ValueError('unknown record status')
        if status == 'ok' and actual not in DECISIONS:
            raise ValueError('successful records require a known decision')
        if status == 'provider_error' and (actual is not None or row.get('fields')):
            raise ValueError('failed provider cannot have a successful decision or fields')
        predicted = row.get('fields', {})
        if not isinstance(predicted, dict):
            raise ValueError('fields must be an object')
        seconds = row.get('latency_seconds')
        if seconds is not None:
            if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
                raise ValueError('latency must be a finite nonnegative number')
            if kind == 'real_model':
                latencies.append(seconds)
        review = row.get('human_review')
        if review is not None:
            if not isinstance(review, dict):
                raise ValueError('human_review must be an object')
            inspected = count(review.get('checked_fields'), 'checked_fields')
            edited = count(review.get('changed_fields'), 'changed_fields')
            if edited > inspected:
                raise ValueError('changed_fields exceeds checked_fields')
            reviewed += 1
            checked += inspected
            changed += edited
        if status == 'provider_error':
            errors += 1
            continue
        completed += 1
        correct += expected == actual
        confusion[expected][actual] += 1
        rejected += expected == 'READY' and actual != 'READY'
        released += expected != 'READY' and actual == 'READY'
        field_correct += sum(k in predicted and predicted[k] == v for k, v in fields.items())

    latencies.sort()
    return {
        'schema_version': 1, 'kind': kind, 'dataset_sha256': digest,
        'cohort': dataset['cohort'], 'label_origin': dataset['label_origin'],
        'review_status': dataset['review_status'], 'versions': versions,
        'model_calls': calls, 'total_cases': len(labels), 'completed_cases': completed,
        'missing_ids': sorted(labels.keys() - outputs.keys()), 'provider_errors': errors,
        'coverage': ratio(len(outputs), len(labels)),
        'decision_agreement': ratio(correct, len(labels)),
        'completed_decision_agreement': ratio(correct, completed),
        'false_rejection': ratio(rejected, ready), 'false_release': ratio(released, restricted),
        'field_agreement': ratio(field_correct, field_total), 'confusion': confusion,
        'human_reviewed_cases': reviewed, 'human_field_edit_rate': ratio(changed, checked),
        'latency_seconds': {'observed': len(latencies),
                            'median': median(latencies) if latencies else None,
                            'p95': latencies[math.ceil(.95 * len(latencies)) - 1] if latencies else None},
        'limitations': ['Labels and provenance require independent verification.',
                        'READY describes usable structure, not assignment authorization.',
                        'No population accuracy or labor savings inferred.',
                        'Provider latency includes failed attempts when recorded; replay latency excluded.'],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    dataset_bytes = args.dataset.read_bytes()
    run_bytes = args.run.read_bytes()
    result = summarize(json.loads(dataset_bytes), json.loads(run_bytes))
    result['input_files_sha256'] = {
        'dataset': hashlib.sha256(dataset_bytes).hexdigest(),
        'run': hashlib.sha256(run_bytes).hexdigest(),
    }
    with args.out.open('x', encoding='utf-8') as output:
        json.dump(result, output, ensure_ascii=False, indent=2, allow_nan=False)
        output.write('\n')


if __name__ == '__main__':
    main()
