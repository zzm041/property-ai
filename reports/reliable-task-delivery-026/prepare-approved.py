"""Freeze only approved B inputs/source and record A/B authority; no inference/DB."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from datetime import UTC, datetime

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'reports/auth-identity-ai-001/local-qwen/supplement-024-rubric-01'
FILES = [
    'backend/app/service/analysis.py', 'backend/app/domain/ai.py',
    'backend/app/provider/llm/__init__.py', 'backend/app/provider/vision/__init__.py',
    'backend/app/provider/http.py',
    'reports/auth-identity-ai-001/local-qwen/gold-set-24.json',
    'reports/auth-identity-ai-001/local-qwen/repair-image-gold.json',
    'reports/auth-identity-ai-001/local-qwen/run-quality28.py',
]


def main():
    manifest = OUT / 'frozen-inputs.json'
    if manifest.exists():
        raise RuntimeError('Freeze already exists; inspect, never overwrite or reset budget')
    photos = json.loads((ROOT / FILES[6]).read_text(encoding='utf-8'))
    paths = FILES + ['reports/auth-identity-ai-001/local-qwen/repair-images/' + p['file'] for p in photos['cases']]
    private = ROOT / 'tmp/selfhost-ai-local/supplement-024-rubric-01/frozen'
    private.mkdir(parents=True, exist_ok=False)
    records = []
    for name in paths:
        source, target = ROOT / name, private / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        records.append({'path': name, 'bytes': source.stat().st_size,
                        'sha256': hashlib.sha256(source.read_bytes()).hexdigest()})
    OUT.mkdir(parents=True, exist_ok=True)
    content = {'supplement_id': 'SUPPLEMENT-024-RUBRIC-01', 'approved': True,
               'frozen_at_utc': datetime.now(UTC).isoformat(),
               'base_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
               'private_frozen_root': str(private), 'files': records,
               'repair_rounds_used': 3, 'new_inference_requests': 0,
               'deadline_beijing': '2026-09-27T14:00:00+08:00',
               'usage_budget': 'Owner supplied53percent; live remaining usage unobservable'}
    with manifest.open('x', encoding='utf-8') as stream:
        json.dump(content, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'frozen_files': len(records), 'base_sha': content['base_sha'], 'inference_requests': 0}))


if __name__ == '__main__':
    main()
