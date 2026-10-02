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
$SourceCommit = "61b762d9775845c47cf0b43f3003a3f4d1cb5519"
$CandidateRoot = Join-Path $RuntimeDir "candidate-$SourceCommit"
$AdapterPath = Join-Path $CandidateRoot "scripts\ops\local_ai_http.py"
$ModelAdapterPath = Join-Path $CandidateRoot "scripts\ops\local_ai_models.py"
$RuntimeControlPath = Join-Path $CandidateRoot "scripts\ops\local_ai_runtime_control.py"
$ExpectedAdapterSha256 = "70BB10C801D695830FCC33630773DB82986427B86962432354A0726AF3A3C676"
$ExpectedModelAdapterSha256 = "AD89639C05C18BF6D78FF28F32A62A105EE5B5ECFC9837EB5700B75DFCAAF320"
$ExpectedRuntimeControlSha256 = "567B325C7130E6AA7047AB957E5C19B442B66C8DBA29F8FAEDA50EE60830C876"
$ResolutionRoot = Join-Path $RepoRoot "reports"
$ResolutionPath = Join-Path $RepoRoot "reports\real-ai-integration-027\delivery-auto-8093\call-001-resolution.json"
$ResolutionReviewPath = Join-Path $RepoRoot "reports\codex\REAL-AI-INTEGRATION-027-DELIVERY-AUTO-call001-review-round1.raw.json"
$ExpectedResolutionSha256 = "1DAEE269FDE7BF1EEB532528A9D12FE905D3F7D4A4FBB64A36FCB7A3B266E05E"
$ExpectedResolutionReviewSha256 = "094E801BAA750929793B2FA231EADD272CC052B05A42CC39B20359B61D0F06B0"
$ExpectedReservationSha256 = "C2DA7C7D75698C01A25451887C72A290B6844A00095073C324B9B5CD52C1855F"
$ExpectedResultSha256 = "577F4DB0ACC0D4D94DB4A4B6CB6B2FA5B450E461F37008606F86016667702784"
$ReplayResolutionPath = Join-Path $RepoRoot "reports\real-ai-integration-027\delivery-auto-8093\call-008-replay-resolution-20261002.json"
$ReplayResolutionReviewPath = Join-Path $RepoRoot "reports\codex\REAL-AI-INTEGRATION-027-CALL008-REPLAY-RESOLUTION-review.raw.json"
$ExpectedReplayResolutionSha256 = "1C7F62B016973A08398215B639094DF5B460D4F35C621632F06A407585DE801E"
$ExpectedReplayResolutionReviewSha256 = "51E64C808F978B642D7CD2368D923B82B635A819473F23D6A62B760A6B1A1BFB"
$ExpectedReplayReservationSha256 = "4C196C2E93D029FF65036D3414BC02804EBEFC2A3AE7A847171C4245D9D76A71"
$ExpectedReplayResultSha256 = "F34D19C9383FED33DE32535CBB48358D2F4C731AA537B349C767343C388FB5C4"
$ContextReplayResolutionPath = Join-Path $RepoRoot "reports\real-ai-integration-027\delivery-auto-8093\call-013-replay-resolution-20261002.json"
$ContextReplayResolutionReviewPath = Join-Path $RepoRoot "reports\codex\REAL-AI-INTEGRATION-027-CALL013-REPLAY-RESOLUTION-review.raw.json"
$ExpectedContextReplayResolutionSha256 = "1666931B1C0F4C1F8FB7CC1F63FFCA810D4502535FAB90998D13DB512C3DB5FB"
$ExpectedContextReplayResolutionReviewSha256 = "BF8D0AA58CE7D2F38797B0EBB37B61F9A70E00054ADBB40FE618A5CF28DFF5BA"
$ExpectedContextReplayReservationSha256 = "57304CCA7754612CD7F93B2EAEB4568F69FE14D4675F145F16B64F7A2B1725CD"
$ExpectedContextReplayResultSha256 = "CF966772B58D8E152211F4054F9F32FBAA8D1342688FFD1A657E5FC50C25D9E4"
$TimeoutReplayResolutionPath = Join-Path $RepoRoot "reports\real-ai-integration-027\delivery-auto-8093\call-014-replay-resolution-20261002.json"
$TimeoutReplayResolutionReviewPath = Join-Path $RepoRoot "reports\codex\REAL-AI-INTEGRATION-027-CALL014-REPLAY-RESOLUTION-review.raw.json"
$ExpectedTimeoutReplayResolutionSha256 = "4F2648EE0FA0D2371E02D6862C7E7B3D310B1938ED70E751C040C704D624AF34"
$ExpectedTimeoutReplayResolutionReviewSha256 = "E774E08E910B772BAD0BA80FF0B6DAA31ED41CED8926090A3F94E9900F8539A6"
$ExpectedTimeoutReplayReservationSha256 = "B3E80EDF54F006F2C18A10756151303494885492D9B95978D230E382A1A4C477"
$ExpectedTimeoutReplayResultSha256 = "99CEDBDEBD63523CACB40267B58008AB0B921A8CA6987A8A98AA781EA8165B88"
$PythonPath = Join-Path $RepoRoot "tmp\selfhost-ai-runtime\bge-142-cpu\.venv\Scripts\python.exe"
$AsrPython = Join-Path $RepoRoot "tmp\selfhost-ai-runtime\asr-006-cpu\.venv\Scripts\python.exe"
$ModelRoot = Join-Path $RepoRoot "tmp\selfhost-ai-local\models-hf"
$ModelName = "qwen38-27b-q3km-g55:7af3b8f"
$RuntimeModelName = "qwen38-27b-q3km-g55-ctx8192:7af3b8f"

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
    if ($SourceCommit -match "^__" -or $ExpectedAdapterSha256 -match "^__" -or $ExpectedModelAdapterSha256 -match "^__" -or $ExpectedRuntimeControlSha256 -match "^__") {
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
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $ModelAdapterPath).Hash -ne $ExpectedModelAdapterSha256) {
        throw "frozen work AI model adapter hash mismatch"
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
    $replayReservation = Join-Path $PrivateDir "call-008\reservation.json"
    $replayResult = Join-Path $PrivateDir "call-008\result.json"
    foreach ($asset in @($ReplayResolutionPath,$ReplayResolutionReviewPath,$replayReservation,$replayResult)) {
        if (-not (Test-Path -LiteralPath $asset -PathType Leaf)) { throw "reviewed call-008 replay asset is missing" }
    }
    if (((Get-FileHash -Algorithm SHA256 -LiteralPath $ReplayResolutionPath).Hash -ne $ExpectedReplayResolutionSha256) -or
            ((Get-FileHash -Algorithm SHA256 -LiteralPath $ReplayResolutionReviewPath).Hash -ne $ExpectedReplayResolutionReviewSha256) -or
            ((Get-FileHash -Algorithm SHA256 -LiteralPath $replayReservation).Hash -ne $ExpectedReplayReservationSha256) -or
            ((Get-FileHash -Algorithm SHA256 -LiteralPath $replayResult).Hash -ne $ExpectedReplayResultSha256)) {
        throw "reviewed call-008 replay binding mismatch"
    }
    $contextReplayReservation = Join-Path $PrivateDir "call-013\reservation.json"
    $contextReplayResult = Join-Path $PrivateDir "call-013\result.json"
    foreach ($asset in @($ContextReplayResolutionPath,$ContextReplayResolutionReviewPath,$contextReplayReservation,$contextReplayResult)) {
        if (-not (Test-Path -LiteralPath $asset -PathType Leaf)) { throw "reviewed call-013 replay asset is missing" }
    }
    if (((Get-FileHash -Algorithm SHA256 -LiteralPath $ContextReplayResolutionPath).Hash -ne $ExpectedContextReplayResolutionSha256) -or
            ((Get-FileHash -Algorithm SHA256 -LiteralPath $ContextReplayResolutionReviewPath).Hash -ne $ExpectedContextReplayResolutionReviewSha256) -or
            ((Get-FileHash -Algorithm SHA256 -LiteralPath $contextReplayReservation).Hash -ne $ExpectedContextReplayReservationSha256) -or
            ((Get-FileHash -Algorithm SHA256 -LiteralPath $contextReplayResult).Hash -ne $ExpectedContextReplayResultSha256)) {
        throw "reviewed call-013 replay binding mismatch"
    }
    $timeoutReplayReservation = Join-Path $PrivateDir "call-014\reservation.json"
    $timeoutReplayResult = Join-Path $PrivateDir "call-014\result.json"
    foreach ($asset in @($TimeoutReplayResolutionPath,$TimeoutReplayResolutionReviewPath,$timeoutReplayReservation,$timeoutReplayResult)) {
        if (-not (Test-Path -LiteralPath $asset -PathType Leaf)) { throw "reviewed call-014 replay asset is missing" }
    }
    if (((Get-FileHash -Algorithm SHA256 -LiteralPath $TimeoutReplayResolutionPath).Hash -ne $ExpectedTimeoutReplayResolutionSha256) -or
            ((Get-FileHash -Algorithm SHA256 -LiteralPath $TimeoutReplayResolutionReviewPath).Hash -ne $ExpectedTimeoutReplayResolutionReviewSha256) -or
            ((Get-FileHash -Algorithm SHA256 -LiteralPath $timeoutReplayReservation).Hash -ne $ExpectedTimeoutReplayReservationSha256) -or
            ((Get-FileHash -Algorithm SHA256 -LiteralPath $timeoutReplayResult).Hash -ne $ExpectedTimeoutReplayResultSha256)) {
        throw "reviewed call-014 replay binding mismatch"
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
            limits=[ordered]@{asr=6;embedding=12;reranker=8;qwen=105}
            call_baseline=[ordered]@{asr=1;embedding=6;reranker=2;qwen=46}
            duration_seconds=4336;duration_baseline_seconds=535.483
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
            },[ordered]@{
                record="call-008"
                reservation_sha256=$ExpectedReplayReservationSha256.ToLowerInvariant()
                result_sha256=$ExpectedReplayResultSha256.ToLowerInvariant()
                resolution_path=$ReplayResolutionPath
                resolution_sha256=$ExpectedReplayResolutionSha256
                review_path=$ReplayResolutionReviewPath
                review_sha256=$ExpectedReplayResolutionReviewSha256
            },[ordered]@{
                record="call-013"
                reservation_sha256=$ExpectedContextReplayReservationSha256.ToLowerInvariant()
                result_sha256=$ExpectedContextReplayResultSha256.ToLowerInvariant()
                resolution_path=$ContextReplayResolutionPath
                resolution_sha256=$ExpectedContextReplayResolutionSha256
                review_path=$ContextReplayResolutionReviewPath
                review_sha256=$ExpectedContextReplayResolutionReviewSha256
            },[ordered]@{
                record="call-014"
                reservation_sha256=$ExpectedTimeoutReplayReservationSha256.ToLowerInvariant()
                result_sha256=$ExpectedTimeoutReplayResultSha256.ToLowerInvariant()
                resolution_path=$TimeoutReplayResolutionPath
                resolution_sha256=$ExpectedTimeoutReplayResolutionSha256
                review_path=$TimeoutReplayResolutionReviewPath
                review_sha256=$ExpectedTimeoutReplayResolutionReviewSha256
            })
            runtime_binding_path=$bindingPath
            runtime_binding_sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $bindingPath).Hash.ToLowerInvariant()
        }
        [IO.File]::WriteAllText($ConfigPath, ($config | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
        $operationId=[guid]::NewGuid().ToString("N")
        $stdout=Join-Path $RuntimeDir "adapter-$operationId.stdout.log"
        $stderr=Join-Path $RuntimeDir "adapter-$operationId.stderr.log"
        $oldToken=$env:LOCAL_AI_INTERNAL_TOKEN;$oldModel=$env:PROPERTY_AI_LOCAL_QWEN_MODEL;$oldRuntimeModel=$env:PROPERTY_AI_LOCAL_QWEN_RUNTIME_MODEL
        try {
            $env:LOCAL_AI_INTERNAL_TOKEN=$token;$env:PROPERTY_AI_LOCAL_QWEN_MODEL=$ModelName;$env:PROPERTY_AI_LOCAL_QWEN_RUNTIME_MODEL=$RuntimeModelName
            $launcher=Start-Process -FilePath $PythonPath -ArgumentList @("-I","-X","utf8",$AdapterPath,"serve","--config",$ConfigPath) -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
        } finally {
            $env:LOCAL_AI_INTERNAL_TOKEN=$oldToken;$env:PROPERTY_AI_LOCAL_QWEN_MODEL=$oldModel;$env:PROPERTY_AI_LOCAL_QWEN_RUNTIME_MODEL=$oldRuntimeModel
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
            adapter_sha256=$ExpectedAdapterSha256;model_adapter_sha256=$ExpectedModelAdapterSha256;runtime_control_sha256=$ExpectedRuntimeControlSha256
            config_sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $ConfigPath).Hash
            runtime_binding_sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $bindingPath).Hash
            token_sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $SecretPath).Hash
            stdout=$stdout;stderr=$stderr
            endpoint="http://127.0.0.1:11437";scope="work_order_only";models=$health.models;runtime_qwen_model=$RuntimeModelName;inference_on_start=$false
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
