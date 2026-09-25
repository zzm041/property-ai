"""Authorized R3fix2 candidate-01 screening and fixed-set diagnostic.

One global 40-request/3600-second budget.  Candidate-01 either continues from
the predeclared eight-request screen into the same 32-request fixed set or is
stopped before one offline correction.  Historical runs are never reused.
"""
import argparse
from datetime import UTC, datetime
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
TOTAL = Path(__file__).parent / 'diagnostic-r3fix2'
OUT = TOTAL / 'candidate-01'
PRIVATE = ROOT / 'tmp/real-ai-integration-027/diagnostic-r3fix2'
BASE = ROOT / 'reports/auth-identity-ai-001/local-qwen'
PROMPT_VERSION = 'wo-analysis-v10-observed-event-r3fix2'

spec = importlib.util.spec_from_file_location(
    'prior_r3_runner', Path(__file__).parent / 'diagnostic-r3fix1.py'
)
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
h = d.h
d.OUT = OUT
d.PRIVATE = PRIVATE
d.PROMPT_VERSION = PROMPT_VERSION
h.OUT = OUT
h.EXPECTED_PROMPT_VERSION = PROMPT_VERSION
h.__file__ = __file__

old_append = h.append_file
old_next_number = h.next_number


def append_file(path, value):
    if path.name == 'reservation.json':
        value = dict(value)
        value.pop('repair_rounds_used', None)
        value.update(candidate_number=1, global_request_limit=40,
                     global_window_seconds=3600, screening_limit=8)
    old_append(path, value)


h.append_file = append_file


def global_reservations():
    return sorted(TOTAL.glob('candidate-*/request-*/reservation.json'))


def remaining_seconds(_started, now):
    rows = global_reservations()
    started = h.read(rows[0])['reserved_unix'] if rows else _started
    return max(0.0, 3600 - (now - started))


h.remaining_seconds = remaining_seconds


def next_number(directory, *, now=None):
    now = time.time() if now is None else now
    if len(global_reservations()) >= 40:
        raise RuntimeError('GLOBAL_REQUEST_LIMIT')
    number = old_next_number(directory, now=now)
    if number == 9 and not (OUT / 'screening-gate.json').exists():
        raise RuntimeError('SCREENING_GATE_REQUIRED')
    return number


h.next_number = next_number


def frozen():
    manifest = h.read(OUT / 'frozen.json')
    root = Path(manifest['private_frozen_root'])
    for item in manifest['files']:
        assert h.sha(root / item['path']) == item['sha256']
        assert h.sha(ROOT / item['path']) == item['sha256'], 'Candidate changed during diagnostic'
    return root, manifest


d.frozen = frozen
h.frozen = frozen


def plans():
    result = d.old_plans()
    first = ['GOLD-007', 'GOLD-011', 'GOLD-012', 'GOLD-013', 'GOLD-014', 'GOLD-006']
    ordered = [next(row for row in result if row['case']['id'] == case_id) for case_id in first]
    ordered += [row for row in result if row['case']['id'] == 'PHOTO-003']
    assert len(ordered) == 8 and [row['kind'] for row in ordered[-2:]] == ['VLM', 'LLM']
    ordered += [row for row in result if row not in ordered]
    assert len(ordered) == 32 and len({(row['case']['id'], row['kind']) for row in ordered}) == 32
    return ordered


h.plans = plans
h.prepare_images = d.images
h.raw_contract = d.contract


def freeze():
    assert not TOTAL.exists(), 'Existing global run must be resumed, never reset'
    target = PRIVATE / 'frozen-candidate-01'
    target.mkdir(parents=True, exist_ok=False)
    names = [item['path'] for item in h.read(BASE / 'crossfield-diagnostic-01/frozen-inputs-v2.json')['files']]
    names += [
        'backend/app/domain/observed_event.py',
        'reports/real-ai-integration-027/diagnostic-r3fix2.py',
        'docs/05-AI-DISPATCH-SPEC.md',
    ]
    entries = []
    for name in sorted(set(names)):
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, destination)
        entries.append({'path': name, 'sha256': h.sha(destination)})

    runtime = h.read(BASE / 'crossfield-diagnostic-01/runtime.json')
    pid = int(runtime['pid'])
    command = (
        f"Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' | "
        "Select-Object ProcessId,ExecutablePath,@{N='created';E={$_.CreationDate.ToString('o')}} | ConvertTo-Json -Compress"
    )
    actual = json.loads(subprocess.check_output(['powershell', '-NoProfile', '-Command', command], text=True))
    assert actual['ExecutablePath'] == runtime['executable']
    assert datetime.fromisoformat(actual['created']) == datetime.fromisoformat(runtime['process_created_at'])

    OUT.mkdir(parents=True)
    append_file(TOTAL / 'budget.json', {
        'maximum_requests': 40, 'maximum_seconds': 3600, 'maximum_candidates': 2,
        'window_starts_on_first_reservation': True,
        'historical_allowances_not_inherited': True,
    })
    append_file(OUT / 'frozen.json', {
        'base_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'private_frozen_root': str(target), 'files': entries,
        'candidate_number': 1, 'maximum_candidates': 2,
        'protocol': 'observed-event-v1', 'prompt_version': PROMPT_VERSION,
        'model': h.MODEL, 'model_digest': h.DIGEST,
        'parameters': {'context_length': 4096, 'temperature': 0, 'thinking': False,
                       'llm_max_tokens': 512, 'vlm_max_tokens': 256, 'per_request_seconds': 60},
        'global_budget': {'requests': 40, 'seconds': 3600},
        'candidate_fixed_set_requests': 32,
        'material_fix': (
            'Each subject must be supported inside its own exact quote; otherwise use 未确定. '
            'Unknown subject preserves fact/risk/routing and includes quote in the event identity.'
        ),
    })
    append_file(OUT / 'screening-plan.json', {
        'fixed_before_inference': True,
        'maximum_requests': 8,
        'ordered_requests': [
            {'number': i + 1, 'case': row['case']['id'], 'kind': row['kind']}
            for i, row in enumerate(plans()[:8])
        ],
        'purpose': 'prior subject failure, insufficient/conflicting/unknown evidence, multi-subject, security, and image-text subject binding',
    })
    runtime.update(verified_at_utc=datetime.now(UTC).isoformat(), inference_calls_this_check=0)
    append_file(OUT / 'runtime.json', runtime)
    append_file(OUT / 'images.json', h.read(BASE / 'crossfield-diagnostic-01/images.json'))
    d.images()
    print('CANDIDATE-01 FROZEN; zero inference requests')


def screening_pass():
    rows = sorted(OUT.glob('request-*/judgement.json'))
    assert len(rows) == 8 and len(global_reservations()) == 8
    for path in rows:
        judgement = h.read(path)
        assert judgement['continue'] and judgement['response_sha256'] == h.sha(path.parent / 'response.json')
    append_file(OUT / 'screening-gate.json', {
        'status': 'PASS_CONTINUE_SAME_CANDIDATE',
        'candidate_hash': h.sha(OUT / 'frozen.json'),
        'judgements': [{'path': str(path.relative_to(TOTAL)), 'sha256': h.sha(path)} for path in rows],
        'no_source_or_parameter_change': True,
    })
    print('SCREENING PASS; same candidate may continue')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['freeze', 'next', 'child', 'judge', 'screening-pass'])
    parser.add_argument('--number', type=int)
    parser.add_argument('--decision', choices=['accept', 'stop'])
    parser.add_argument('--note')
    args = parser.parse_args()
    if args.mode == 'freeze':
        freeze()
    elif args.mode == 'next':
        h.execute_next()
    elif args.mode == 'child':
        h.child(args.number)
    elif args.mode == 'judge':
        d.judge(args.number, args.decision, args.note)
    else:
        screening_pass()
