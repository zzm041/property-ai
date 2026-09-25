"""Final candidate for the authorized R3fix2 two-candidate global budget."""
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
OUT = TOTAL / 'candidate-02'
PRIVATE = ROOT / 'tmp/real-ai-integration-027/diagnostic-r3fix2'
BASE = ROOT / 'reports/auth-identity-ai-001/local-qwen'
PROMPT_VERSION = 'wo-analysis-v10-observed-event-r3fix2b'

spec = importlib.util.spec_from_file_location(
    'candidate1_runner', Path(__file__).parent / 'diagnostic-r3fix2.py'
)
c1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c1)
h = c1.h
c1.d.OUT = OUT
c1.d.PRIVATE = PRIVATE
c1.d.PROMPT_VERSION = PROMPT_VERSION
h.OUT = OUT
h.EXPECTED_PROMPT_VERSION = PROMPT_VERSION
h.__file__ = __file__


def append_file(path, value):
    if path.name == 'reservation.json':
        value = dict(value)
        value.pop('repair_rounds_used', None)
        value.update(candidate_number=2, global_request_limit=40,
                     global_window_seconds=3600, final_fixed_set=True)
    c1.old_append(path, value)


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
    return c1.old_next_number(directory, now=now)


h.next_number = next_number


def frozen():
    manifest = h.read(OUT / 'frozen.json')
    root = Path(manifest['private_frozen_root'])
    for item in manifest['files']:
        assert h.sha(root / item['path']) == item['sha256']
        assert h.sha(ROOT / item['path']) == item['sha256'], 'Candidate changed during diagnostic'
    return root, manifest


h.frozen = frozen


def plans():
    result = c1.d.old_plans()
    first = ['GOLD-007', 'GOLD-011', 'GOLD-012', 'GOLD-013', 'GOLD-014', 'GOLD-006']
    ordered = [next(row for row in result if row['case']['id'] == case_id) for case_id in first]
    ordered += [row for row in result if row['case']['id'] == 'PHOTO-003']
    assert len(ordered) == 8 and [row['kind'] for row in ordered[-2:]] == ['VLM', 'LLM']
    ordered += [row for row in result if row not in ordered]
    assert len(ordered) == 32 and len({(row['case']['id'], row['kind']) for row in ordered}) == 32
    return ordered


h.plans = plans
h.prepare_images = c1.d.images
h.raw_contract = c1.d.contract


def freeze():
    assert not OUT.exists(), 'Candidate-02 must be resumed, never reset'
    first = TOTAL / 'candidate-01'
    assert (first / 'stop.json').exists()
    first_reservations = sorted(first.glob('request-*/reservation.json'))
    assert len(first_reservations) <= 8 and len(first_reservations) == len(list(first.glob('request-*/response.json')))
    assert len(global_reservations()) == len(first_reservations) < 8
    assert remaining_seconds(h.read(first_reservations[0])['reserved_unix'], time.time()) > 0

    target = PRIVATE / 'frozen-candidate-02'
    target.mkdir(parents=True, exist_ok=False)
    names = [item['path'] for item in h.read(BASE / 'crossfield-diagnostic-01/frozen-inputs-v2.json')['files']]
    names += [
        'backend/app/domain/observed_event.py',
        'reports/real-ai-integration-027/diagnostic-r3fix2-candidate2.py',
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

    OUT.mkdir()
    append_file(OUT / 'frozen.json', {
        'base_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'private_frozen_root': str(target), 'files': entries,
        'candidate_number': 2, 'maximum_candidates': 2,
        'candidate_01_stop_sha256': h.sha(first / 'stop.json'),
        'candidate_01_requests_used': len(first_reservations),
        'candidate_01_results_not_reused': True,
        'protocol': 'observed-event-v1', 'prompt_version': PROMPT_VERSION,
        'model': h.MODEL, 'model_digest': h.DIGEST,
        'parameters': {'context_length': 4096, 'temperature': 0, 'thinking': False,
                       'llm_max_tokens': 512, 'vlm_max_tokens': 256, 'per_request_seconds': 60},
        'global_budget': {'requests': 40, 'seconds': 3600,
                          'starts_at_unix': h.read(first_reservations[0])['reserved_unix']},
        'candidate_fixed_set_requests': 32,
        'only_correction': (
            'Adds the same quote-local subject requirement to the schema field description and generic examples. '
            'No enum, category, risk, sample, model or runtime parameter changed.'
        ),
    })
    runtime.update(verified_at_utc=datetime.now(UTC).isoformat(), inference_calls_this_check=0)
    append_file(OUT / 'runtime.json', runtime)
    append_file(OUT / 'images.json', h.read(BASE / 'crossfield-diagnostic-01/images.json'))
    c1.d.images()
    print('CANDIDATE-02 FROZEN; zero additional inference requests')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['freeze', 'next', 'child', 'judge'])
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
    else:
        c1.d.judge(args.number, args.decision, args.note)
