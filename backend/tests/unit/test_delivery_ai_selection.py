"""Run the actual PowerShell entrypoint against inert runtime stand-ins."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
PWSH = shutil.which('pwsh')
pytestmark = pytest.mark.skipif(PWSH is None, reason='PowerShell 7 is required for actual entrypoint test')


@pytest.fixture
def entry(tmp_path):
    deploy = tmp_path / 'deploy'; deploy.mkdir()
    shutil.copyfile(ROOT / 'deploy/delivery.ps1', deploy / 'delivery.ps1')
    for name in ('.env.delivery', '.env.delivery.bootstrap'):
        (deploy / name).write_text('', encoding='utf-8')
    for name in ('work-ai-runtime.ps1', 'rag-runtime.ps1'):
        (deploy / name).write_text('''param([string]$Action,[string]$ArtifactManifest,[string]$ArtifactSha256)
[IO.File]::WriteAllText((Join-Path $PSScriptRoot "called.txt"),"called")
@{action=$Action;manifest=$ArtifactManifest;sha256=$ArtifactSha256}|ConvertTo-Json -Compress
exit 0
''', encoding='utf-8')
    return deploy


def invoke(deploy, action):
    return subprocess.run([PWSH, '-NoProfile', '-File', str(deploy / 'delivery.ps1'), action],
        capture_output=True, text=True, encoding='utf-8', timeout=10)


@pytest.mark.parametrize('action', ['work-ai-start', 'rag-start'])
def test_default_start_uses_selected_paired_artifact(entry, action):
    manifest = entry.parent / 'reports' / 'candidate with spaces' / 'manifest.json'
    manifest.parent.mkdir(parents=True); manifest.write_text('{}', encoding='utf-8')
    selected = dict(format='property-ai-runtime-selection-v1', manifest='reports/candidate with spaces/manifest.json', sha256='a'*64)
    (entry / 'ai-runtime-selection.json').write_text(json.dumps(selected), encoding='utf-8')
    result = invoke(entry, action)
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    assert Path(actual['manifest']) == manifest
    assert actual['sha256'] == selected['sha256'] and actual['action'] == 'start'


@pytest.mark.parametrize('selected', [
    '{broken',
    json.dumps(dict(format='wrong', manifest='reports/manifest.json', sha256='a'*64)),
    json.dumps(dict(format='property-ai-runtime-selection-v1', manifest='reports/manifest.json', sha256='bad')),
    json.dumps(dict(format='property-ai-runtime-selection-v1', manifest='../outside.json', sha256='a'*64)),
])
def test_invalid_selection_never_silently_starts_old_runtime(entry, selected):
    (entry / 'ai-runtime-selection.json').write_text(selected, encoding='utf-8')
    result = invoke(entry, 'rag-start')
    assert result.returncode != 0
    assert not (entry / 'called.txt').exists()


def test_absent_selection_preserves_historical_entry_until_activation(entry):
    result = invoke(entry, 'work-ai-start')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['manifest'] == ''
