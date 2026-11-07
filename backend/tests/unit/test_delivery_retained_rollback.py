"""Exercise the actual entrypoint with a Docker stand-in; no real services."""
import json
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT=Path(__file__).resolve().parents[3]
PWSH=shutil.which('pwsh')
pytestmark=pytest.mark.skipif(PWSH is None,reason='PowerShell 7 required')

@pytest.mark.parametrize('action',['up','stop','init'])
@pytest.mark.parametrize('retained',[True,False])
def test_retained_rollback_never_enters_compose_mutation(tmp_path,action,retained):
    deploy=tmp_path/'deploy';deploy.mkdir()
    shutil.copyfile(ROOT/'deploy/delivery.ps1',deploy/'delivery.ps1')
    for name in ['.env.delivery','.env.delivery.bootstrap']:
        (deploy/name).write_text('COMPOSE_PROJECT_NAME=property-ai-delivery-check\n',encoding='utf-8')
    wrapper=tmp_path/'invoke.ps1'
    wrapper.write_text('''param([string]$Entry,[string]$Action,[string]$Retained)
function global:docker {
    $global:LASTEXITCODE=0
    if ($args[0] -eq 'ps') {
        if ($Retained -eq 'yes') { Write-Output 'property-ai-delivery-check-api-1-rollback-20261103t100125z' }
    } else { [IO.File]::AppendAllText((Join-Path $PSScriptRoot 'compose-called.txt'),($args -join ' ') + "`n") }
}
& $Entry $Action
''',encoding='utf-8')
    result=subprocess.run([PWSH,'-NoProfile','-File',str(wrapper),str(deploy/'delivery.ps1'),action,'yes' if retained else 'no'],capture_output=True,text=True,encoding='utf-8',timeout=10)
    log=tmp_path/'compose-called.txt'
    if retained:
        assert result.returncode != 0
        assert 'retained rollback' in result.stderr.lower()
        assert not log.exists()
    else:
        assert result.returncode==0,result.stderr
        assert log.exists()
