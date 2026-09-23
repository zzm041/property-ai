"""One approved 4B comparison: identical committed-v3 input; SELECT only."""
from pathlib import Path
from datetime import UTC, datetime, timedelta
from dataclasses import asdict
import hashlib
import json
import runpy
import subprocess
import threading
import time

ROOT = Path(r'D:\property-ai')
OUT = Path(__file__).parent
MODEL = 'qwen3.5:4b'
target = OUT / 'probe-current-context-4b-v3.json'
assert not target.exists(), 'Inspect existing evidence; never silently repeat'
helpers = runpy.run_path(str(OUT / 'probe-model.py'))
api, sample = helpers['api'], helpers['resource_sample']
download = json.loads((OUT / 'download-4b-result.json').read_text())
assert download['hashes_verified'] and download['model'] == MODEL
assert api('/api/version')['version'] == '0.34.2'
assert not api('/api/ps')['models'], 'Wait for prior inference to unload; do not evict it'
metadata = api('/api/show', {'model': MODEL})
assert metadata['details']['quantization_level'] == 'Q4_K_M'
assert 'vision' in metadata['capabilities']
baseline = sample()
e = {
    'task_id': 'SELFHOST-AI-LOCAL-001', 'model': MODEL,
    'source_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
    'manifest_sha256': download['manifest_sha256'],
    'model_details': metadata['details'], 'capabilities': metadata['capabilities'],
    'mode': 'Same committed-v3 prompt/input; SELECT existing synthetic order; direct real local inference',
    'baseline': baseline, 'business_requests': False, 'business_state_changed': False,
    'queue_operations': False, 'runtime_config_changed': False,
    'acceptance_boundary': 'Diagnostic only; schema/labels do not establish factual grounding, AUTO dispatch, UI or production acceptance.',
    'resource_scope': 'Whole-workstation measurements with other applications retained; not exclusive model attribution.',
    'comparison_limit': 'Two actual deployment candidates: 2B Q8_0 versus 4B Q4_K_M; not an isolated parameter-count experiment.',
    'request_controls': {'temperature': 0, 'reasoning_effort': 'none', 'max_tokens': 512, 'timeout_seconds': 60, 'context_length': 4096},
}
source_paths = ['backend/app/service/analysis.py', 'backend/app/domain/ai.py', 'backend/app/provider/llm/__init__.py']
assert not subprocess.check_output(['git', 'diff', 'HEAD', '--', *source_paths], cwd=ROOT), 'Unrecorded prompt/provider change; stop'
e['source_sha256'] = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in source_paths}

def save():
    target.write_text(json.dumps(e, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')

if baseline['ram_free_mib'] < 4500 or baseline['gpu_free_mib'] < 6000:
    e['result'] = 'RESOURCE_HEADROOM_BLOCKED_NO_INFERENCE'
    save()
    print(json.dumps(e, ensure_ascii=False))
    raise SystemExit(2)

h = runpy.run_path(str(ROOT / 'reports/notification-events-inbox-013/run-runtime.py'))
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
import httpx
from app.core.config import Settings
from app.domain.ai import WorkOrderAnalysisOutput, map_analysis_output, WORK_ORDER_ANALYSIS_PROMPT_VERSION
from app.model.work_order import WorkOrder
from app.repository.ai import AiRepository
from app.provider.factory import build_llm_provider
from app.service.analysis import AnalysisService, AnalysisSnapshot

assert WORK_ORDER_ANALYSIS_PROMPT_VERSION == 'wo-analysis-v3'
ctx = json.loads((ROOT / 'tmp/selfhost-ai-local/business-context.json').read_text(encoding='utf-8'))
prior = json.loads((OUT / 'business-database-runtime.json').read_text(encoding='utf-8'))
settings = h['_guard']()
engine = create_engine(settings.test_database_url)
try:
    with Session(engine) as session:
        session.execute(text('SET TRANSACTION READ ONLY'))
        record = session.get(WorkOrder, ctx['work_order_id'])
        assert record.tenant_id == ctx['tenant_id'] and record.property_id == ctx['property_id']
        content = session.execute(text('SELECT text,extra_notes,asr_text FROM work_order_contents WHERE work_order_id=:id'), {'id': record.id}).mappings().one()
        assert not content['extra_notes'] and not content['asr_text'], 'Input changed; stop'
        repo = AiRepository(session)
        context = repo.get_analysis_context(record, since=record.created_at-timedelta(days=settings.ai_context_recent_days), reference_at=record.created_at, limit=settings.ai_context_recent_orders_limit)
        original = content['text']
        old_hash = hashlib.sha256(json.dumps({'prompt_version': WORK_ORDER_ANALYSIS_PROMPT_VERSION, 'work_order_id': record.id, 'text': original, 'asr': '', 'media': '', 'context': context}, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        assert old_hash == prior['analyses'][0]['input_hash'], 'Input must match actual original analysis exactly'
        snapshot = AnalysisSnapshot(work_order_id=record.id, tenant_id=record.tenant_id, property_id=record.property_id, house_id=record.house_id, building_id=repo.get_building_id(record.house_id), analysis_id=prior['analyses'][0]['id'], analysis_version=1, work_order_version=record.version, input_hash=old_hash, text=original, existing_asr_text=None, images=(), audio=(), context=context)
        e.update(work_order_id=record.id, prompt_version=WORK_ORDER_ANALYSIS_PROMPT_VERSION, prior_input_hash_verified=old_hash, current_report=original, context=context, persisted_status_before=record.status)
finally:
    engine.dispose()

model_settings = Settings(_env_file=None, app_env='test', llm_provider='production', llm_base_url='http://127.0.0.1:11435/v1', llm_model=MODEL, llm_api_key='local-loopback-no-auth', llm_disable_thinking=True, llm_max_output_tokens=512, llm_timeout_seconds=60)
service = AnalysisService.__new__(AnalysisService)
service._llm = build_llm_provider(model_settings)
local_http = httpx.Client(trust_env=False, timeout=60)
service._llm._client = local_http
e['transport'] = 'Real httpx client, trust_env=False; no inherited proxy, mock transport or answer substitution'
real_completion = service._llm.structured_completion
def recorded_completion(*, prompt, prompt_version):
    e['full_prompt_sha256'] = hashlib.sha256(prompt.encode('utf-8')).hexdigest()
    e['full_prompt_characters'] = len(prompt)
    return real_completion(prompt=prompt, prompt_version=prompt_version)
service._llm.structured_completion = recorded_completion
service._clock = lambda: datetime.now(UTC)
logs = []
service._write_call_log = lambda *args, **kwargs: logs.append(kwargs)
samples = [baseline]
done = threading.Event()
def sampling():
    while not done.wait(1):
        try:
            samples.append(sample())
        except Exception as exc:
            samples.append({'sample_error_type': type(exc).__name__})
sampler = threading.Thread(target=sampling, daemon=True)
sampler.start()
started = time.perf_counter()
print(json.dumps({'stage': 'same-input real 4B diagnostic', 'baseline': baseline, 'input_hash': old_hash}), flush=True)
try:
    payload, error, status = service._run_llm(snapshot, asr_text=None, vision_text=None, request_id=ctx['run_id']+'-4b-readonly', attempt=1)
    e.update(raw_payload=payload, error=error, call_status=status, measured_call=logs)
    mapped = map_analysis_output(WorkOrderAnalysisOutput.model_validate(payload)) if payload else None
    e['mapped'] = asdict(mapped) if mapped else None
    e['schema_and_dispatch_labels_match'] = bool(mapped and mapped.category.value == 'ELECTRICAL' and mapped.required_skill == 'ELECTRICAL' and not mapped.low_confidence)
    e['result'] = 'DIAGNOSTIC_REQUIRES_SEMANTIC_REVIEW' if mapped else 'MODEL_CALL_FAILED'
    e['loaded_models'] = api('/api/ps')['models']
except Exception as exc:
    e.update(result='DIAGNOSTIC_ERROR', error_type=type(exc).__name__)
finally:
    local_http.close()
    e['elapsed_seconds'] = round(time.perf_counter() - started, 3)
    done.set()
    sampler.join(timeout=10)
    e['resource_samples'] = samples
    valid = [row for row in samples if 'ram_free_mib' in row]
    e['min_ram_free_mib'] = min(row['ram_free_mib'] for row in valid)
    e['min_gpu_free_mib'] = min(row['gpu_free_mib'] for row in valid)
    e['max_gpu_used_mib'] = max(row['gpu_used_mib'] for row in valid)
    e['headroom_stop_further_tests'] = e['min_ram_free_mib'] < 2000 or e['min_gpu_free_mib'] < 2000
    save()
    print(json.dumps({key: e.get(key) for key in ('result', 'raw_payload', 'elapsed_seconds', 'min_ram_free_mib', 'min_gpu_free_mib', 'headroom_stop_further_tests')}, ensure_ascii=False), flush=True)
