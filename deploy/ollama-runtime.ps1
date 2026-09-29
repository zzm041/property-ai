param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet("start", "stop", "status")]
    [string]$Action,
    [ValidateRange(2147483648, 8589934592)]
    [long]$GpuOverheadBytes = 5368709120
)

$ErrorActionPreference = "Stop"
$DeployDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $DeployDir
$RuntimeDir = Join-Path $RepoRoot "tmp\delivery-ollama-runtime"
$StatePath = Join-Path $RuntimeDir "current.json"
$StartLockPath = Join-Path $RuntimeDir "start.lock"
$RestartResolutionPath = Join-Path $RepoRoot "reports\real-ai-integration-027\delivery-auto-8093\ollama-0344-restart-resolution.json"
$ExpectedRestartResolutionSha256 = "128943CB6C1B9CD653E1AB250B7D9D5330C2201184E2DA0B5399E3B4FB788317"
$ControlledStops = @(
    [pscustomobject]@{
        Path = Join-Path $RepoRoot "tmp\delivery-work-ai-runtime\calls\call-003\result.json"
        Sha256 = "B725035C11585AF362F6652B7531BDB63DCBC3DF75F75182015EAF513A7603B1"
    },
    [pscustomobject]@{
        Path = Join-Path $RepoRoot "tmp\delivery-work-ai-runtime\calls\call-007\result.json"
        Sha256 = "C136825C9391FFC8F48395AB1A3E78A4638FC53F181FCAE84FBF47DF284BF85C"
    }
)
$ModelRoot = Join-Path $RepoRoot "tmp\selfhost-ai-local\models"
$OllamaExe = if ($env:PROPERTY_AI_OLLAMA_EXE) {
    $env:PROPERTY_AI_OLLAMA_EXE
} else {
    "C:\Users\lmj\AppData\Local\Programs\Ollama\ollama.exe"
}
$ExpectedExeSha256 = "D571E4CF0AD4456FCCD33D5B985B6D63BA5669C6E5A812391704083952AAE3D5"
$ExpectedVersion = "0.34.4"
$Endpoint = "127.0.0.1:11435"

function Get-ListenerPid {
    $pids = @()
    foreach ($line in (& netstat -ano -p TCP)) {
        if ($line -match "^\s*TCP\s+127\.0\.0\.1:11435\s+\S+\s+LISTENING\s+(\d+)\s*$") {
            $pids += [int]$Matches[1]
        }
    }
    if ($pids.Count -gt 1) { throw "multiple listeners found on the isolated Ollama port" }
    if ($pids.Count -eq 1) { return $pids[0] }
    return $null
}

function Read-State {
    if (-not (Test-Path -LiteralPath $StatePath -PathType Leaf)) { return $null }
    return Get-Content -Raw -LiteralPath $StatePath | ConvertFrom-Json
}

function Retire-RebootedReceipt([object]$State) {
    if (-not (Test-Path -LiteralPath $RestartResolutionPath -PathType Leaf)) { return $false }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $RestartResolutionPath).Hash -ne $ExpectedRestartResolutionSha256) {
        return $false
    }
    $resolution = Get-Content -Raw -LiteralPath $RestartResolutionPath | ConvertFrom-Json
    $boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime()
    $recordedBoot = ([DateTimeOffset]$resolution.current_evidence.boot_time_utc).UtcDateTime
    $recordedStart = ([DateTimeOffset]$State.started_at_utc).UtcDateTime
    $currentPid = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$State.pid)" -ErrorAction SilentlyContinue
    if (
        $resolution.format -ne "property-ai-delivery-ollama-restart-resolution-v1" -or
        $resolution.task_id -ne "REAL-AI-INTEGRATION-027" -or
        [string]$resolution.original.receipt_sha256 -ne (Get-FileHash -Algorithm SHA256 -LiteralPath $StatePath).Hash -or
        [string]$resolution.original.operation_id -ne [string]$State.operation_id -or
        [int]$resolution.original.pid -ne [int]$State.pid -or
        ([DateTimeOffset]$resolution.original.started_at_utc).UtcDateTime -ne $recordedStart -or
        [IO.Path]::GetFullPath([string]$resolution.original.executable) -ne [IO.Path]::GetFullPath([string]$State.executable) -or
        [string]$resolution.original.executable_sha256 -ne [string]$State.executable_sha256 -or
        [Math]::Abs(($boot - $recordedBoot).TotalMilliseconds) -gt 1 -or
        $recordedBoot -le $recordedStart -or
        $null -ne $currentPid -or
        $null -ne (Get-ListenerPid) -or
        $resolution.current_evidence.recorded_pid_exists -ne $false -or
        [int]$resolution.current_evidence.listener_11435_count -ne 0 -or
        [string]$resolution.current_evidence.current_executable_sha256 -ne $ExpectedExeSha256 -or
        $resolution.semantics.historical_graceful_exit_confirmed -ne $false -or
        $resolution.semantics.current_execution_absent -ne $true -or
        $resolution.semantics.model_request_or_budget_effect -ne $false
    ) { return $false }
    $archive = Join-Path $RuntimeDir ("retired-restart-{0}.json" -f $State.operation_id)
    if (Test-Path -LiteralPath $archive) { throw "restart-retired receipt archive already exists" }
    Move-Item -LiteralPath $StatePath -Destination $archive
    return $true
}

function Retire-ControlledStopReceipt([object]$State) {
    $existing = @($ControlledStops | Where-Object { Test-Path -LiteralPath $_.Path -PathType Leaf })
    if ($existing.Count -eq 0) { throw "stale current receipt exists without controlled-stop evidence" }
    $binding = $null
    foreach ($candidate in $existing) {
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $candidate.Path).Hash -ne $candidate.Sha256) {
            continue
        }
        $evidence = Get-Content -Raw -LiteralPath $candidate.Path | ConvertFrom-Json
        $verified = $evidence.remote_stop.verified_before_stop
        $stopped = $evidence.remote_stop.stopped
        if (
            $evidence.status -eq "FAILED" -and
            $evidence.reason -eq "RESOURCE_FLOOR" -and
            $evidence.remote_completion -eq "CONTROLLED_STOP" -and
            [int]$verified.pid -eq [int]$State.pid -and
            [IO.Path]::GetFullPath([string]$verified.path) -eq [IO.Path]::GetFullPath([string]$State.executable) -and
            ([DateTimeOffset]$verified.started_utc).UtcDateTime -eq ([DateTimeOffset]$State.started_at_utc).UtcDateTime -and
            $stopped.tree_exit_confirmed -eq $true -and
            $stopped.listener_exit_confirmed -eq $true
        ) {
            $binding = $candidate
            break
        }
    }
    if ($null -eq $binding) {
        if (Retire-RebootedReceipt $State) { return }
        throw "controlled-stop or reviewed restart evidence does not bind the stale receipt"
    }
    $sameProcess = $false
    $currentPid = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$State.pid)" -ErrorAction SilentlyContinue
    if ($null -ne $currentPid -and $null -ne $currentPid.CreationDate) {
        $recordedStart = ([DateTimeOffset]$State.started_at_utc).UtcDateTime
        $sameProcess = [Math]::Abs((([DateTime]$currentPid.CreationDate).ToUniversalTime() - $recordedStart).TotalMilliseconds) -le 1
    } elseif ($null -ne $currentPid) {
        $sameProcess = $true
    }
    if ($sameProcess -or $null -ne (Get-ListenerPid)) {
        throw "controlled-stop receipt cannot be retired while execution may still exist"
    }
    $archive = Join-Path $RuntimeDir ("retired-controlled-{0}.json" -f $State.operation_id)
    if (Test-Path -LiteralPath $archive) { throw "controlled-stop receipt archive already exists" }
    Move-Item -LiteralPath $StatePath -Destination $archive
}

function Assert-StateProcess([object]$State) {
    $process = Get-Process -Id ([int]$State.pid) -ErrorAction Stop
    $recordedStart = ([DateTimeOffset]$State.started_at_utc).UtcDateTime
    if (
        [IO.Path]::GetFullPath($process.Path) -ne [IO.Path]::GetFullPath([string]$State.executable) -or
        [Math]::Abs(($process.StartTime.ToUniversalTime() - $recordedStart).TotalMilliseconds) -gt 1 -or
        (Get-ListenerPid) -ne $process.Id
    ) { throw "recorded Ollama process identity does not own the isolated listener" }
    return $process
}

function Assert-ReviewedRuntimeState([object]$State, [object]$Version) {
    if (
        [IO.Path]::GetFullPath([string]$State.executable) -ne [IO.Path]::GetFullPath($OllamaExe) -or
        [string]$State.executable_sha256 -ne $ExpectedExeSha256 -or
        (Get-FileHash -Algorithm SHA256 -LiteralPath ([string]$State.executable)).Hash -ne $ExpectedExeSha256 -or
        [string]$Version.version -ne $ExpectedVersion -or
        [string]$State.host -ne $Endpoint -or
        $State.no_cloud -ne $true -or
        [int]$State.max_loaded_models -ne 1 -or
        [int]$State.concurrency -ne 1
    ) {
        throw "recorded Ollama runtime does not match the reviewed version and isolation policy"
    }
}

function Invoke-Version {
    $request = [Net.HttpWebRequest]::Create("http://127.0.0.1:11435/api/version")
    $request.Proxy = $null
    $request.Timeout = 2000
    $response = $null
    $reader = $null
    try {
        $response = $request.GetResponse()
        $reader = [IO.StreamReader]::new($response.GetResponseStream())
        return $reader.ReadToEnd() | ConvertFrom-Json
    } finally {
        if ($null -ne $reader) { $reader.Dispose() }
        if ($null -ne $response) { $response.Dispose() }
    }
}

function Invoke-ResidentModelCount {
    $request = [Net.HttpWebRequest]::Create("http://127.0.0.1:11435/api/ps")
    $request.Proxy = $null
    $request.Timeout = 2000
    $response = $null
    $reader = $null
    try {
        $response = $request.GetResponse()
        $reader = [IO.StreamReader]::new($response.GetResponseStream())
        $payload = $reader.ReadToEnd() | ConvertFrom-Json
        return @($payload.models).Count
    } finally {
        if ($null -ne $reader) { $reader.Dispose() }
        if ($null -ne $response) { $response.Dispose() }
    }
}

function Test-RagListener {
    foreach ($line in (& netstat -ano -p TCP)) {
        if ($line -match "^\s*TCP\s+127\.0\.0\.1:11436\s+\S+\s+LISTENING\s+\d+\s*$") { return $true }
    }
    return $false
}

switch ($Action) {
    "start" {
        New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
        $startLock = $null
        try {
            try {
                $startLock = [IO.File]::Open($StartLockPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            } catch [IO.IOException] {
                throw "another Ollama start operation owns the isolated runtime reservation"
            }
            foreach ($path in @($OllamaExe, $ModelRoot)) {
                if (-not (Test-Path -LiteralPath $path)) { throw "required local model runtime asset is missing: $path" }
            }
            if ((Get-FileHash -Algorithm SHA256 -LiteralPath $OllamaExe).Hash -ne $ExpectedExeSha256) {
                throw "Ollama executable hash differs from the reviewed runtime"
            }
            $listenerPid = Get-ListenerPid
            $state = Read-State
            if ($null -ne $listenerPid) {
                if ($null -eq $state) { throw "isolated Ollama port is occupied without this entry's receipt" }
                $null = Assert-StateProcess $state
                $version = Invoke-Version
                Assert-ReviewedRuntimeState $state $version
                [ordered]@{ state = $state; version = $version.version; inference_on_start = $false } | ConvertTo-Json -Depth 6
                break
            }
            if ($null -ne $state) {
                Retire-ControlledStopReceipt $state
                $state = $null
            }
            $operationId = [guid]::NewGuid().ToString("N")
            $stdout = Join-Path $RuntimeDir "server-$operationId.stdout.log"
            $stderr = Join-Path $RuntimeDir "server-$operationId.stderr.log"
            $env:OLLAMA_HOST = $Endpoint
            $env:OLLAMA_MODELS = $ModelRoot
            $env:OLLAMA_NO_CLOUD = "1"
            $env:OLLAMA_NOPRUNE = "1"
            $env:OLLAMA_MAX_LOADED_MODELS = "1"
            $env:OLLAMA_NUM_PARALLEL = "1"
            $env:OLLAMA_CONTEXT_LENGTH = "4096"
            $env:OLLAMA_MAX_QUEUE = "2"
            $env:OLLAMA_KEEP_ALIVE = "30s"
            $env:OLLAMA_LOAD_TIMEOUT = "120s"
            $env:OLLAMA_GPU_OVERHEAD = [string]$GpuOverheadBytes
            $env:OLLAMA_IGPU_ENABLE = "0"
            $env:OLLAMA_DEBUG = "0"
            $env:OLLAMA_ORIGINS = "http://127.0.0.1:11435"
            $process = Start-Process -FilePath $OllamaExe -ArgumentList "serve" -WorkingDirectory $RepoRoot `
                -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
            $state = [ordered]@{
                format = "property-ai-delivery-ollama-runtime-v1"
                operation_id = $operationId
                started_at_utc = $process.StartTime.ToUniversalTime().ToString("o")
                pid = $process.Id
                executable = $OllamaExe
                executable_sha256 = $ExpectedExeSha256
                host = $Endpoint
                models = $ModelRoot
                stdout = $stdout
                stderr = $stderr
                no_cloud = $true
                max_loaded_models = 1
                concurrency = 1
                context_length = 4096
                keep_alive = "30s"
                gpu_reserved_bytes = $GpuOverheadBytes
                inference_on_start = $false
            }
            $stateStream = $null
            try {
                $stateStream = [IO.File]::Open($StatePath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
                $bytes = [Text.Encoding]::UTF8.GetBytes(($state | ConvertTo-Json -Depth 6))
                $stateStream.Write($bytes, 0, $bytes.Length)
                $stateStream.Flush($true)
            } finally {
                if ($null -ne $stateStream) { $stateStream.Dispose() }
            }
            $deadline = [DateTime]::UtcNow.AddSeconds(15)
            do {
                Start-Sleep -Milliseconds 250
                $process.Refresh()
                if ($process.HasExited) { throw "owned Ollama runtime exited during startup; logs and receipt were retained" }
                try { $version = Invoke-Version } catch { $version = $null }
            } until ($null -ne $version -or [DateTime]::UtcNow -ge $deadline)
            if ($null -eq $version -or $version.version -ne $ExpectedVersion) { throw "Ollama readiness/version check failed" }
            $null = Assert-StateProcess $state
            [ordered]@{ state = $state; version = $version.version; inference_on_start = $false } | ConvertTo-Json -Depth 6
        } finally {
            if ($null -ne $startLock) {
                $startLock.Dispose()
                Remove-Item -LiteralPath $StartLockPath -Force
            }
        }
    }
    "status" {
        $state = Read-State
        if ($null -eq $state) { throw "delivery Ollama runtime has no current receipt" }
        $null = Assert-StateProcess $state
        $version = Invoke-Version
        Assert-ReviewedRuntimeState $state $version
        [ordered]@{ state = $state; version = $version.version; inference_on_status = $false } | ConvertTo-Json -Depth 6
    }
    "stop" {
        $state = Read-State
        if ($null -eq $state) { throw "delivery Ollama runtime has no current receipt" }
        $process = Assert-StateProcess $state
        if (Test-RagListener) { throw "RAG adapter is still listening; run rag-stop and confirm drain before ollama-stop" }
        if ((Invoke-ResidentModelCount) -ne 0) { throw "a model is still resident; wait for idle unload before ollama-stop" }
        & taskkill /PID $process.Id /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "exact Ollama process-tree stop failed" }
        $deadline = [DateTime]::UtcNow.AddSeconds(10)
        do { Start-Sleep -Milliseconds 250 } until (
            $null -eq (Get-Process -Id $process.Id -ErrorAction SilentlyContinue) -or [DateTime]::UtcNow -ge $deadline
        )
        if ($null -ne (Get-Process -Id $process.Id -ErrorAction SilentlyContinue)) { throw "Ollama root process exit is unconfirmed" }
        if ($null -ne (Get-ListenerPid)) { throw "isolated Ollama listener did not release" }
        $archive = Join-Path $RuntimeDir ("stopped-{0}.json" -f ([DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")))
        Move-Item -LiteralPath $StatePath -Destination $archive
        "delivery Ollama runtime stopped; models, logs and immutable stop receipt retained"
    }
}
