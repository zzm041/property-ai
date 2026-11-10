param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet("start", "stop", "status", "verify-artifact")]
    [string]$Action,
    [string]$ArtifactManifest = "",
    [string]$ArtifactSha256 = ""
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Net.Http
$DeployDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $DeployDir
$RuntimeDir = Join-Path $RepoRoot "tmp\delivery-rag-runtime"
$PrivateDir = Join-Path $RuntimeDir "calls"
$SecretPath = Join-Path $RuntimeDir "internal-token.txt"
$ConfigPath = Join-Path $RuntimeDir "config.json"
$CurrentPath = Join-Path $RuntimeDir "current.json"
$RagOllamaCurrent = Join-Path $RepoRoot "tmp\delivery-rag-ollama-runtime\current.json"
$SourceCommit = "caa2d21594bb7b78f2dc01e6bdfacc408161ec58"
$CandidateRoot = Join-Path $RuntimeDir "candidate-$SourceCommit"
$AdapterPath = Join-Path $CandidateRoot "scripts\ops\local_ai_http.py"
# PA-170: this value is the sha256 of the bytes `git archive` emits for
# $SourceCommit, not of the stored blob. On a repository with
# core.autocrlf=true the archive carries CRLF, so `git show | sha256sum`
# (LF) reports 31EC019F... for the very same file. Keep this pin in sync with
# archive output, and never compare it against a plain `git show` hash.
$ExpectedAdapterSha256 = "EE9F19900F923461AB8AD0A1DC65806FF96065E074C89B2B2456810B533C7270"
$PythonPath = Join-Path $RepoRoot "tmp\selfhost-ai-runtime\bge-142-cpu\.venv\Scripts\python.exe"
$AsrPython = Join-Path $RepoRoot "tmp\selfhost-ai-runtime\asr-006-cpu\.venv\Scripts\python.exe"
$ModelRoot = Join-Path $RepoRoot "tmp\selfhost-ai-local\models-hf"
$QwenEndpoint = "http://127.0.0.1:11438"
$RuntimeArtifact = $null
if ([bool]$ArtifactManifest -xor [bool]$ArtifactSha256) { throw "artifact manifest and SHA256 must be provided together" }
if ($ArtifactManifest) {
    $verifiedJson = & $PythonPath -I -B (Join-Path $RepoRoot "scripts\ops\verify_runtime_artifact.py") --manifest $ArtifactManifest --sha256 $ArtifactSha256
    if ($LASTEXITCODE -ne 0) { throw "runtime artifact verification failed" }
    $RuntimeArtifact = $verifiedJson | ConvertFrom-Json
    $CandidateRoot = $RuntimeArtifact.root
    $AdapterPath = Join-Path $CandidateRoot "scripts\ops\local_ai_http.py"
    $ExpectedAdapterSha256 = $RuntimeArtifact.files.'scripts/ops/local_ai_http.py'
    $SourceCommit = $null
}
if ($Action -eq "verify-artifact") {
    if ($null -eq $RuntimeArtifact) { throw "verify-artifact requires a pinned artifact" }
    [ordered]@{artifact_id=$RuntimeArtifact.artifact_id;candidate_image_id=$RuntimeArtifact.candidate_image_id;services_started=0} | ConvertTo-Json
    exit 0
}

function Read-Token {
    if (-not (Test-Path -LiteralPath $SecretPath -PathType Leaf)) { return $null }
    $token = (Get-Content -Raw -LiteralPath $SecretPath).Trim()
    if ($token.Length -lt 43 -or $token -notmatch "^[A-Za-z0-9_-]+$") {
        throw "delivery RAG internal token is invalid"
    }
    return $token
}

function New-Token {
    $bytes = [byte[]]::new(32)
    [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

function Assert-Candidate {
    if (-not (Test-Path -LiteralPath $CandidateRoot -PathType Container)) {
        if ($null -ne $RuntimeArtifact) { throw "verified runtime artifact disappeared" }
        New-Item -ItemType Directory -Force -Path $CandidateRoot | Out-Null
        $archive=Join-Path $RuntimeDir "candidate-$SourceCommit.tar"
        & git -C $RepoRoot archive --format=tar --output=$archive $SourceCommit backend/app scripts/ops/local_ai_http.py scripts/ops/local_ai_models.py scripts/ops/local_ai_process.py scripts/ops/local_ai_runtime_control.py
        if($LASTEXITCODE -ne 0){throw "could not export frozen RAG candidate"}
        try { & tar -xf $archive -C $CandidateRoot;if($LASTEXITCODE -ne 0){throw "could not extract frozen RAG candidate"} } finally { Remove-Item -LiteralPath $archive -Force }
    }
    if((Get-FileHash -Algorithm SHA256 -LiteralPath $AdapterPath).Hash -ne $ExpectedAdapterSha256){
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $AdapterPath).Hash
        throw ("frozen RAG adapter hash mismatch: pinned={0} actual={1} path={2} (the pin is the git-archive byte form; a plain `git show | sha256sum` reports the LF form and will never match on a core.autocrlf=true checkout)" -f $ExpectedAdapterSha256, $actual, $AdapterPath)
    }
}

function Assert-RagConfigured {
    # PA-170: the RAG runtime has never been started on this host. Say so with a
    # stable, greppable code instead of a bare "asset is missing", so the caller
    # can tell "not configured yet" apart from "configured but broken".
    $missing = @()
    foreach ($path in @($PythonPath, $AsrPython, $ModelRoot, $RagOllamaCurrent)) {
        if (-not (Test-Path -LiteralPath $path)) { $missing += "missing asset $path" }
    }
    if ($missing.Count -eq 0) {
        # The adapter binds itself to the RAG Ollama process recorded in that
        # receipt. A receipt whose process is gone turns into a Python traceback
        # and a bare "did not acquire 11436" 15 seconds later, which reads like a
        # networking fault; report the actual state instead.
        $ollama = Get-Content -Raw -LiteralPath $RagOllamaCurrent | ConvertFrom-Json
        if ($null -eq $ollama.pid -or -not (Get-Process -Id ([int]$ollama.pid) -ErrorAction SilentlyContinue)) {
            $missing += ("RAG Ollama receipt {0} records pid {1}, which is not running" -f $RagOllamaCurrent, $ollama.pid)
        }
    }
    if ($missing.Count -gt 0) {
        throw ("RAG_RUNTIME_DISABLED: the delivery RAG runtime is not configured on this host; {0}. Enable it with `deploy\rag-runtime.ps1 start` only after the checklist in docs/runbook.md §6 passes." -f ($missing -join "; "))
    }
}

function Get-Listener {
    $listeners = @()
    foreach ($line in (& netstat -ano -p TCP)) {
        if ($line -match "^\s*TCP\s+127\.0\.0\.1:11436\s+\S+\s+LISTENING\s+(\d+)\s*$") {
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
        $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Get, "http://127.0.0.1:11436/healthz")
        $request.Headers.Authorization = [Net.Http.Headers.AuthenticationHeaderValue]::new("Bearer", $Token)
        $response = $client.SendAsync($request).GetAwaiter().GetResult()
        if (-not $response.IsSuccessStatusCode) { throw "RAG adapter health returned HTTP $([int]$response.StatusCode)" }
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
        $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Post, "http://127.0.0.1:11436/admin/drain")
        $request.Headers.Authorization = [Net.Http.Headers.AuthenticationHeaderValue]::new("Bearer", $Token)
        $request.Content = [Net.Http.ByteArrayContent]::new([byte[]]::new(0))
        $response = $client.SendAsync($request).GetAwaiter().GetResult()
        if (-not $response.IsSuccessStatusCode) { throw "RAG adapter drain returned HTTP $([int]$response.StatusCode)" }
        return $response.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json
    } finally {
        $client.Dispose()
        $handler.Dispose()
    }
}

function Read-Current {
    if (-not (Test-Path -LiteralPath $CurrentPath -PathType Leaf)) { return $null }
    return Get-Content -Raw -LiteralPath $CurrentPath | ConvertFrom-Json
}

function Assert-CurrentProcess([object]$State) {
    $listener = @(Get-Listener)
    if ($listener.Count -ne 1 -or [int]$listener[0].OwningProcess -ne [int]$State.listener_pid) {
        throw "11436 listener does not match the recorded delivery RAG instance"
    }
    $process = Get-Process -Id ([int]$State.listener_pid) -ErrorAction Stop
    $recordedStart = ([DateTimeOffset]$State.listener_started_at_utc).UtcDateTime
    if ([Math]::Abs(($process.StartTime.ToUniversalTime() - $recordedStart).TotalMilliseconds) -gt 1) {
        throw "delivery RAG process start time does not match its receipt"
    }
    return $process
}

function New-CurrentState([object]$ListenerProcess, [object]$Health, [object]$Launcher) {
    $state = [ordered]@{
        format = "property-ai-delivery-rag-runtime-v1"
        started_at_utc = [DateTime]::UtcNow.ToString("o")
        launcher_pid = if ($null -eq $Launcher) { $null } else { $Launcher.Id }
        launcher_started_at_utc = if ($null -eq $Launcher) { $null } else { $Launcher.StartTime.ToUniversalTime().ToString("o") }
        listener_pid = $ListenerProcess.Id
        listener_started_at_utc = $ListenerProcess.StartTime.ToUniversalTime().ToString("o")
        adapter_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $AdapterPath).Hash
        config_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $ConfigPath).Hash
        token_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $SecretPath).Hash
        endpoint = "http://127.0.0.1:11436"
        qwen_endpoint = $QwenEndpoint
        scope = "rag_only"
        models = $Health.models
        source_commit = $SourceCommit
        artifact_id = $RuntimeArtifact.artifact_id
        candidate_image_id = $RuntimeArtifact.candidate_image_id
        inference_on_start = $false
    }
    $receipt = Join-Path $RuntimeDir ("launch-{0}.json" -f ([DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")))
    if (Test-Path -LiteralPath $receipt) { throw "delivery RAG launch receipt already exists" }
    [IO.File]::WriteAllText($receipt, ($state | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText($CurrentPath, ($state | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
    return $state
}

function New-RuntimeBinding {
    if (-not (Test-Path -LiteralPath $RagOllamaCurrent -PathType Leaf)) { throw "RAG Ollama receipt is missing" }
    $ollama=Get-Content -Raw -LiteralPath $RagOllamaCurrent | ConvertFrom-Json
    if($ollama.host -ne "127.0.0.1:11438" -or $ollama.no_cloud -ne $true -or [int]$ollama.context_length -ne 4096 -or [int]$ollama.concurrency -ne 1){throw "RAG Ollama receipt does not match the isolated contract"}
    $bindingPath=Join-Path $RuntimeDir ("runtime-binding-{0}.json" -f [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ"))
    $binding=[ordered]@{pid=[int]$ollama.pid;executable=[string]$ollama.executable;launched_at_utc=([DateTimeOffset]$ollama.started_at_utc).UtcDateTime.ToString("o");listener="127.0.0.1:11438";source_receipt_sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $RagOllamaCurrent).Hash}
    [IO.File]::WriteAllText($bindingPath,($binding|ConvertTo-Json),[Text.UTF8Encoding]::new($false));return $bindingPath
}

switch ($Action) {
    "start" {
        Assert-RagConfigured
        Assert-Candidate
        foreach ($path in @($AdapterPath)) {
            if (-not (Test-Path -LiteralPath $path)) { throw "required local AI asset is missing: $path" }
        }
        New-Item -ItemType Directory -Force -Path $RuntimeDir,$PrivateDir | Out-Null
        $existing = @(Get-Listener)
        if ($existing.Count -gt 0) {
            $state = Read-Current
            if ($null -ne $RuntimeArtifact -and ($null -eq $state -or $state.artifact_id -ne $RuntimeArtifact.artifact_id -or $state.adapter_sha256 -ne $ExpectedAdapterSha256)) {
                throw "running RAG uses a different source; drain and stop its exact receipt before replacement"
            }
            $token = Read-Token
            if (-not $token) { throw "11436 is occupied but the delivery RAG token is missing" }
            $health = Invoke-Health $token
            if ($health.scope -ne "rag_only" -or $health.request_active -or $health.accepting_requests -ne $true) { throw "recorded RAG adapter is not accepting idle RAG-only work" }
            $adopt=$null -eq $state
            if ($null -ne $state) {
                try { $null=Assert-CurrentProcess $state } catch {
                    if(Get-Process -Id ([int]$state.listener_pid) -ErrorAction SilentlyContinue){throw}
                    $archive=Join-Path $RuntimeDir ("retired-current-{0}.json" -f [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ"))
                    Move-Item -LiteralPath $CurrentPath -Destination $archive
                    $adopt=$true
                }
            }
            if ($adopt) {
                $lines = @(Get-Content -LiteralPath (Join-Path $RuntimeDir "adapter.stdout.log") | Where-Object { $_.Trim() })
                if ($lines.Count -ne 1) { throw "unrecorded 11436 process cannot be attributed to this launch" }
                $started = $lines[0] | ConvertFrom-Json
                if (
                    [int]$started.pid -ne [int]$existing[0].OwningProcess -or
                    [IO.Path]::GetFullPath([string]$started.source) -ne [IO.Path]::GetFullPath($AdapterPath) -or
                    $started.listen -ne "127.0.0.1:11436" -or
                    $started.health_inference -ne $false -or
                    (Get-Item -LiteralPath (Join-Path $RuntimeDir "adapter.stderr.log")).Length -ne 0
                ) { throw "unrecorded 11436 process cannot be attributed to this exact idle launch" }
                $listenerProcess = Get-Process -Id ([int]$existing[0].OwningProcess) -ErrorAction Stop
                $state = New-CurrentState $listenerProcess $health $null
            }
            Write-Output ($state | ConvertTo-Json -Depth 5)
            break
        }
        $token = Read-Token
        if (-not $token) {
            $token = New-Token
            [IO.File]::WriteAllText($SecretPath, $token, [Text.UTF8Encoding]::new($false))
        }
        $bindingPath=New-RuntimeBinding
        $config = [ordered]@{
            host = "127.0.0.1"
            port = 11436
            private = $PrivateDir
            model_root = $ModelRoot
            asr_python = $AsrPython
            bge_python = $PythonPath
            scope = "rag_only"
            limits = [ordered]@{ asr = 1; embedding = 12; reranker = 8; qwen = 18 }
            duration_seconds = 7200
            shared_model_lock_path = (Join-Path $RepoRoot "tmp\delivery-ai-shared\model.lock")
            shared_model_lock_wait_seconds = 55
            evidence_root = $RuntimeDir
            runtime_binding_path = $bindingPath
            runtime_binding_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $bindingPath).Hash.ToLowerInvariant()
            require_runtime_binding = $true
        }
        [IO.File]::WriteAllText($ConfigPath, ($config | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
        $stdout = Join-Path $RuntimeDir "adapter.stdout.log"
        $stderr = Join-Path $RuntimeDir "adapter.stderr.log"
        $oldToken = $env:LOCAL_AI_INTERNAL_TOKEN
        $oldModel = $env:PROPERTY_AI_LOCAL_QWEN_MODEL
        $oldEndpoint = $env:PROPERTY_AI_LOCAL_QWEN_ENDPOINT
        try {
            $env:LOCAL_AI_INTERNAL_TOKEN = $token
            $env:PROPERTY_AI_LOCAL_QWEN_MODEL = "qwen3.5:4b"
            $env:PROPERTY_AI_LOCAL_QWEN_ENDPOINT = $QwenEndpoint
            $launcher = Start-Process -FilePath $PythonPath -ArgumentList @(
                "-I", "-B", "-X", "utf8", $AdapterPath, "serve", "--config", $ConfigPath
            ) -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
        } finally {
            $env:LOCAL_AI_INTERNAL_TOKEN = $oldToken
            $env:PROPERTY_AI_LOCAL_QWEN_MODEL = $oldModel
            $env:PROPERTY_AI_LOCAL_QWEN_ENDPOINT = $oldEndpoint
        }
        $deadline = [DateTime]::UtcNow.AddSeconds(15)
        do {
            Start-Sleep -Milliseconds 250
            $listener = @(Get-Listener)
        } until ($listener.Count -eq 1 -or [DateTime]::UtcNow -ge $deadline)
        if ($listener.Count -ne 1) { throw "delivery RAG adapter did not acquire 11436" }
        $listenerProcess = Get-Process -Id ([int]$listener[0].OwningProcess) -ErrorAction Stop
        $health = Invoke-Health $token
        if ($health.scope -ne "rag_only" -or $health.request_active -or $health.accepting_requests -ne $true -or $health.reservation_gate -ne "READY") {
            throw "delivery RAG adapter failed its no-inference startup checks"
        }
        $state = New-CurrentState $listenerProcess $health $launcher
        Write-Output ($state | ConvertTo-Json -Depth 6)
    }
    "status" {
        $state = Read-Current
        if ($null -eq $state) {
            throw ("RAG_RUNTIME_DISABLED: the delivery RAG runtime has no current receipt under {0}; it is not running on this host (see docs/runbook.md §6 for the enable checklist)" -f $RuntimeDir)
        }
        if (@(Get-Listener).Count -eq 0) {
            throw ("RAG_RUNTIME_DISABLED: receipt {0} exists but nothing listens on 11436; the recorded run is stale, so RAG is not usable (see docs/runbook.md §6)" -f $CurrentPath)
        }
        $null = Assert-CurrentProcess $state
        $health = Invoke-Health (Read-Token)
        [ordered]@{ state = $state; health = $health; rag_enabled = $true } | ConvertTo-Json -Depth 7
    }
    "stop" {
        $state = Read-Current
        if ($null -eq $state) { throw "delivery RAG runtime has no current receipt" }
        if (@(Get-Listener).Count -eq 0) {
            throw ("RAG_RUNTIME_DISABLED: receipt {0} exists but nothing listens on 11436; there is no delivery RAG process to stop (see docs/runbook.md §6)" -f $CurrentPath)
        }
        $process = Assert-CurrentProcess $state
        $drain = Invoke-Drain (Read-Token)
        if ($drain.accepting_requests -ne $false) { throw "delivery RAG adapter did not enter drain state" }
        if ($drain.request_active) { throw "delivery RAG request is active; adapter is draining and stop is deferred" }
        Stop-Process -Id $process.Id
        $null = $process.WaitForExit(10000)
        if (@(Get-Listener).Count -ne 0) { throw "11436 did not release after the exact adapter stopped" }
        if ($state.launcher_pid -and $state.launcher_started_at_utc -and [int]$state.launcher_pid -ne $process.Id) {
            $launcher = Get-Process -Id ([int]$state.launcher_pid) -ErrorAction SilentlyContinue
            if ($null -ne $launcher) {
                $launcherStart = ([DateTimeOffset]$state.launcher_started_at_utc).UtcDateTime
                if ([Math]::Abs(($launcher.StartTime.ToUniversalTime() - $launcherStart).TotalMilliseconds) -gt 1) {
                    throw "delivery RAG launcher PID was reused; stop refused"
                }
                Stop-Process -Id $launcher.Id
                $null = $launcher.WaitForExit(10000)
            }
        }
        Write-Output "delivery RAG adapter stopped; model assets, ledger and token were retained"
    }
}
