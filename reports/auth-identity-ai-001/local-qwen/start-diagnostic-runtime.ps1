$ErrorActionPreference = 'Stop'
$repoRoot = 'D:\property-ai'
$runtimeRoot = 'D:\property-ai\tmp\selfhost-ai-local\quality28-20260924'
$modelRoot = 'D:\property-ai\tmp\selfhost-ai-local\models'
$runnerExe = 'C:\Users\lmj\AppData\Local\Programs\Ollama\ollama.exe'
$evidencePath = 'D:\property-ai\reports\auth-identity-ai-001\local-qwen\diagnostic-runtime28.json'
if (Test-Path -LiteralPath $evidencePath) { throw 'Existing result; inspect, never duplicate start.' }
if ((Test-Path -LiteralPath $runtimeRoot) -or (Get-NetTCPConnection -LocalPort 11435 -State Listen -ErrorAction SilentlyContinue)) { throw 'Existing runtime or listener; no replacement.' }
if (-not (Test-Path -LiteralPath $runnerExe) -or -not (Test-Path -LiteralPath $modelRoot)) { throw 'Existing runtime/models required; no installation.' }
$old = Get-Content -LiteralPath 'D:\property-ai\tmp\selfhost-ai-local\launch.json' -Raw | ConvertFrom-Json
if (Get-Process -Id $old.pid -ErrorAction SilentlyContinue) { throw 'Prior PID exists; inspect ownership rather than replace.' }
if ($old.models -ne $modelRoot -or $old.host -ne '127.0.0.1:11435' -or $old.context_length -ne 4096) { throw 'Prior boundary mismatch.' }
New-Item -ItemType Directory -Path $runtimeRoot | Out-Null
$env:OLLAMA_HOST = '127.0.0.1:11435'
$env:OLLAMA_MODELS = $modelRoot
$env:OLLAMA_NO_CLOUD = '1'
$env:OLLAMA_NOPRUNE = '1'
$env:OLLAMA_MAX_LOADED_MODELS = '1'
$env:OLLAMA_NUM_PARALLEL = '1'
$env:OLLAMA_CONTEXT_LENGTH = '4096'
$env:OLLAMA_MAX_QUEUE = '2'
$env:OLLAMA_KEEP_ALIVE = '30s'
$env:OLLAMA_LOAD_TIMEOUT = '120s'
$env:OLLAMA_GPU_OVERHEAD = '2147483648'
$env:OLLAMA_IGPU_ENABLE = '0'
$env:OLLAMA_DEBUG = '0'
$env:OLLAMA_ORIGINS = 'http://127.0.0.1:11435'
$runner = Start-Process -FilePath $runnerExe -ArgumentList 'serve' -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeRoot 'server.stdout.log') -RedirectStandardError (Join-Path $runtimeRoot 'server.stderr.log')
$record = [ordered]@{task_id='SELFHOST-AI-LOCAL-001';phase='gold28-readonly';launched_at_utc=[DateTime]::UtcNow.ToString('o');pid=$runner.Id;executable=$runnerExe;host=$env:OLLAMA_HOST;models=$modelRoot;no_cloud=$true;max_loaded_models=1;concurrency=1;context_length=4096;keep_alive='30s';gpu_reserved_bytes=2147483648;old_pid_absent=$true;api_worker_changed=$false;business_port_8083_listening_before=$false;global_settings_changed=$false;log_directory=$runtimeRoot;status='STARTED_NOT_YET_READY'}
$record.business_port_8083_listening_before=[bool](Get-NetTCPConnection -LocalPort 8083 -State Listen -ErrorAction SilentlyContinue)
$record | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $evidencePath -Encoding utf8
for ($attempt=0;$attempt -lt 20;$attempt++) {
    $runner.Refresh();if ($runner.HasExited) { throw 'Owned model server exited; preserve logs.' }
    try {
        $version=Invoke-RestMethod -Uri 'http://127.0.0.1:11435/api/version' -NoProxy -TimeoutSec 2
        if ($version.version -ne '0.34.2') { throw 'Version mismatch' }
        $record.status='READY_NO_INFERENCE';$record.version=$version.version
        $record | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $evidencePath -Encoding utf8
        $record | ConvertTo-Json -Depth 5
        exit 0
    } catch { Start-Sleep -Milliseconds 500 }
}
throw 'Readiness timeout; no duplicate start.'
