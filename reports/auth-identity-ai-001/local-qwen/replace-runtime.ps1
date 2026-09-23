$ErrorActionPreference = 'Stop'
$repoRoot = 'D:\property-ai'
$out = Join-Path $repoRoot 'reports\auth-identity-ai-001\local-qwen'
$runtime = Join-Path $repoRoot 'tmp\selfhost-ai-local'
$pythonExe = Join-Path $repoRoot 'tmp\uv-python\cpython-3.12.10-windows-x86_64-none\python.exe'
$launcher = Join-Path $out 'launch-auth001.py'
$recordPath = Join-Path $out 'runtime-replacement.json'
if (Test-Path -LiteralPath $recordPath) { throw 'Replacement checkpoint exists. Inspect it; never repeat blindly.' }
$preflight = Get-Content -LiteralPath (Join-Path $out 'business-preflight.json') -Raw | ConvertFrom-Json
if (([DateTime]::UtcNow - [DateTime]::Parse($preflight.at_utc).ToUniversalTime()).TotalMinutes -gt 5) { throw 'Idle preflight too old' }
if ($preflight.revision -ne '20260919_0009' -or $preflight.vhost -ne 'property_ai_auth001') { throw 'Wrong environment' }
$old = @(Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -in 26332,47436,47800 })
if ($old.Count -ne 3) { throw 'Old process set changed; inspect before stopping' }
if (($old | Where-Object ProcessId -eq 26332).CommandLine -notlike '*ai-fallback-runtime-022\launch-runtime.py api success*') { throw 'Unexpected API owner' }
if (($old | Where-Object ProcessId -eq 47436).CommandLine -notlike '*ai-fallback-runtime-022\launch-runtime.py worker success*') { throw 'Unexpected Worker wrapper' }
if (($old | Where-Object ProcessId -eq 47800).ParentProcessId -ne 47436) { throw 'Unexpected Worker child' }
$record = [ordered]@{task_id='SELFHOST-AI-LOCAL-001'; started_at_utc=[DateTime]::UtcNow.ToString('o'); old_processes=@($old | Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine); steps=@(); source_sha=$preflight.code_sha}
function Save-Checkpoint { [IO.File]::WriteAllText($recordPath, ($record | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false)) }
Save-Checkpoint
# Exact destination, confirmed empty task lists, AUTH001 vhost only. No queue consumption.
& $pythonExe -I -c "import runpy; h=runpy.run_path(r'D:\property-ai\reports\notification-events-inbox-013\run-runtime.py'); s=h['_guard'](); from auth001_isolated_runtime import apply_isolated_environ; apply_isolated_environ(s); from app.worker.celery_app import celery_app; target='auth001@DESKTOP-8NHSE12'; i=celery_app.control.inspect(destination=[target],timeout=8); values=[getattr(i,n)() for n in ('active','reserved','scheduled')]; assert all(v is not None and target in v and not v[target] for v in values); celery_app.control.shutdown(destination=[target]); print('Exact idle AUTH001 Worker warm shutdown requested')"
if ($LASTEXITCODE -ne 0) { throw 'Worker shutdown guard failed; API untouched' }
$record.steps += 'exact-idle-worker-warm-shutdown-requested'; Save-Checkpoint
for ($attempt=0; $attempt -lt 20; $attempt++) {
    if (-not (Get-Process -Id 47436,47800 -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 500
}
if (Get-Process -Id 47436,47800 -ErrorAction SilentlyContinue) { throw 'Worker still alive: no forced kill or duplicate start' }
$record.steps += 'old-worker-exited'; Save-Checkpoint
$apiOwner = Get-NetTCPConnection -LocalPort 8083 -State Listen -ErrorAction Stop
if ($apiOwner.OwningProcess -ne 26332) { throw 'API owner changed' }
Stop-Process -Id 26332
Wait-Process -Id 26332 -Timeout 10 -ErrorAction SilentlyContinue
if (Get-NetTCPConnection -LocalPort 8083 -State Listen -ErrorAction SilentlyContinue) { throw 'API port still in use' }
$record.steps += 'verified-old-api-stopped'; Save-Checkpoint
$api = Start-Process -FilePath $pythonExe -ArgumentList @('-I',$launcher,'api') -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtime 'api.stdout.log') -RedirectStandardError (Join-Path $runtime 'api.stderr.log')
$record.api_pid = $api.Id; Save-Checkpoint
$worker = Start-Process -FilePath $pythonExe -ArgumentList @('-I',$launcher,'worker') -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtime 'worker.stdout.log') -RedirectStandardError (Join-Path $runtime 'worker.stderr.log')
$record.worker_pid = $worker.Id; Save-Checkpoint
$record.steps += 'new-api-and-worker-started-same-launcher'; Save-Checkpoint
for ($attempt=0; $attempt -lt 20; $attempt++) {
    try { $health=Invoke-RestMethod -Uri 'http://127.0.0.1:8083/api/v1/healthz' -TimeoutSec 2; $record.api_health=$health; break } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $record.Contains('api_health')) { throw 'New API not ready; retain exact process checkpoint' }
$record.steps += 'api-health-confirmed'; Save-Checkpoint
$record | ConvertTo-Json -Depth 8
