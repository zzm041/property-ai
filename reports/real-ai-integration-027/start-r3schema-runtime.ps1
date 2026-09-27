$ErrorActionPreference = 'Stop'
$repoRoot = 'D:\property-ai'
$runtimeRoot = 'D:\property-ai\tmp\selfhost-ai-local\r3schema-20260925'
$modelRoot = 'D:\property-ai\tmp\selfhost-ai-local\models'
$runnerExe = 'C:\Users\lmj\AppData\Local\Programs\Ollama\ollama.exe'
$evidencePath = 'D:\property-ai\reports\real-ai-integration-027\r3schema-runtime.json'

if (Test-Path -LiteralPath $evidencePath) { throw 'Existing R3 schema runtime record must be resumed.' }
if ((Test-Path -LiteralPath $runtimeRoot) -or (Get-NetTCPConnection -LocalPort 11435 -State Listen -ErrorAction SilentlyContinue)) {
    throw 'Existing R3 schema runtime or listener requires identity inspection.'
}
if (-not (Test-Path -LiteralPath $runnerExe) -or -not (Test-Path -LiteralPath $modelRoot)) {
    throw 'Pinned runtime and existing model assets are required.'
}

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
$record = [ordered]@{
    task_id='REAL-AI-INTEGRATION-027'; phase='r3-json-schema-diagnostic';
    launched_at_utc=[DateTime]::UtcNow.ToString('o'); pid=$runner.Id;
    executable=$runnerExe; host=$env:OLLAMA_HOST; models=$modelRoot;
    no_cloud=$true; max_loaded_models=1; concurrency=1; context_length=4096;
    keep_alive='30s'; gpu_reserved_bytes=2147483648;
    api_worker_changed=$false; global_settings_changed=$false;
    log_directory=$runtimeRoot; status='STARTED_NOT_YET_READY'
}
$record | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $evidencePath -Encoding utf8
for ($attempt=0; $attempt -lt 20; $attempt++) {
    $runner.Refresh()
    if ($runner.HasExited) { throw 'Owned model server exited; preserve logs.' }
    try {
        $version = Invoke-RestMethod -Uri 'http://127.0.0.1:11435/api/version' -NoProxy -TimeoutSec 2
        if ($version.version -ne '0.34.2') { throw 'Version mismatch' }
        $record.status='READY_NO_INFERENCE'; $record.version=$version.version
        $record | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $evidencePath -Encoding utf8
        $record | ConvertTo-Json -Depth 5
        exit 0
    } catch { Start-Sleep -Milliseconds 500 }
}
throw 'Readiness timeout; no duplicate start.'
