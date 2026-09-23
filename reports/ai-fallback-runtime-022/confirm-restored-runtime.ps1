# Read-only continuation: previous replacement completed, initial worker reply timed out.
# Never rerun the replacement or start another process just to obtain this snapshot.
$ErrorActionPreference='Stop'
Set-Location -LiteralPath 'D:\property-ai'
$runtimePython='D:\property-ai\tmp\uv-python\cpython-3.12.10-windows-x86_64-none\python.exe'
$probe=@'
import site,sys,json
site.addsitedir(r'D:\property-ai\.venv\Lib\site-packages')
sys.path[:0]=[r'D:\property-ai\backend',r'D:\property-ai\scripts\ops']
from auth001_isolated_runtime import build_isolated_settings,apply_isolated_environ
settings=build_isolated_settings();apply_isolated_environ(settings)
from app.worker.celery_app import celery_app
worker='auth001@DESKTOP-8NHSE12'
inspector=celery_app.control.inspect(destination=[worker],timeout=8)
result={}
for field in ('active','reserved','scheduled'):
    value=getattr(inspector,field)();assert value is not None and worker in value
    result[field]=len(value[worker]);assert result[field]==0
print(json.dumps(result))
'@
$idle = & $runtimePython -I -c $probe
if ($LASTEXITCODE -ne 0) { throw 'Worker not confirmed idle; preserve live processes' }
$processes=@(Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -in @(26332,47436,47800) } | Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine)
if ($processes.Count -ne 3) { throw 'Unexpected runtime ownership' }
if (Get-Process -Id 41352,46432,47604 -ErrorAction SilentlyContinue) { throw 'Old fault processes still exist' }
$health=Invoke-RestMethod 'http://127.0.0.1:8083/api/v1/healthz'
if ($health.data.status -ne 'ok') { throw 'Unhealthy API' }
$queue=docker exec property-ai-rabbitmq-1 rabbitmqctl list_queues -p property_ai_auth001 name messages_ready messages_unacknowledged consumers
if ($LASTEXITCODE -ne 0 -or @($queue -match '^property_ai\.auth001\s+0\s+0\s+1$').Count -ne 1 -or @($queue -match '^property_ai\.auth001.dlq\s+3\s+0\s+0$').Count -ne 1) { throw 'Unexpected isolated queue counters' }
$e=[ordered]@{
    task_id='AI-FALLBACK-RUNTIME-022'; code_sha=(git rev-parse HEAD); observed_at_utc=[DateTime]::UtcNow.ToString('o')
    replaced_fault_pids=@(41352,46432,47604); processes=$processes; idle=($idle|ConvertFrom-Json); health=$health; queue=$queue
    api_startup=@(Get-Content tmp/auth001-runtime/api-08adb3d-success.stdout.log -TotalCount 2)
    worker_startup=@(Get-Content tmp/auth001-runtime/worker-08adb3d-success.stdout.log -TotalCount 3)
    logging_source_sha256=(Get-FileHash backend/app/core/logging.py).Hash
    project_config_sha256=(Get-FileHash miniprogram/project.config.json).Hash
    continuation_note='First restore invocation stopped before any process mutation due to helper array matching; corrected second invocation replaced exact idle fault processes. Immediate worker inspect reply timed out after startup. This read-only continuation confirms those same live replacement processes; no duplicate start, business request or replay.'
    shared_services_changed=$false; message_contents_read=$false; business_replayed=$false
}
$target='D:\property-ai\reports\ai-fallback-runtime-022\restored-runtime-environment.json'
if (Test-Path -LiteralPath $target) { throw 'Preserve existing evidence' }
$e|ConvertTo-Json -Depth 8|Set-Content -LiteralPath $target -Encoding utf8
$e|Select-Object code_sha,observed_at_utc,idle,health|ConvertTo-Json -Depth 4
