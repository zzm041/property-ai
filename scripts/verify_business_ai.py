"""Explicit, bounded local verification. No integration or provider calls selected.

Use the project's Python environment. Default prints the plan without running.
Each actual run creates a new reports directory and returns nonzero on failure.
"""
import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SUITES = {
    'access': ('test_announcement_scope_contract.py', 'test_announcement_audience_contract.py',
               'test_announcement_governance.py', 'test_work_order_visibility.py',
               'test_work_order_live_eligibility.py', 'test_manual_dispatch_capability.py',
               'test_review_business_gaps.py', 'test_incident_management.py', 'test_receipt_handling.py', 'test_employee_work_history.py'),
    'analysis': ('test_asr_checkpoint_contract.py', 'test_vlm_checkpoint_contract.py', 'test_media_model_revision.py',
                 'test_ai_analysis_input.py', 'test_summary_fidelity_prompt.py', 'test_database_recovery_025.py',
                 'test_task_intents_026.py', 'test_analysis_threshold_contract.py',
                 'test_analysis_crossfield_contract.py', 'test_media_cycle_0015.py',
                 'test_analysis_task_trace.py', 'test_durable_task_trace.py'),
    'rag': ('test_retrieval_trace_contract.py', 'test_retrieval_failure_contract.py',
            'test_retrieval_freshness_contract.py', 'test_retrieval_access_contract.py',
            'test_knowledge_retrieval_scope.py', 'test_reranker_output_contract.py'),
    'diagnosis': ('test_supervisor_diagnosis.py',),
    'semantic': ('test_observed_event_027.py', 'test_observed_event_delivery_contract.py',
                 'test_observation_rejection_diagnostics.py', 'test_observation_risk_consistency.py',
                 'test_observation_subject_conflicts.py', 'test_observation_coverage_v16.py',
                 'test_observation_mechanical_continuation.py',
                 'test_observation_operation_context.py',
                 'test_observation_lighting_continuation.py',
                 'test_observation_smoke_label.py',
                 'test_out_of_scope_request.py',
                 'test_scope_contact_gas_compatibility.py',
                 'test_scope_channel_confirmation.py',
                 'test_evidence_line_integrity.py',
                 'test_observation_danger_omission.py',
                 'test_observation_crossfield_safety.py', 'test_clarification_evidence_contract.py'),
    'agent': ('test_supervisor_agent.py', 'test_supervisor_agent_integration.py'),
    'client': ('announcement-audience.test.js', 'supervisor-diagnosis.test.js',
               'assistant-audience.test.js', 'work-order-audience.test.js',
               'property-scope.test.js', 'ui-layout-contract.test.js', 'staff-internal-progress.test.js',
               'supervisor-history.test.js', 'supervisor-lifecycle.test.js', 'receipt-handling.test.js',
               'employee-page-lifecycle.test.js', 'api-error-metadata.test.js', 'employee-history.test.js',
               'supervisor-agent.test.js'),
}


def source_fingerprints():
    paths = list((ROOT / 'backend/app').rglob('*.py')) + list((ROOT / 'backend/tests').rglob('*.py'))
    for suffix in ('*.js', '*.wxml', '*.wxss', '*.json'):
        paths += [path for path in (ROOT / 'miniprogram').rglob(suffix)
                  if 'node_modules' not in path.parts]
    paths += [ROOT / 'scripts/verify_business_ai.py', ROOT / 'backend/pyproject.toml']
    paths += [path for name in ('uv.lock', 'backend/uv.lock', 'miniprogram/package-lock.json',
                               'pytest.ini', 'pyproject.toml') if (path := ROOT / name).is_file()]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths) if p.is_file()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=(*SUITES, 'all'), default='all')
    parser.add_argument('--run', action='store_true', help='Run the declared synthetic suites')
    args = parser.parse_args(argv)
    selected = list(SUITES) if args.suite == 'all' else [args.suite]
    commands = []
    for suite in selected:
        prefix = 'miniprogram/tests' if suite == 'client' else 'backend/tests/unit'
        files = [f'{prefix}/{name}' for name in SUITES[suite]]
        if any(not (ROOT / name).is_file() for name in files):
            parser.error(f'Missing declared test in {suite}')
        if suite == 'client':
            node = shutil.which('node')
            if node is None:
                parser.error('Node is required for client tests; no installation is attempted')
            commands.extend((suite + '-' + Path(name).stem, [node, name]) for name in files)
        else:
            commands.append((suite, [sys.executable, '-B', '-m', 'pytest', *files, '-q', '-p', 'no:cacheprovider']))
    if not args.run:
        print(json.dumps(dict(mode='PLAN_ONLY', commands=commands,
                             scope='Synthetic local tests; no deployed, model-quality or load acceptance'), indent=2))
        return 0

    stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
    report = ROOT / 'reports' / 'business-ai-verification' / f'{stamp}-{uuid4().hex[:8]}'
    report.mkdir(parents=True, exist_ok=False)
    result = dict(status='RUNNING', started_at=datetime.now(UTC).isoformat(), results=[],
                  scope='Synthetic local tests only; not a network or environment sandbox; no production or model-quality claim',
                  source_sha256=source_fingerprints())

    def save():
        (report / 'result.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')

    save()
    print(f'Evidence: {report}', flush=True)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    for name, command in commands:
        if not name.startswith('client-'):
            command = [*command, f'--basetemp={report / "pytest-temp" / name}']
        entry = dict(suite=name, command=command, status='RUNNING', exit_code=None)
        result['results'].append(entry)
        save()
        try:
            with (report / f'{name}.txt').open('wb') as log:
                completed = subprocess.run(command, cwd=ROOT, env=env, stdout=log,
                                           stderr=subprocess.STDOUT, timeout=300, check=False)
            entry.update(status='PASS' if completed.returncode == 0 else 'FAIL', exit_code=completed.returncode)
        except subprocess.TimeoutExpired:
            entry.update(status='TIMEOUT')
        except OSError as exc:
            entry.update(status='EXECUTION_ERROR', error_type=type(exc).__name__)
        except KeyboardInterrupt:
            entry.update(status='INTERRUPTED')
            result['status'] = 'INTERRUPTED'
            save()
            raise
        save()
        print(f'{name}: {entry["status"]}', flush=True)
    result['source_after_sha256'] = source_fingerprints()
    before, after = result['source_sha256'], result['source_after_sha256']
    result['source_changes'] = sorted(path for path in before.keys() | after.keys()
                                      if before.get(path) != after.get(path))
    result['status'] = 'PASS' if (not result['source_changes']
        and all(item['status'] == 'PASS' for item in result['results'])) else 'FAIL'
    result['finished_at'] = datetime.now(UTC).isoformat()
    save()
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
