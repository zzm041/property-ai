"""Business metric denominators and provenance safeguards."""
import importlib.util
import hashlib
import json
from pathlib import Path
import sys

import pytest

MODULE = Path(__file__).resolve().parents[1] / 'business_metrics.py'
spec = importlib.util.spec_from_file_location('business_metrics', MODULE)
metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics)


def dataset():
    return {'cohort': 'regression', 'label_origin': 'test_fixture',
            'review_status': 'not_independently_reviewed', 'cases': [
                {'id': 'a', 'expected_decision': 'READY', 'expected_fields': {'duty': 'CLEANING'}},
                {'id': 'b', 'expected_decision': 'CLARIFY'},
                {'id': 'c', 'expected_decision': 'MANUAL'}]}


def run(data, rows=(), kind='real_model'):
    return {'kind': kind, 'dataset_sha256': metrics.fingerprint(data),
            'model_calls': len(rows) if kind == 'real_model' else 0,
            'versions': {'model': 'fixture', 'prompt': 'fixture', 'source': 'fixture'},
            'cases': list(rows)}


def test_errors_and_missing_samples_remain_in_denominator():
    data = dataset()
    result = metrics.summarize(data, run(data, [
        {'id': 'a', 'status': 'ok', 'decision': 'MANUAL'},
        {'id': 'b', 'status': 'ok', 'decision': 'READY'}]))
    assert result['coverage'] == {'numerator': 2, 'denominator': 3, 'rate': 2 / 3}
    assert result['false_rejection']['rate'] == 1
    assert result['false_release'] == {'numerator': 1, 'denominator': 2, 'rate': .5}
    assert result['decision_agreement']['denominator'] == 3
    assert result['field_agreement']['rate'] == 0
    assert result['missing_ids'] == ['c']
    assert result['human_field_edit_rate']['rate'] is None


def test_provider_failure_is_not_false_rejection_or_success():
    data = dataset()
    result = metrics.summarize(data, run(data, [{'id': 'a', 'status': 'provider_error'}]))
    assert result['provider_errors'] == 1
    assert result['false_rejection']['numerator'] == 0
    assert result['completed_decision_agreement']['rate'] is None


def test_latency_and_human_fields_have_separate_observed_denominators():
    data = dataset()
    rows = [{'id': 'a', 'status': 'ok', 'decision': 'READY',
             'fields': {'duty': 'CLEANING'}, 'latency_seconds': 4,
             'human_review': {'checked_fields': 5, 'changed_fields': 2}},
            {'id': 'b', 'status': 'ok', 'decision': 'CLARIFY', 'latency_seconds': 8}]
    result = metrics.summarize(data, run(data, rows))
    assert result['human_field_edit_rate'] == {'numerator': 2, 'denominator': 5, 'rate': .4}
    assert result['latency_seconds'] == {'observed': 2, 'median': 6.0, 'p95': 8}
    assert result['field_agreement']['rate'] == 1
    assert result['decision_agreement']['rate'] == 2 / 3


@pytest.mark.parametrize('mutation', ['duplicate', 'unknown', 'hash', 'nan', 'negative', 'bad_decision', 'bad_edit', 'failed_decision'])
def test_malformed_evidence_is_rejected(mutation):
    data = dataset()
    row = {'id': 'a', 'status': 'ok', 'decision': 'READY'}
    record = run(data, [row])
    if mutation == 'duplicate': record['cases'].append(dict(row))
    if mutation == 'unknown': row['id'] = 'unknown'
    if mutation == 'hash': record['dataset_sha256'] = '0' * 64
    if mutation == 'nan': row['latency_seconds'] = float('nan')
    if mutation == 'negative': row['latency_seconds'] = -1
    if mutation == 'bad_decision': row['decision'] = 'DISPATCH'
    if mutation == 'bad_edit': row['human_review'] = {'checked_fields': 1, 'changed_fields': 2}
    if mutation == 'failed_decision': row['status'] = 'provider_error'
    with pytest.raises(ValueError): metrics.summarize(data, record)


def test_replay_does_not_claim_model_latency_or_new_calls():
    data = dataset()
    record = run(data, [{'id': 'a', 'status': 'ok', 'decision': 'READY', 'latency_seconds': 1}], 'saved_output_replay')
    assert metrics.summarize(data, record)['latency_seconds']['observed'] == 0
    record['model_calls'] = 1
    with pytest.raises(ValueError): metrics.summarize(data, record)


def test_empty_dataset_does_not_create_perfect_accuracy():
    data = dataset(); data['cases'] = []
    result = metrics.summarize(data, run(data))
    assert result['decision_agreement']['rate'] is None
    assert result['false_release']['rate'] is None


def test_duplicate_labels_and_boolean_counts_rejected():
    data = dataset(); data['cases'].append(dict(data['cases'][0]))
    with pytest.raises(ValueError): metrics.summarize(data, run(data))
    data = dataset(); record = run(data); record['model_calls'] = True
    with pytest.raises(ValueError): metrics.summarize(data, record)


def test_cli_binds_exact_bytes_once_and_refuses_overwrite(tmp_path, monkeypatch):
    data = dataset(); record = run(data)
    dataset_file = tmp_path / 'dataset.json'; run_file = tmp_path / 'run.json'
    target = tmp_path / 'summary.json'
    raw = {dataset_file: json.dumps(data).encode(), run_file: json.dumps(record).encode()}
    for path, content in raw.items(): path.write_bytes(content)
    monkeypatch.setattr(sys, 'argv', ['metrics', '--dataset', str(dataset_file),
                                     '--run', str(run_file), '--out', str(target)])
    original = Path.read_bytes; original_text = Path.read_text; reads = []
    def disallow_separate_text_read(path, *args, **kwargs):
        if path in raw: raise AssertionError('Parse the same bytes used for the digest')
        return original_text(path, *args, **kwargs)
    def read_once(path):
        if path in raw:
            if path in reads: raise AssertionError('Input reread after assessment')
            reads.append(path)
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', read_once)
    monkeypatch.setattr(Path, 'read_text', disallow_separate_text_read)
    metrics.main()
    summary = json.loads(target.read_text())
    assert set(reads) == set(raw)
    assert summary['input_files_sha256']['dataset'] == hashlib.sha256(raw[dataset_file]).hexdigest()
    assert summary['input_files_sha256']['run'] == hashlib.sha256(raw[run_file]).hexdigest()
    before = original(target); reads.clear()
    with pytest.raises(FileExistsError): metrics.main()
    assert original(target) == before
