$ErrorActionPreference = 'Stop'
$repoRoot = 'D:\property-ai'
$runtimeRoot = 'D:\property-ai\tmp\selfhost-ai-local\qwen35-9b-20260927'
$modelRoot = 'D:\property-ai\tmp\selfhost-ai-local\models'
$runnerExe = 'C:\Users\lmj\AppData\Local\Programs\Ollama\ollama.exe'
$priorEvidence = 'D:\property-ai\reports\real-ai-integration-027\qwen35-9b-runtime.json'
$evidencePath = 'D:\property-ai\reports\real-ai-integration-027\qwen35-9b-runtime-resume.json'
$expectedExeSha = 'F282BAB3CB8ECE9F1843811C08E54FEC449557EFADC49EE0F9AF07F1B93407FC'

if (-not (Test-Path -LiteralPath $priorEvidence) -or (Test-Path -LiteralPath $evidencePath)) { throw 'Resume evidence boundary rejected.' }
$prior = Get-Content -LiteralPath $priorEvidence -Raw | ConvertFrom-Json
if ($prior.status -ne 'STARTED_NOT_YET_READY' -or $prior.pid -ne 40700) { throw 'Prior bounded attempt mismatch.' }
if (Get-Process -Id $prior.pid -ErrorAction SilentlyContinue) { throw 'Prior process identity is still live.' }
if (Get-NetTCPConnection -LocalPort 11435 -State Listen -ErrorAction SilentlyContinue) { throw '11435 listener must be absent before resume.' }
if ((Get-FileHash -LiteralPath $runnerExe -Algorithm SHA256).Hash -ne $expectedExeSha) { throw 'Current Ollama executable hash mismatch.' }

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
$runner = Start-Process -FilePath $runnerExe -ArgumentList 'serve' -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeRoot 'server.resume.stdout.log') -RedirectStandardError (Join-Path $runtimeRoot 'server.resume.stderr.log')
$record = [ordered]@{
    task_id='REAL-AI-INTEGRATION-027'; phase='qwen35-9b-candidate-resume';
    prior_pid=$prior.pid; prior_reason='strict historical 0.34.2 expectation rejected actual 0.34.3';
    launched_at_utc=[DateTime]::UtcNow.ToString('o'); pid=$runner.Id;
    executable=$runnerExe; executable_sha256=$expectedExeSha; host=$env:OLLAMA_HOST;
    models=$modelRoot; no_cloud=$true; max_loaded_models=1; concurrency=1;
    context_length=4096; keep_alive='30s'; gpu_reserved_bytes=2147483648;
    api_worker_changed=$false; global_settings_changed=$false;
    log_directory=$runtimeRoot; status='STARTED_NOT_YET_READY'
}
$record | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $evidencePath -Encoding utf8
for ($attempt=0; $attempt -lt 30; $attempt++) {
    $runner.Refresh()
    if ($runner.HasExited) { throw 'Owned resumed model server exited; preserve logs.' }
    try {
        $version = Invoke-RestMethod -Uri 'http://127.0.0.1:11435/api/version' -NoProxy -TimeoutSec 2
        if ($version.version -ne '0.34.3') { throw 'Version mismatch' }
        $record.status='READY_NO_INFERENCE'; $record.version=$version.version
        $record | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $evidencePath -Encoding utf8
        $record | ConvertTo-Json -Depth 5
        exit 0
    } catch { Start-Sleep -Milliseconds 500 }
}
throw 'Resume readiness timeout; no duplicate start.'
