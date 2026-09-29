param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet("start", "stop", "status")]
    [string]$Action
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Net.Http
$DeployDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $DeployDir
$RuntimeDir = Join-Path $RepoRoot "tmp\delivery-work-ai-runtime"
$PrivateDir = Join-Path $RuntimeDir "calls"
$SecretPath = Join-Path $RuntimeDir "internal-token.txt"
$ConfigPath = Join-Path $RuntimeDir "config.json"
$CurrentPath = Join-Path $RuntimeDir "current.json"
$OllamaCurrent = Join-Path $RepoRoot "tmp\delivery-ollama-runtime\current.json"
$SourceCommit = "68d062f4355d837afee1afa377e1d83d7c1dd485"
$CandidateRoot = Join-Path $RuntimeDir "candidate-$SourceCommit"
$AdapterPath = Join-Path $CandidateRoot "scripts\ops\local_ai_http.py"
$RuntimeControlPath = Join-Path $CandidateRoot "scripts\ops\local_ai_runtime_control.py"
$ExpectedAdapterSha256 = "BD747BCBAD2BF10DD1810B8C69754CA91F7207C7C16AB24F5588E7091F0BE326"
$ExpectedRuntimeControlSha256 = "CEA1B4AD6A5D2C12F70E233E28C791C86BC6EFDC5F2EA9864FDC022D57D13867"
$ResolutionRoot = Join-Path $RepoRoot "reports"
$ResolutionPath = Join-Path $RepoRoot "reports\real-ai-integration-027\delivery-auto-8093\call-001-resolution.json"
$ResolutionReviewPath = Join-Path $RepoRoot "reports\codex\REAL-AI-INTEGRATION-027-DELIVERY-AUTO-call001-review-round1.raw.json"
$ExpectedResolutionSha256 = "1DAEE269FDE7BF1EEB532528A9D12FE905D3F7D4A4FBB64A36FCB7A3B266E05E"
$ExpectedResolutionReviewSha256 = "094E801BAA750929793B2FA231EADD272CC052B05A42CC39B20359B61D0F06B0"
$ExpectedReservationSha256 = "C2DA7C7D75698C01A25451887C72A290B6844A00095073C324B9B5CD52C1855F"
$ExpectedResultSha256 = "577F4DB0ACC0D4D94DB4A4B6CB6B2FA5B450E461F37008606F86016667702784"
$PythonPath = Join-Path $RepoRoot "tmp\selfhost-ai-runtime\bge-142-cpu\.venv\Scripts\python.exe"
$AsrPython = Join-Path $RepoRoot "tmp\selfhost-ai-runtime\asr-006-cpu\.venv\Scripts\python.exe"
$ModelRoot = Join-Path $RepoRoot "tmp\selfhost-ai-local\models-hf"
$ModelName = "qwen38-27b-q3km-g55:7af3b8f"

function Read-Token {
    if (-not (Test-Path -LiteralPath $SecretPath -PathType Leaf)) { return $null }
    $token = (Get-Content -Raw -LiteralPath $SecretPath).Trim()
    if ($token.Length -lt 43 -or $token -notmatch "^[A-Za-z0-9_-]+$") {
        throw "delivery work AI internal token is invalid"
    }
    return $token
}

function New-Token {
    $bytes = [byte[]]::new(32)
    [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

function Get-Listener {
    $listeners = @()
    foreach ($line in (& netstat -ano -p TCP)) {
        if ($line -match "^\s*TCP\s+127\.0\.0\.1:11437\s+\S+\s+LISTENING\s+(\d+)\s*$") {
            $listeners += [pscustomobject]@{ OwningProcess = [int]$Matches[1] }
        }
    }
    return $listeners
}

function Invoke-Health([string]$Token) {
    $handler = [Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $client = [Net.Http.HttpClient]::new($handler)
    try {
        $client.Timeout = [TimeSpan]::FromSeconds(2)
        $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Get, "http://127.0.0.1:11437/healthz")
        $request.Headers.Authorization = [Net.Http.Headers.AuthenticationHeaderValue]::new("Bearer", $Token)
        $response = $client.SendAsync($request).GetAwaiter().GetResult()
        if (-not $response.IsSuccessStatusCode) { throw "work AI health returned HTTP $([int]$response.StatusCode)" }
        return $response.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json
    } finally {
        $client.Dispose()
        $handler.Dispose()
    }
}

function Invoke-Drain([string]$Token) {
    $handler = [Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $client = [Net.Http.HttpClient]::new($handler)
    try {
        $client.Timeout = [TimeSpan]::FromSeconds(3)
        $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Post, "http://127.0.0.1:11437/admin/drain")
        $request.Headers.Authorization = [Net.Http.Headers.AuthenticationHeaderValue]::new("Bearer", $Token)
        $request.Content = [Net.Http.ByteArrayContent]::new([byte[]]::new(0))
        $response = $client.SendAsync($request).GetAwaiter().GetResult()
        if (-not $response.IsSuccessStatusCode) { throw "work AI drain returned HTTP $([int]$response.StatusCode)" }
        return $response.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json
    } finally {
        $client.Dispose()
        $handler.Dispose()
    }
}

function Assert-Candidate {
    if ($SourceCommit -match "^__" -or $ExpectedAdapterSha256 -match "^__" -or $ExpectedRuntimeControlSha256 -match "^__") {
        throw "work AI candidate is not frozen"
    }
    if (-not (Test-Path -LiteralPath $CandidateRoot -PathType Container)) {
        New-Item -ItemType Directory -Force -Path $CandidateRoot | Out-Null
        $archive = Join-Path $RuntimeDir "candidate-$SourceCommit.tar"
        if (Test-Path -LiteralPath $archive) { throw "candidate archive path already exists" }
        & git -C $RepoRoot archive --format=tar --output=$archive $SourceCommit backend/app scripts/ops/local_ai_http.py scripts/ops/local_ai_models.py scripts/ops/local_ai_process.py scripts/ops/local_ai_runtime_control.py
        if ($LASTEXITCODE -ne 0) { throw "could not export frozen work AI candidate" }
        try {
            & tar -xf $archive -C $CandidateRoot
            if ($LASTEXITCODE -ne 0) { throw "could not extract frozen work AI candidate" }
        } finally {
            Remove-Item -LiteralPath $archive -Force
        }
    }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $AdapterPath).Hash -ne $ExpectedAdapterSha256) {
        throw "frozen work AI adapter hash mismatch"
    }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $RuntimeControlPath).Hash -ne $ExpectedRuntimeControlSha256) {
        throw "frozen runtime control hash mismatch"
    }
    $reservation = Join-Path $PrivateDir "call-001\reservation.json"
    $result = Join-Path $PrivateDir "call-001\result.json"
    foreach ($asset in @($ResolutionPath,$ResolutionReviewPath,$reservation,$result)) {
        if (-not (Test-Path -LiteralPath $asset -PathType Leaf)) { throw "reviewed call-001 resolution asset is missing" }
    }
    $resolutionHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ResolutionPath).Hash
    $reviewHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ResolutionReviewPath).Hash
    $reservationHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $reservation).Hash
    $resultHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $result).Hash
    if (($resolutionHash -ne $ExpectedResolutionSha256) -or
            ($reviewHash -ne $ExpectedResolutionReviewSha256) -or
            ($reservationHash -ne $ExpectedReservationSha256) -or
            ($resultHash -ne $ExpectedResultSha256)) {
        throw "reviewed call-001 resolution binding mismatch"
    }
}

function New-RuntimeBinding {
    if (-not (Test-Path -LiteralPath $OllamaCurrent -PathType Leaf)) { throw "delivery Ollama receipt is missing" }
    $ollama = Get-Content -Raw -LiteralPath $OllamaCurrent | ConvertFrom-Json
    if ($ollama.host -ne "127.0.0.1:11435" -or $ollama.no_cloud -ne $true -or [int]$ollama.concurrency -ne 1) {
        throw "delivery Ollama receipt does not match the approved runtime"
    }
    $bindingPath = Join-Path $RuntimeDir ("runtime-binding-{0}.json" -f ([DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")))
    if (Test-Path -LiteralPath $bindingPath) { throw "runtime binding already exists" }
    $binding = [ordered]@{
        pid = [int]$ollama.pid
        executable = [string]$ollama.executable
        launched_at_utc = ([DateTimeOffset]$ollama.started_at_utc).UtcDateTime.ToString("o")
        listener = "127.0.0.1:11435"
        source_receipt_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $OllamaCurrent).Hash
    }
    [IO.File]::WriteAllText($bindingPath, ($binding | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    return $bindingPath
}

function Read-Current {
    if (-not (Test-Path -LiteralPath $CurrentPath -PathType Leaf)) { return $null }
    return Get-Content -Raw -LiteralPath $CurrentPath | ConvertFrom-Json
}

function Assert-CurrentProcess([object]$State) {
    $listener = @(Get-Listener)
    if ($listener.Count -ne 1 -or [int]$listener[0].OwningProcess -ne [int]$State.listener_pid) {
        throw "11437 listener does not match the recorded work AI instance"
    }
    $process = Get-Process -Id ([int]$State.listener_pid) -ErrorAction Stop
    $recordedStart = ([DateTimeOffset]$State.listener_started_at_utc).UtcDateTime
    if ([Math]::Abs(($process.StartTime.ToUniversalTime() - $recordedStart).TotalMilliseconds) -gt 1) {
        throw "work AI process start time does not match its receipt"
    }
    return $process
}

switch ($Action) {
    "start" {
        foreach ($path in @($PythonPath,$AsrPython,$ModelRoot,$OllamaCurrent)) {
            if (-not (Test-Path -LiteralPath $path)) { throw "required work AI asset is missing: $path" }
        }
        New-Item -ItemType Directory -Force -Path $RuntimeDir,$PrivateDir | Out-Null
        Assert-Candidate
        $existing = @(Get-Listener)
        if ($existing.Count -gt 0) {
            $state = Read-Current
            $token = Read-Token
            if ($null -eq $state -or -not $token) { throw "unrecorded 11437 listener refused" }
            $null = Assert-CurrentProcess $state
            $health = Invoke-Health $token
            if ($health.scope -ne "work_order_only" -or $health.request_active -or $health.accepting_requests -ne $true) {
                throw "recorded work AI adapter is not idle and accepting"
            }
            [ordered]@{ state=$state; health=$health } | ConvertTo-Json -Depth 7
            break
        }
        $token = Read-Token
        if (-not $token) {
            $token = New-Token
            [IO.File]::WriteAllText($SecretPath, $token, [Text.UTF8Encoding]::new($false))
        }
        $bindingPath = New-RuntimeBinding
        $config = [ordered]@{
            host="127.0.0.1";port=11437;private=$PrivateDir;model_root=$ModelRoot
            asr_python=$AsrPython;bge_python=$PythonPath;scope="work_order_only"
            limits=[ordered]@{asr=2;embedding=12;reranker=8;qwen=120}
            call_baseline=[ordered]@{asr=1;embedding=6;reranker=2;qwen=46}
            duration_seconds=7200;duration_baseline_seconds=535.483
            asr_start_ram_mib=11264;evidence_root=$RuntimeDir
            shared_model_lock_path=(Join-Path $RepoRoot "tmp\delivery-ai-shared\model.lock")
            shared_model_lock_wait_seconds=55
            resolution_root=$ResolutionRoot
            resolutions=@([ordered]@{
                record="call-001"
                reservation_sha256=$ExpectedReservationSha256.ToLowerInvariant()
                result_sha256=$ExpectedResultSha256.ToLowerInvariant()
                resolution_path=$ResolutionPath
                resolution_sha256=$ExpectedResolutionSha256
                review_path=$ResolutionReviewPath
                review_sha256=$ExpectedResolutionReviewSha256
            })
            runtime_binding_path=$bindingPath
            runtime_binding_sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $bindingPath).Hash.ToLowerInvariant()
        }
        [IO.File]::WriteAllText($ConfigPath, ($config | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
        $operationId=[guid]::NewGuid().ToString("N")
        $stdout=Join-Path $RuntimeDir "adapter-$operationId.stdout.log"
        $stderr=Join-Path $RuntimeDir "adapter-$operationId.stderr.log"
        $oldToken=$env:LOCAL_AI_INTERNAL_TOKEN;$oldModel=$env:PROPERTY_AI_LOCAL_QWEN_MODEL
        try {
            $env:LOCAL_AI_INTERNAL_TOKEN=$token;$env:PROPERTY_AI_LOCAL_QWEN_MODEL=$ModelName
            $launcher=Start-Process -FilePath $PythonPath -ArgumentList @("-I","-X","utf8",$AdapterPath,"serve","--config",$ConfigPath) -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
        } finally {
            $env:LOCAL_AI_INTERNAL_TOKEN=$oldToken;$env:PROPERTY_AI_LOCAL_QWEN_MODEL=$oldModel
        }
        $deadline=[DateTime]::UtcNow.AddSeconds(15)
        do { Start-Sleep -Milliseconds 250;$listener=@(Get-Listener) } until ($listener.Count -eq 1 -or [DateTime]::UtcNow -ge $deadline)
        if ($listener.Count -ne 1) { throw "work AI adapter did not acquire 11437" }
        $listenerProcess=Get-Process -Id ([int]$listener[0].OwningProcess) -ErrorAction Stop
        $health=Invoke-Health $token
        if ($health.scope -ne "work_order_only" -or $health.request_active -or $health.accepting_requests -ne $true -or $health.reservation_gate -ne "READY") {
            throw "work AI adapter failed no-inference startup checks"
        }
        $state=[ordered]@{
            format="property-ai-delivery-work-ai-runtime-v1";started_at_utc=[DateTime]::UtcNow.ToString("o")
            source_commit=$SourceCommit;launcher_pid=$launcher.Id;launcher_started_at_utc=$launcher.StartTime.ToUniversalTime().ToString("o")
            listener_pid=$listenerProcess.Id;listener_started_at_utc=$listenerProcess.StartTime.ToUniversalTime().ToString("o")
            adapter_sha256=$ExpectedAdapterSha256;runtime_control_sha256=$ExpectedRuntimeControlSha256
            config_sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $ConfigPath).Hash
            runtime_binding_sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $bindingPath).Hash
            token_sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $SecretPath).Hash
            stdout=$stdout;stderr=$stderr
            endpoint="http://127.0.0.1:11437";scope="work_order_only";models=$health.models;inference_on_start=$false
        }
        [IO.File]::WriteAllText($CurrentPath, ($state | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
        $state | ConvertTo-Json -Depth 6
    }
    "status" {
        $state=Read-Current;if ($null -eq $state) { throw "work AI runtime has no current receipt" }
        $null=Assert-CurrentProcess $state;$health=Invoke-Health (Read-Token)
        [ordered]@{state=$state;health=$health} | ConvertTo-Json -Depth 7
    }
    "stop" {
        $state=Read-Current;if ($null -eq $state) { throw "work AI runtime has no current receipt" }
        $process=Assert-CurrentProcess $state;$drain=Invoke-Drain (Read-Token)
        if ($drain.accepting_requests -ne $false -or $drain.request_active) { throw "work AI adapter is not safely drainable" }
        try {
            Stop-Process -Id ([int]$state.listener_pid) -ErrorAction Stop
        } catch {
            & taskkill /PID ([int]$state.listener_pid) /F | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "exact work AI adapter process could not be stopped" }
        }
        $deadline=[DateTime]::UtcNow.AddSeconds(10)
        do { Start-Sleep -Milliseconds 100 } until (@(Get-Listener).Count -eq 0 -or [DateTime]::UtcNow -ge $deadline)
        if (@(Get-Listener).Count -ne 0) { throw "11437 did not release after exact adapter stop" }
        Write-Output "delivery work AI adapter stopped; Ollama, model assets, ledger and token were retained"
    }
}
