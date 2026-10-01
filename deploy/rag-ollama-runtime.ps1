param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet("start", "stop", "status")]
    [string]$Action
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Net.Http
$DeployDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $DeployDir
$RuntimeDir = Join-Path $RepoRoot "tmp\delivery-rag-ollama-runtime"
$StatePath = Join-Path $RuntimeDir "current.json"
$StartLockPath = Join-Path $RuntimeDir "start.lock"
$AuthorizedStaleResolutionPath = Join-Path $RepoRoot "reports\real-ai-integration-027\delivery-rag-8093\rag-ollama-stale-receipt-resolution-20261002.json"
$ExpectedAuthorizedStaleResolutionSha256 = "EE8C4294800C65CAA5EC9A34B7581A42072F8409332AC7DCEC13B33F742024B2"
$ModelRoot = Join-Path $RepoRoot "tmp\selfhost-ai-local\models"
$OllamaExe = if ($env:PROPERTY_AI_OLLAMA_EXE) { $env:PROPERTY_AI_OLLAMA_EXE } else { "C:\Users\lmj\AppData\Local\Programs\Ollama\ollama.exe" }
$ExpectedExeSha256 = "D571E4CF0AD4456FCCD33D5B985B6D63BA5669C6E5A812391704083952AAE3D5"
$ExpectedVersion = "0.34.4"
$Endpoint = "127.0.0.1:11438"
$ContextLength = 4096

function Get-LoopbackListenerPids([int]$Port) {
    $ids = @()
    $netstatLines = @(& netstat -ano -p TCP)
    if ($LASTEXITCODE -ne 0) { throw "could not inspect loopback listener state" }
    foreach ($line in $netstatLines) {
        if ($line -match "^\s*TCP\s+127\.0\.0\.1:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$") { $ids += [int]$Matches[1] }
    }
    return $ids
}

function Get-ListenerPid {
    $ids = @(Get-LoopbackListenerPids 11438)
    if ($ids.Count -gt 1) { throw "multiple listeners found on the RAG Ollama port" }
    if ($ids.Count -eq 1) { return $ids[0] }
    return $null
}

function Get-CimProcessOrNull([int]$ProcessId) {
    return Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
}

function Get-ProcessTreeIds([int]$RootPid) {
    $all = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $result = [Collections.Generic.HashSet[int]]::new()
    $null = $result.Add($RootPid)
    do {
        $added = $false
        foreach ($item in $all) {
            if ($result.Contains([int]$item.ParentProcessId) -and $result.Add([int]$item.ProcessId)) { $added = $true }
        }
    } while ($added)
    return @($result)
}

function Read-State {
    if (Test-Path -LiteralPath $StatePath -PathType Leaf) { return Get-Content -Raw -LiteralPath $StatePath | ConvertFrom-Json }
    return $null
}

function Invoke-Json([string]$Path) {
    $handler = [Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $client = [Net.Http.HttpClient]::new($handler)
    try {
        $client.Timeout = [TimeSpan]::FromSeconds(2)
        return $client.GetStringAsync("http://127.0.0.1:11438$Path").GetAwaiter().GetResult() | ConvertFrom-Json
    } finally {
        $client.Dispose()
        $handler.Dispose()
    }
}

function Assert-State([object]$State) {
    $process = Get-Process -Id ([int]$State.pid) -ErrorAction Stop
    $started = ([DateTimeOffset]$State.started_at_utc).UtcDateTime
    if (
        (Get-ListenerPid) -ne $process.Id -or
        [IO.Path]::GetFullPath($process.Path) -ne [IO.Path]::GetFullPath($OllamaExe) -or
        [Math]::Abs(($process.StartTime.ToUniversalTime() - $started).TotalMilliseconds) -gt 1 -or
        (Get-FileHash -Algorithm SHA256 -LiteralPath $process.Path).Hash -ne $ExpectedExeSha256 -or
        $State.host -ne $Endpoint -or
        [int]$State.context_length -ne $ContextLength -or
        $State.no_cloud -ne $true
    ) { throw "RAG Ollama receipt does not bind the current isolated process" }
    $version = Invoke-Json "/api/version"
    if ($version.version -ne $ExpectedVersion) { throw "RAG Ollama version changed" }
    return $process
}

function Retire-AuthorizedStaleReceipt([object]$State) {
    if (-not (Test-Path -LiteralPath $AuthorizedStaleResolutionPath -PathType Leaf)) { return $false }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $AuthorizedStaleResolutionPath).Hash -ne $ExpectedAuthorizedStaleResolutionSha256) { return $false }
    $resolution = Get-Content -Raw -LiteralPath $AuthorizedStaleResolutionPath | ConvertFrom-Json
    $currentPid = Get-CimProcessOrNull ([int]$State.pid)
    if (
        $resolution.format -ne "property-ai-local-runtime-stale-receipt-resolution-v1" -or
        $resolution.task_id -ne "REAL-AI-INTEGRATION-027" -or
        [string]$resolution.original.receipt_sha256 -ne (Get-FileHash -Algorithm SHA256 -LiteralPath $StatePath).Hash -or
        [string]$resolution.original.operation_id -ne [string]$State.operation_id -or
        [int]$resolution.original.pid -ne [int]$State.pid -or
        ([DateTimeOffset]$resolution.original.started_at_utc).UtcDateTime -ne ([DateTimeOffset]$State.started_at_utc).UtcDateTime -or
        [string]$resolution.original.endpoint -ne $Endpoint -or
        [string]$resolution.original.executable_sha256 -ne $ExpectedExeSha256 -or
        [string]$resolution.current_evidence.current_executable_sha256 -ne (Get-FileHash -Algorithm SHA256 -LiteralPath $OllamaExe).Hash -or
        $null -ne $currentPid -or $null -ne (Get-ListenerPid) -or
        $resolution.current_evidence.recorded_pid_exists -ne $false -or
        [int]$resolution.current_evidence.listener_count -ne 0 -or
        $resolution.semantics.historical_exit_mode -ne "UNKNOWN" -or
        $resolution.semantics.current_execution_absent -ne $true -or
        $resolution.semantics.model_request_or_budget_effect -ne $false -or
        $resolution.semantics.history_rewritten -ne $false
    ) { return $false }
    $archive = Join-Path $RuntimeDir ("retired-authorized-stale-{0}.json" -f $State.operation_id)
    if (Test-Path -LiteralPath $archive) { throw "authorized stale RAG receipt archive already exists" }
    Move-Item -LiteralPath $StatePath -Destination $archive
    return $true
}

switch ($Action) {
    "start" {
        New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
        $startLock = $null
        try {
            try {
                $startLock = [IO.File]::Open($StartLockPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            } catch [IO.IOException] {
                throw "another RAG Ollama start operation owns the exclusive start lock"
            }
            foreach ($path in @($OllamaExe, $ModelRoot)) {
                if (-not (Test-Path -LiteralPath $path)) { throw "required RAG Ollama asset is missing" }
            }
            if ((Get-FileHash -Algorithm SHA256 -LiteralPath $OllamaExe).Hash -ne $ExpectedExeSha256) { throw "RAG Ollama executable hash changed" }
            $listener = Get-ListenerPid
            $state = Read-State
            if ($listener) {
                if ($null -eq $state) { throw "11438 is occupied without a receipt" }
                $null = Assert-State $state
                $state | ConvertTo-Json -Depth 6
                return
            }
            if ($null -ne $state -and -not (Retire-AuthorizedStaleReceipt $state)) { throw "stale RAG Ollama receipt requires explicit evidence; start refused" }
            $id = [guid]::NewGuid().ToString("N")
            $stdout = Join-Path $RuntimeDir "server-$id.stdout.log"
            $stderr = Join-Path $RuntimeDir "server-$id.stderr.log"
            $saved = @{host=$env:OLLAMA_HOST;models=$env:OLLAMA_MODELS;cloud=$env:OLLAMA_NO_CLOUD;context=$env:OLLAMA_CONTEXT_LENGTH}
            try {
                $env:OLLAMA_HOST = $Endpoint; $env:OLLAMA_MODELS = $ModelRoot; $env:OLLAMA_NO_CLOUD = "1"; $env:OLLAMA_NOPRUNE = "1"
                $env:OLLAMA_MAX_LOADED_MODELS = "1"; $env:OLLAMA_NUM_PARALLEL = "1"; $env:OLLAMA_CONTEXT_LENGTH = "4096"; $env:OLLAMA_MAX_QUEUE = "2"
                $env:OLLAMA_KEEP_ALIVE = "30s"; $env:OLLAMA_LOAD_TIMEOUT = "120s"; $env:OLLAMA_IGPU_ENABLE = "0"; $env:OLLAMA_DEBUG = "0"
                $process = Start-Process -FilePath $OllamaExe -ArgumentList "serve" -WorkingDirectory $RepoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
            } finally {
                $env:OLLAMA_HOST = $saved.host; $env:OLLAMA_MODELS = $saved.models; $env:OLLAMA_NO_CLOUD = $saved.cloud; $env:OLLAMA_CONTEXT_LENGTH = $saved.context
            }
            $state = [ordered]@{format="property-ai-delivery-rag-ollama-runtime-v1";operation_id=$id;started_at_utc=$process.StartTime.ToUniversalTime().ToString("o");pid=$process.Id;executable=$OllamaExe;executable_sha256=$ExpectedExeSha256;host=$Endpoint;models=$ModelRoot;stdout=$stdout;stderr=$stderr;no_cloud=$true;max_loaded_models=1;concurrency=1;context_length=4096;inference_on_start=$false}
            $receipt = [IO.File]::Open($StatePath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            try {
                $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($state | ConvertTo-Json -Depth 6))
                $receipt.Write($bytes, 0, $bytes.Length); $receipt.Flush($true)
            } finally { $receipt.Dispose() }
            $deadline = [DateTime]::UtcNow.AddSeconds(15)
            do {
                Start-Sleep -Milliseconds 250; $process.Refresh()
                if ($process.HasExited) { throw "RAG Ollama exited during startup" }
                try { $version = Invoke-Json "/api/version" } catch { $version = $null }
            } until ($version -or [DateTime]::UtcNow -ge $deadline)
            $null = Assert-State $state
            $tags = Invoke-Json "/api/tags"; $resident = Invoke-Json "/api/ps"
            if (-not (@($tags.models) | Where-Object { $_.name -eq "qwen3.5:4b" -and $_.digest -eq "2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd" }) -or @($resident.models).Count -ne 0) { throw "RAG 4B visibility or no-inference startup check failed" }
            $state | ConvertTo-Json -Depth 6
        } finally {
            if ($null -ne $startLock) {
                $startLock.Dispose()
                if (Test-Path -LiteralPath $StartLockPath) { Remove-Item -LiteralPath $StartLockPath -Force }
            }
        }
    }
    "status" {
        $state = Read-State
        if ($null -eq $state) { throw "RAG Ollama has no current receipt" }
        $null = Assert-State $state
        $resident = Invoke-Json "/api/ps"
        [ordered]@{state=$state;resident_models=@($resident.models).Count;inference_on_status=$false} | ConvertTo-Json -Depth 7
    }
    "stop" {
        $state = Read-State
        if ($null -eq $state) { throw "RAG Ollama has no current receipt" }
        $process = Assert-State $state
        if ((Invoke-Json "/api/ps").models.Count -ne 0) { throw "RAG model is resident" }
        if (@(Get-LoopbackListenerPids 11436).Count -ne 0) { throw "RAG adapter is still listening" }
        $tree = @(Get-ProcessTreeIds $process.Id)
        & taskkill /PID $process.Id /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "RAG Ollama stop failed" }
        $deadline = [DateTime]::UtcNow.AddSeconds(10)
        do {
            Start-Sleep -Milliseconds 200
            $remaining = @($tree | Where-Object { $null -ne (Get-CimProcessOrNull $_) })
            $listener = Get-ListenerPid
        } until (($remaining.Count -eq 0 -and $null -eq $listener) -or [DateTime]::UtcNow -ge $deadline)
        if ($remaining.Count -ne 0 -or $null -ne $listener) { throw "RAG Ollama process tree or listener remained after stop" }
        $archive = Join-Path $RuntimeDir ("stopped-{0}.json" -f [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ"))
        Move-Item -LiteralPath $StatePath -Destination $archive
    }
}
