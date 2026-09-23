$ErrorActionPreference = 'Stop'
$repoRoot = 'D:\property-ai'
$runtimeRoot = Join-Path $repoRoot 'tmp\selfhost-ai-local'
$modelRoot = Join-Path $runtimeRoot 'models'
$ollamaExe = 'C:\Users\lmj\AppData\Local\Programs\Ollama\ollama.exe'
$port = 11435
if (-not (Test-Path -LiteralPath $ollamaExe -PathType Leaf)) { throw 'Existing Ollama missing; no install allowed.' }
if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { throw 'Independent port is occupied; do not replace its owner.' }
if (Test-Path -LiteralPath (Join-Path $runtimeRoot 'launch.json')) { throw 'Existing launch record: inspect the recorded process instead of starting twice.' }
New-Item -ItemType Directory -Path $modelRoot -Force | Out-Null

# Process-local only. Preserve the normal 11434 service, its models and global settings.
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
$child = Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeRoot 'server.stdout.log') -RedirectStandardError (Join-Path $runtimeRoot 'server.stderr.log')
$record = [ordered]@{
    task_id = 'SELFHOST-AI-LOCAL-001'
    launched_at_utc = [DateTime]::UtcNow.ToString('o')
    pid = $child.Id
    executable = $ollamaExe
    host = $env:OLLAMA_HOST
    models = $modelRoot
    no_cloud = $true
    no_prune = $true
    max_loaded_models = 1
    concurrency = 1
    context_length = 4096
    max_queue = 2
    keep_alive = '30s'
    gpu_reserved_bytes = 2147483648
    global_settings_changed = $false
    model_loaded = $false
}
$json = $record | ConvertTo-Json -Depth 4
[IO.File]::WriteAllText((Join-Path $runtimeRoot 'launch.json'), $json, [Text.UTF8Encoding]::new($false))
Write-Output $json
for ($attempt = 0; $attempt -lt 15; $attempt++) {
    $child.Refresh()
    if ($child.HasExited) { throw 'Test-owned server exited; inspect private log before retrying.' }
    try {
        $version = Invoke-RestMethod -Uri 'http://127.0.0.1:11435/api/version' -TimeoutSec 2
        Write-Output ($version | ConvertTo-Json -Compress)
        exit 0
    } catch { Start-Sleep -Milliseconds 500 }
}
throw 'Readiness timed out; preserve process/log and inspect without duplicate startup.'
