"""One authorized R3 fix diagnostic; old R3 records remain immutable."""
import argparse
from datetime import UTC, datetime
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'reports/auth-identity-ai-001/local-qwen'
OUT = Path(__file__).parent / 'diagnostic-r3fix1'
PRIVATE = ROOT / 'tmp/real-ai-integration-027/diagnostic-r3fix1'
PROMPT_VERSION = 'wo-analysis-v10-observed-event-r3fix1'

spec = importlib.util.spec_from_file_location('bounded_r3fix_collector', BASE / 'run-approved-supplement.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
h.OUT = OUT
h.EXPECTED_PROMPT_VERSION = PROMPT_VERSION
h.__file__ = __file__
old_plans = h.plans
old_contract = h.raw_contract


def frozen():
    manifest = h.read(OUT / 'frozen.json')
    root = Path(manifest['private_frozen_root'])
    for item in manifest['files']:
        assert h.sha(root / item['path']) == item['sha256']
        assert h.sha(ROOT / item['path']) == item['sha256'], 'Candidate changed during diagnostic'
    return root, manifest


h.frozen = frozen


def plans():
    result = old_plans()
    # Previously failed and critical boundaries first; the remaining fixed set
    # stays unchanged and is neither resampled nor reordered after inference.
    early = ['GOLD-007', 'GOLD-006', 'GOLD-005', 'GOLD-009', 'GOLD-010', 'GOLD-011']
    return [next(row for row in result if row['case']['id'] == case_id) for case_id in early] + [
        row for row in result if row['case']['id'] not in early
    ]


h.plans = plans


def images():
    for item in h.read(OUT / 'images.json'):
        assert h.sha(Path(item['copy'])) == item['copy_sha256']
    return True


h.prepare_images = images


def contract(raw, kind):
    if kind == 'VLM':
        return old_contract(raw, kind)
    from app.domain.observed_event import ObservedAnalysisOutput
    try:
        response = json.loads(raw)
        choice = response['choices'][0]
        value = json.loads(choice['message']['content'])
        ObservedAnalysisOutput.model_validate(value)
        legacy = {key: val for key, val in value.items() if key != 'observations'}
        legacy['subcategory'] = 'validation-only'
        choice['message']['content'] = json.dumps(legacy, ensure_ascii=False)
        return old_contract(json.dumps(response), kind)
    except (ValueError, KeyError, TypeError, IndexError):
        return ['OBSERVATION_PROTOCOL_INVALID']


h.raw_contract = contract


def freeze():
    assert not OUT.exists(), 'Existing run must be resumed, never reset'
    PRIVATE.mkdir(parents=True, exist_ok=True)
    target = PRIVATE / 'frozen'
    target.mkdir()
    names = [item['path'] for item in h.read(BASE / 'crossfield-diagnostic-01/frozen-inputs-v2.json')['files']]
    names += [
        'backend/app/domain/observed_event.py',
        'reports/real-ai-integration-027/diagnostic-r3fix1.py',
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
    h.append_file(OUT / 'frozen.json', {
        'base_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'private_frozen_root': str(target),
        'files': entries,
        'protocol': 'observed-event-v1',
        'prompt_version': PROMPT_VERSION,
        'budget': {'requests': 32, 'seconds': 1800, 'per_request_seconds': 60},
        'material_fix': (
            'Adds exact observable labels for scorched discoloration and burning smell, narrows spark/burn evidence, '
            'and explicitly prevents mapping those facts to peeling or gas smell. Old request-03 remains rejected.'
        ),
        'rubric': (
            'Current facts, risk, Chinese summary, complete issues and repair images remain strict; '
            'ordinary maintenance stays unified; raw outputs are immutable.'
        ),
        'prior_rounds': 'Historical 3/3 and NOT_READY remain untouched; this is the one newly authorized fixed run.',
    })
    runtime.update(verified_at_utc=datetime.now(UTC).isoformat(), inference_calls_this_check=0)
    h.append_file(OUT / 'runtime.json', runtime)
    h.append_file(OUT / 'images.json', h.read(BASE / 'crossfield-diagnostic-01/images.json'))
    images()
    print('FROZEN; zero inference requests')


def judge(number, decision, note):
    from app.domain.ai import WorkOrderAnalysisOutput, map_analysis_output
    from app.domain.observed_event import ObservedAnalysisOutput, bind_observations
    from dataclasses import asdict

    folder = OUT / f'request-{number:02d}'
    response = h.read(folder / 'response.json')
    mapping = None
    if decision == 'accept':
        assert not response['contract_errors'] and not (OUT / 'stop.json').exists()
        if response['kind'] == 'LLM':
            value = response['provider_result'][0]
            sources = {
                'report': response['case'].get('report', response['case'].get('paired_report')),
                'asr': '',
                'vision': '',
            }
            if response['case']['id'].startswith('PHOTO'):
                sources['vision'] = h.read(OUT / f'request-{number - 1:02d}' / 'response.json')['provider_result'][0]
            observed = ObservedAnalysisOutput.model_validate(value)
            key, trace = bind_observations(observed, sources)
            mapped = map_analysis_output(WorkOrderAnalysisOutput.model_validate({
                **observed.model_dump(exclude={'observations'}), 'subcategory': key,
            }))
            mapping = {'trace': trace, 'mapped': asdict(mapped)}
    h.append_file(folder / 'judgement.json', {
        'continue': decision == 'accept',
        'source': 'main inspected original input/output; independent review pending',
        'response_sha256': h.sha(folder / 'response.json'),
        'note': note,
        'raw_vs_effective': mapping,
    })
    if decision == 'stop' and not (OUT / 'stop.json').exists():
        h.append_file(OUT / 'stop.json', {
            'reason': 'CRITICAL_SEMANTIC_OR_EVIDENCE_ERROR',
            'number': number,
            'status': 'NOT_READY',
            'note': note,
        })


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
        judge(args.number, args.decision, args.note)
