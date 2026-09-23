$ErrorActionPreference = 'Stop'
$runtimePython = 'D:\property-ai\tmp\uv-python\cpython-3.12.10-windows-x86_64-none\python.exe'
$repoPath = 'D:\property-ai'
Set-Location -LiteralPath $repoPath
$verifyIdle = @'
import site,sys,json
site.addsitedir(r'D:\property-ai\.venv\Lib\site-packages')
sys.path[:0]=[r'D:\property-ai\backend',r'D:\property-ai\scripts\ops']
from auth001_isolated_runtime import build_isolated_settings,apply_isolated_environ
settings=build_isolated_settings();apply_isolated_environ(settings)
from app.worker.celery_app import celery_app
worker='auth001@DESKTOP-8NHSE12'
inspector=celery_app.control.inspect(destination=[worker],timeout=5)
result={}
for field in ('active','reserved','scheduled'):
    value=getattr(inspector,field)()
    assert value is not None and worker in value
    result[field]=len(value[worker]);assert result[field]==0
print(json.dumps(result))
'@
$idle = & $runtimePython -I -c $verifyIdle
if ($LASTEXITCODE -ne 0) { throw 'Worker not confirmed idle' }
$queue = docker exec property-ai-rabbitmq-1 rabbitmqctl list_queues -p property_ai_auth001 name messages_ready messages_unacknowledged consumers
if ($LASTEXITCODE -ne 0 -or @($queue -match '^property_ai\.auth001\s+0\s+0\s+1$').Count -ne 1) { throw 'Queue not drained' }
$old = @(Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -in @(41352,46432,47604) })
if ($old.Count -ne 3) { throw 'Expected exact fault-runtime processes missing; do not guess' }
foreach ($process in $old) { if ($process.ExecutablePath -ne $runtimePython) { throw 'Interpreter mismatch' } }
if (($old | Where-Object ProcessId -eq 41352).CommandLine -notlike '*launch-runtime.py api unavailable') { throw 'API owner mismatch' }
if (($old | Where-Object ProcessId -eq 46432).CommandLine -notlike '*launch-runtime.py worker unavailable') { throw 'Worker wrapper mismatch' }
if (($old | Where-Object ProcessId -eq 47604).ParentProcessId -ne 46432 -or ($old | Where-Object ProcessId -eq 47604).CommandLine -notlike '*worker -Q property_ai.auth001 --pool=solo*') { throw 'Worker child mismatch' }
$connections = @(Get-NetTCPConnection -LocalPort 8083 -ErrorAction Stop)
if (@($connections | Where-Object { $_.State -eq 'Established' }).Count -gt 0) { throw 'API has active requests' }
if (@($connections | Where-Object { $_.State -eq 'Listen' -and $_.OwningProcess -eq 41352 }).Count -ne 1) { throw 'API listener mismatch' }
$target = 'D:\property-ai\reports\ai-fallback-runtime-022\restored-runtime-environment.json'
if (Test-Path -LiteralPath $target) { throw 'Preserve completed restoration evidence' }
Stop-Process -Id 41352,47604
Wait-Process -Id 46432 -Timeout 10 -ErrorAction SilentlyContinue
if (Get-Process -Id 41352,46432,47604 -ErrorAction SilentlyContinue) { throw 'Old runtime still present; no duplicate start' }
$prefix = 'D:\property-ai\tmp\auth001-runtime'
$launcher = 'D:\property-ai\reports\ai-fallback-runtime-022\launch-runtime.py'
$api = Start-Process -FilePath $runtimePython -ArgumentList @('-I',$launcher,'api','success') -WorkingDirectory $repoPath -WindowStyle Hidden -RedirectStandardOutput "$prefix\api-08adb3d-success.stdout.log" -RedirectStandardError "$prefix\api-08adb3d-success.stderr.log" -PassThru
$worker = Start-Process -FilePath $runtimePython -ArgumentList @('-I',$launcher,'worker','success') -WorkingDirectory $repoPath -WindowStyle Hidden -RedirectStandardOutput "$prefix\worker-08adb3d-success.stdout.log" -RedirectStandardError "$prefix\worker-08adb3d-success.stderr.log" -PassThru
$health = $null
for ($attempt=0; $attempt -lt 20; $attempt++) {
    try { $health = Invoke-RestMethod 'http://127.0.0.1:8083/api/v1/healthz'; break } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $health -or $health.data.status -ne 'ok') { throw 'New API health not confirmed; inspect logs, no repeated start' }
$newIdle = & $runtimePython -I -c $verifyIdle
if ($LASTEXITCODE -ne 0) { throw 'New worker health/idle not confirmed' }
$afterQueue = docker exec property-ai-rabbitmq-1 rabbitmqctl list_queues -p property_ai_auth001 name messages_ready messages_unacknowledged consumers
if ($LASTEXITCODE -ne 0) { throw 'Queue inspection failed' }
$current = @(Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -in @($api.Id,$worker.Id) -or $_.ParentProcessId -eq $worker.Id } | Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine)
$evidence = [ordered]@{
    task_id='AI-FALLBACK-RUNTIME-022'; code_sha=(git rev-parse HEAD); restored_at_utc=[DateTime]::UtcNow.ToString('o')
    idle_before=($idle | ConvertFrom-Json); queue_before=$queue; old_processes=@($old | Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine)
    processes=$current; idle_after=($newIdle | ConvertFrom-Json); queue_after=$afterQueue; health=$health
    api_startup=@(Get-Content "$prefix\api-08adb3d-success.stdout.log" -TotalCount 2)
    worker_startup=@(Get-Content "$prefix\worker-08adb3d-success.stdout.log" -TotalCount 3)
    project_config_sha256=(Get-FileHash 'D:\property-ai\miniprogram\project.config.json').Hash
    historical_messages_read=$false; business_replayed=$false; shared_services_changed=$false
}
$evidence | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $target -Encoding utf8
$current | ConvertTo-Json -Depth 3
'Restored existing Development success configuration; original failure data preserved.'
