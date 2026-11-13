<#
.SYNOPSIS
  Start the offline model service from this bundle (no network, no pre-installed Ollama).

.DESCRIPTION
  Uses the bundled ollama.exe and the bundled 4B model store.
  Environment: OLLAMA_MODELS -> .\models, OLLAMA_HOST -> 127.0.0.1:<Port>.

.PARAMETER Port
  Port for the model service. Default 11435 (matches deploy/.env.delivery).

.PARAMETER Stop
  Stop the service started from this bundle instead of starting it.
#>
param(
  [int]$Port = 11435,
  [switch]$Stop
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$exe = Join-Path $root 'ollama\ollama.exe'
$models = Join-Path $root 'models'
$log = Join-Path $root 'offline-ollama.log'

if (-not (Test-Path $exe)) { throw "bundle incomplete: $exe not found" }
if (-not (Test-Path $models)) { throw "bundle incomplete: $models not found" }

function Get-Tags {
  try {
    return Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/tags" -TimeoutSec 3
  } catch {
    return $null
  }
}

if ($Stop) {
  $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  if ($conn) {
    $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
    if ($proc -and $proc.ProcessName -eq 'ollama') {
      Stop-Process -Id $proc.Id -Force
      "stopped ollama pid=$($proc.Id) port=$Port"
    } else {
      "port $Port is held by $($proc.ProcessName), not ollama; nothing stopped"
    }
  } else {
    "nothing listening on $Port"
  }
  return
}

$env:OLLAMA_MODELS = $models
$env:OLLAMA_HOST = "127.0.0.1:$Port"
$env:OLLAMA_KEEP_ALIVE = '30m'

if (-not (Get-Tags)) {
  $args = @('serve')
  $proc = Start-Process -FilePath $exe -ArgumentList $args -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput $log -RedirectStandardError "$log.err"
  "started ollama pid=$($proc.Id) port=$Port log=$log"
}

$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline) {
  $tags = Get-Tags
  if ($tags) { break }
  Start-Sleep -Milliseconds 500
}
if (-not $tags) { throw "model service did not become ready on port $Port within 60s; see $log" }

$names = @($tags.models | ForEach-Object { $_.name })
"ready: $($names -join ', ')"
if ($names -notcontains 'qwen3.5:4b-ctx8192') {
  throw "expected model qwen3.5:4b-ctx8192 not found in bundle"
}
@"

Model service is up on http://127.0.0.1:$Port
Next step (from the repository root):
  `$env:PROPERTY_AI_LOCAL_QWEN_ENDPOINT = 'http://127.0.0.1:$Port'
  `$env:PROPERTY_AI_LOCAL_QWEN_MODEL    = 'qwen3.5:4b-ctx8192'
  .\deploy\ai-runtime.ps1 up
"@
