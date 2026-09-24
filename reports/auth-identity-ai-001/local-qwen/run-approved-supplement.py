"""One approved supplementary diagnostic. Single-request child; append-only budget.

Not a business runner. No database, queue, model download or legacy-round write.
Use next -> inspect saved original -> record main-review judgement -> next.
"""
from datetime import UTC, datetime
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import runpy
import site
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[3]
BASE = Path(__file__).parent
OUT = BASE / 'supplement-024-rubric-01'
MODEL = 'qwen3.5:4b'
DIGEST = '2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd'
site.addsitedir(str(ROOT / '.venv/Lib/site-packages'))
sys.path.insert(0, str(ROOT / 'backend'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def append_file(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)
        stream.flush(); os.fsync(stream.fileno())


def frozen():
    manifest = read(OUT / 'frozen-inputs.json')
    root = Path(manifest['private_frozen_root'])
    for item in manifest['files']:
        if sha(root / item['path']) != item['sha256']:
            raise RuntimeError('FROZEN_HASH_MISMATCH')
        # analysis is deliberately frozen separately while026 edits its business lifecycle.
        if item['path'].startswith('backend/app/') and not item['path'].endswith('service/analysis.py'):
            if sha(ROOT / item['path']) != item['sha256']:
                raise RuntimeError('PROVIDER_OR_SCHEMA_CHANGED')
    return root, manifest


def plans():
    root, _ = frozen()
    prefix = root / 'reports/auth-identity-ai-001/local-qwen'
    gold, photos = read(prefix / 'gold-set-24.json'), read(prefix / 'repair-image-gold.json')
    result = [{'case': c, 'kind': 'LLM'} for c in gold['cases']]
    for case in photos['cases']:
        result += [{'case': case, 'kind': 'VLM'}, {'case': case, 'kind': 'LLM'}]
    assert len(result) == 32
    return result


def prepare_images():
    try:
        from PIL import Image, __version__
    except ModuleNotFoundError:
        if not (OUT / 'image-dependency-blocked.json').exists():
            append_file(OUT / 'image-dependency-blocked.json', {
                'status': 'IMAGE_BRANCH_BLOCKED', 'dependency': 'Pillow not installed in explicitly reused project dependencies',
                'installed_or_upgraded': False, 'image_transformations': 0,
                'action': 'Only text stages may continue; no alternative image library or installation authorized'})
        return False
    root, _ = frozen()
    target = OUT / 'images.json'
    if target.exists():
        for item in read(target):
            if sha(Path(item['copy'])) != item['copy_sha256']:
                raise RuntimeError('DERIVED_IMAGE_CHANGED')
        return True
    private = root.parent / 'images1024'
    private.mkdir(exist_ok=False)
    rows = []
    for item in read(root / 'reports/auth-identity-ai-001/local-qwen/repair-image-gold.json')['cases']:
        original = root / 'reports/auth-identity-ai-001/local-qwen/repair-images' / item['file']
        destination = private / (Path(item['file']).stem + '.png')
        with Image.open(original) as image:
            original_size = image.size
            image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            image.save(destination, format='PNG')
            rows.append({'id': item['id'], 'original_sha256': sha(original), 'original_size': original_size,
                         'copy': str(destination), 'copy_sha256': sha(destination), 'copy_size': image.size,
                         'mime': 'image/png', 'pillow': __version__, 'method': 'aspect-preserving thumbnail LANCZOS; no upscale/crop/retouch'})
    append_file(target, rows)
    return True


def raw_contract(raw, kind):
    from app.domain.ai import _CATEGORY_ALIASES, _PRIORITY_ALIASES, _SEVERITY_ALIASES, AiRisk
    errors = []
    try:
        response = json.loads(raw)
        choice = response['choices'][0]
        value = json.loads(choice['message']['content'])
        if choice.get('finish_reason') != 'stop': errors.append('TRUNCATED_OR_NON_STOP')
        fields = {'description', 'labels', 'confidence'} if kind == 'VLM' else {
            'category', 'subcategory', 'severity', 'priority', 'summary', 'risk', 'required_role', 'required_skill', 'confidence'}
        if not isinstance(value, dict) or set(value) != fields:
            return ['EXACT_FIELDS_REQUIRED']
        confidence = value['confidence']
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            errors.append('INVALID_CONFIDENCE')
        for name in fields - {'confidence', 'labels'}:
            if not isinstance(value[name], str) or not value[name].strip(): errors.append('INVALID_STRING_' + name)
        if kind == 'VLM':
            if not isinstance(value['labels'], list) or not all(isinstance(s, str) for s in value['labels']): errors.append('INVALID_LABELS')
        else:
            for name, allowed in [('category', _CATEGORY_ALIASES), ('severity', _SEVERITY_ALIASES), ('priority', _PRIORITY_ALIASES)]:
                if not isinstance(value[name], str) or value[name] not in allowed: errors.append('INVALID_ENUM_' + name)
            if value['risk'] not in {r.value for r in AiRisk}: errors.append('INVALID_RISK')
            if value['required_role'] not in {'STAFF', 'SUPERVISOR'}: errors.append('INVALID_ROLE')
            if not any('\u4e00' <= c <= '\u9fff' for c in value['summary']): errors.append('NON_CHINESE_SUMMARY')
    except (ValueError, KeyError, IndexError, TypeError):
        errors.append('RAW_JSON_INVALID')
    return errors


def remaining_seconds(started, now):
    return max(0., 1800 - (now - started))


def wait_single_child(process, deadline, sample_fault, *, clock=time.monotonic):
    """Only the caller-created process may be terminated; model completion unknown."""
    while process.poll() is None:
        reason = ('REQUEST_WALL_TIMEOUT_MODEL_END_UNKNOWN' if clock() >= deadline else
                  'RESOURCE_FLOOR_OR_OBSERVATION_FAILED' if sample_fault.is_set() else None)
        if reason:
            process.terminate(); process.wait(timeout=3)
            return reason
        try:
            process.wait(timeout=min(1, max(.01, deadline - clock())))
        except subprocess.TimeoutExpired:
            pass
    return None


def next_number(directory, *, now=None):
    now = time.time() if now is None else now
    if (directory / 'stop.json').exists(): raise RuntimeError('SUPPLEMENT_STOPPED')
    rows = sorted(directory.glob('request-*/reservation.json'))
    if len(rows) >= 32: raise RuntimeError('REQUEST_LIMIT')
    if rows:
        first = read(rows[0])
        if remaining_seconds(first['reserved_unix'], now) <= 0: raise RuntimeError('WALL_WINDOW_EXPIRED')
        last = rows[-1].parent
        if read(rows[-1]).get('collector_sha256') not in {None, sha(Path(__file__))}:
            raise RuntimeError('COLLECTOR_CHANGED_DURING_APPROVED_RUN')
        if not (last / 'response.json').exists(): raise RuntimeError('PRIOR_REQUEST_UNKNOWN_DO_NOT_REPLAY')
        if not (last / 'judgement.json').exists(): raise RuntimeError('SEMANTIC_REVIEW_REQUIRED')
        judgement = read(last / 'judgement.json')
        if not judgement.get('continue') or judgement.get('response_sha256') != sha(last / 'response.json'):
            raise RuntimeError('PRIOR_STAGE_NOT_ACCEPTED')
    return len(rows) + 1


def child(number):
    import httpx
    from app.core.config import Settings
    from app.provider.factory import build_llm_provider
    from app.provider.vision import QwenVLMProvider
    root, manifest = frozen()
    ns = runpy.run_path(str(root / 'backend/app/service/analysis.py'))
    AnalysisService, AnalysisSnapshot, MediaRef = (ns[n] for n in ('AnalysisService', 'AnalysisSnapshot', 'MediaRef'))
    from app.domain.ai import WORK_ORDER_ANALYSIS_PROMPT_VERSION
    assert WORK_ORDER_ANALYSIS_PROMPT_VERSION == 'wo-analysis-v8-maintenance'
    assert QwenVLMProvider.prompt_version == 'repair-photo-v4'
    plan = plans()[number - 1]; case, kind = plan['case'], plan['kind']
    folder = OUT / f'request-{number:02d}'
    assert read(folder / 'reservation.json')['number'] == number
    images = read(OUT / 'images.json') if (OUT / 'images.json').exists() else []
    logs, captured = [], {}
    def request_hook(request):
        body = json.loads(request.content)
        assert str(request.url) == 'http://127.0.0.1:11435/v1/chat/completions'
        assert request.method == 'POST' and body['model'] == MODEL and body['temperature'] == 0
        assert body['reasoning_effort'] == 'none' and body['max_tokens'] == (256 if kind == 'VLM' else 512)
        append_file(folder / 'request.json', {'body': body, 'url': str(request.url), 'method': request.method,
                    'request_sha256': hashlib.sha256(request.content).hexdigest(), 'at_utc': datetime.now(UTC).isoformat(),
                    'source': 'actual httpx request hook; transmission may be UNKNOWN if response absent'})
    def response_hook(response):
        response.read()
        captured.update(http_status=response.status_code, raw_response_body=response.text,
                        raw_response_sha256=hashlib.sha256(response.content).hexdigest(),
                        contract_errors=raw_contract(response.text, kind))
        append_file(folder / 'raw-response.json', captured)
    client = httpx.Client(trust_env=False, timeout=60, event_hooks={'request': [request_hook], 'response': [response_hook]})
    settings = Settings(_env_file=None, app_env='test', llm_provider='production',
        llm_base_url='http://127.0.0.1:11435/v1', llm_model=MODEL, llm_api_key='local-loopback-no-auth',
        llm_disable_thinking=True, llm_max_output_tokens=512, llm_timeout_seconds=60)
    service = AnalysisService.__new__(AnalysisService)
    service._llm = build_llm_provider(settings); service._llm._client = client
    service._clock = lambda: datetime.now(UTC)
    service._write_call_log = lambda *args, **kwargs: logs.append(kwargs)
    objects = {i['id']: Path(i['copy']).read_bytes() for i in images}
    storage = type('MemoryDiagnosticStorage', (), {'get_object': lambda self, key: objects[key]})()
    service._vlm = QwenVLMProvider(base_url='http://127.0.0.1:11435/v1', model=MODEL,
        api_key='local-loopback-no-auth', timeout_seconds=60, storage=storage, client=client,
        disable_thinking=True, max_output_tokens=256)
    report = case.get('report', case.get('paired_report'))
    context = {'house': {'property_name': '合成测试小区', 'building_name': '合成楼', 'unit_name': '合成单元', 'room_no': '测试房'},
               'reporter_relationships': ['OWNER'], 'recent_house_orders': [dict(zip(('category','status','summary'), r)) for r in case.get('history', [])]}
    media = (MediaRef(case['id'], 'image/png', 'IMAGE'),) if case['id'].startswith('PHOTO') else ()
    snapshot = AnalysisSnapshot(work_order_id=0, tenant_id=701, property_id=702, house_id=703, building_id=704,
        analysis_id=0, analysis_version=1, work_order_version=1, input_hash=hashlib.sha256(report.encode()).hexdigest(),
        text=report, existing_asr_text=None, images=media, audio=(), context=context)
    try:
        if kind == 'VLM':
            result = service._run_vlm(snapshot, 'supplement-' + case['id'], 1)
        else:
            vision = read(OUT / f'request-{number-1:02d}/response.json')['provider_result'][0] if media else None
            result = service._run_llm(snapshot, asr_text=None, vision_text=vision, request_id='supplement-' + case['id'], attempt=1)
        captured.update(case=case, kind=kind, provider_result=result, call_log=logs, model_digest=DIGEST,
                        prompt_version=WORK_ORDER_ANALYSIS_PROMPT_VERSION, vision_prompt_version=QwenVLMProvider.prompt_version,
                        semantic_verdict='PENDING_MAIN_REVIEW', frozen_base_sha=manifest['base_sha'])
        if len(logs) != 1 or result[1] is not None: captured.setdefault('contract_errors', []).append('PROVIDER_ERROR')
        append_file(folder / 'response.json', captured)
    finally:
        client.close()


def execute_next():
    import msvcrt
    helpers = runpy.run_path(str(BASE / 'probe-model.py'))
    sample, api = helpers['resource_sample'], helpers['api']
    with (OUT / 'controller.lock').open('a+b') as lock:
        lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            number = next_number(OUT)
            frozen()
            if not prepare_images() and number > 24:
                print(json.dumps({'status': 'IMAGE_BRANCH_BLOCKED', 'reserved_requests': number - 1})); return
            runtime = read(OUT / 'runtime.json')
            assert runtime['no_cloud'] and runtime['concurrency'] == 1 and runtime['context_length'] == 4096
            assert api('/api/version', timeout=3)['version'] == '0.34.2'
            assert next(r for r in api('/api/tags', timeout=3)['models'] if r['name'] == MODEL)['digest'] == DIGEST
            loaded = api('/api/ps', timeout=3)['models']
            assert len(loaded) <= 1 and all(r['name'] == MODEL and r['digest'] == DIGEST and r['context_length'] == 4096 for r in loaded)
            before = sample()
            if before['ram_free_mib'] < 4500 or before['gpu_free_mib'] < (2500 if loaded else 6000):
                print(json.dumps({'status': 'RESOURCE_GUARD_WAIT', 'sample': before, 'reserved_requests': number - 1})); return
            # Check again after metadata/guard calls, before reserving or spawning.
            assert next_number(OUT) == number
            folder = OUT / f'request-{number:02d}'; folder.mkdir(exist_ok=False)
            append_file(folder / 'reservation.json', {'number': number, 'reserved_unix': time.time(),
                'case': plans()[number-1]['case']['id'], 'kind': plans()[number-1]['kind'],
                'repair_rounds_used': 3, 'reserved_before_send': True, 'before': before,
                'collector_sha256': sha(Path(__file__))})
            started = time.monotonic(); samples = [before]; stop_reason = None
            sample_stop = threading.Event()
            sample_fault = threading.Event()
            def monitor():
                while not sample_stop.wait(1):
                    try:
                        point = sample(); samples.append(point)
                        if min(point['ram_free_mib'], point['gpu_free_mib']) < 2000:
                            sample_fault.set(); return
                    except Exception:
                        sample_fault.set(); return
            monitor_thread = threading.Thread(target=monitor, daemon=True)
            monitor_thread.start()
            with (folder / 'child.stdout.txt').open('xb') as stdout, (folder / 'child.stderr.txt').open('xb') as stderr:
                process = subprocess.Popen([sys.executable, '-I', '-X', 'utf8', __file__, 'child', '--number', str(number)], stdout=stdout, stderr=stderr)
                stop_reason = wait_single_child(process, started + 60, sample_fault)
            sample_stop.set()
            monitor_thread.join(timeout=.1)
            result = read(folder / 'response.json') if (folder / 'response.json').exists() else {}
            if result.get('contract_errors') or not result:
                stop_reason = stop_reason or 'RAW_OR_PROVIDER_CONTRACT_FAILURE'
            append_file(folder / 'supervision.json', {'latency_seconds': time.monotonic()-started, 'samples': samples,
                'exit_code': process.returncode, 'stop_reason': stop_reason, 'child_pid': process.pid,
                'model_side_completion': 'UNKNOWN' if stop_reason else 'response_received'})
            if stop_reason:
                append_file(OUT / 'stop.json', {'reason': stop_reason, 'number': number, 'status': 'NOT_READY'})
            print(json.dumps({'number': number, 'status': stop_reason or 'SEMANTIC_REVIEW_REQUIRED',
                              'provider_result': result.get('provider_result'), 'response_sha256': sha(folder / 'response.json') if result else None}, ensure_ascii=False))
        finally:
            lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('mode', choices=['prepare', 'next', 'child']); p.add_argument('--number', type=int)
    args = p.parse_args()
    if args.mode == 'prepare': prepare_images()
    elif args.mode == 'child': child(args.number)
    else: execute_next()
