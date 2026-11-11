param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet("up", "down", "status", "verify", "resolve-unknown")]
    [string]$Action,
    [switch]$KeepOllama,
    # PA-132: which call the shared completion barrier belongs to, and why releasing it is safe.
    [string]$Call,
    [string]$Reason
)

# PA-116-A-4: one entry point for the local work-AI runtime.
# This script owns *orchestration and cleanup only*. It never starts the adapter
# or Ollama itself; it delegates to the reviewed launchers so that the runtime
# binding / receipt logic stays in exactly one place:
#   deploy\ollama-runtime.ps1   -> tmp\delivery-ollama-runtime\current.json
#   deploy\work-ai-runtime.ps1  -> tmp\delivery-work-ai-runtime\current.json (writes runtime-binding-<ts>.json)
# What it adds on top: killing stray manual processes, clearing stale bindings so a
# reviewer never inherits "connection refused + binding points at a dead pid",
# and a single verify command with explicit assertions.

$ErrorActionPreference = "Stop"
# System.Net.Http types are not resolvable under [Net.Http.*] until the assembly is
# loaded; Windows PowerShell 5.1 does not preload it in a fresh -File session.
try { Add-Type -AssemblyName System.Net.Http -ErrorAction Stop } catch { }
$DeployDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $DeployDir
$RuntimeDir = Join-Path $RepoRoot "tmp\\delivery-work-ai-runtime"
$OllamaRuntimeDir = Join-Path $RepoRoot "tmp\\delivery-ollama-runtime"
$SecretPath = Join-Path $RuntimeDir "internal-token.txt"
$CurrentPath = Join-Path $RuntimeDir "current.json"
$OllamaCurrentPath = Join-Path $OllamaRuntimeDir "current.json"
$PromptVersionsPath = Join-Path $DeployDir "ai-runtime-prompts.json"
# PA-132: the shared completion barrier is written by whichever adapter scope hit UNKNOWN.
# Its release path lives in scripts\ops\ai_barrier.py so it can be unit tested and so the
# decision (who/when/why) ends up in a receipt instead of a bare `del`.
$BarrierPath = Join-Path $RepoRoot "tmp\\delivery-ai-shared\\completion-unknown.json"
$BarrierTool = Join-Path $RepoRoot "scripts\\ops\\ai_barrier.py"
$BarrierPython = Join-Path $RepoRoot "tmp\\selfhost-ai-runtime\\bge-142-cpu\\.venv\\Scripts\\python.exe"
$WorkAiLauncher = Join-Path $DeployDir "work-ai-runtime.ps1"
$OllamaLauncher = Join-Path $DeployDir "ollama-runtime.ps1"
$Endpoint = "http://127.0.0.1:11437"
$WorkPort = 11437
$ManagedPorts = @(11435, 11438)
# The runtime contract commit is defined by the launcher pins; read it from there
# instead of keeping a second copy in this orchestrator that can drift.
$ExpectedSourceCommit = $null
$launcherText = [IO.File]::ReadAllText($WorkAiLauncher, [Text.UTF8Encoding]::new($false))
if ($launcherText -match '(?m)^\$SourceCommit = "([0-9a-f]{40})"') { $ExpectedSourceCommit = $Matches[1] }
if (-not $ExpectedSourceCommit) { throw "could not read the pinned runtime source commit from $WorkAiLauncher" }
$ExpectedQwenModel = "qwen3.5:4b"
$ExpectedPromptVersionsFormat = "property-ai-runtime-prompts-v1"

function Get-PortListenerPids([int]$Port) {
    $pids = @()
    foreach ($line in (& netstat -ano -p TCP)) {
        if ($line -match ("^\s*TCP\s+127\.0\.0\.1:" + $Port + "\s+\S+\s+LISTENING\s+(\d+)\s*$")) {
            $pids += [int]$Matches[1]
        }
    }
    return $pids
}

function Read-Token {
    if (-not (Test-Path -LiteralPath $SecretPath -PathType Leaf)) { return $null }
    return (Get-Content -Raw -LiteralPath $SecretPath).Trim()
}

function Read-Json([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    # Windows PowerShell 5.1 Get-Content defaults to the ANSI code page, which
    # corrupts the UTF-8 prompt-version file. Read bytes and decode explicitly.
    return ([IO.File]::ReadAllText($Path, [Text.UTF8Encoding]::new($false)) | ConvertFrom-Json)
}

function Invoke-Api([string]$Path, [string]$Method, [string]$Token) {
    $handler = [Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $client = [Net.Http.HttpClient]::new($handler)
    try {
        $client.Timeout = [TimeSpan]::FromSeconds(5)
        $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::new($Method), "$Endpoint$Path")
        $request.Headers.Authorization = [Net.Http.Headers.AuthenticationHeaderValue]::new("Bearer", $Token)
        if ($Method -ne "GET") { $request.Content = [Net.Http.ByteArrayContent]::new([byte[]]::new(0)) }
        $response = $client.SendAsync($request).GetAwaiter().GetResult()
        $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        if (-not $response.IsSuccessStatusCode) { throw "work AI $Path returned HTTP $([int]$response.StatusCode): $body" }
        return $body | ConvertFrom-Json
    } finally {
        $client.Dispose()
        $handler.Dispose()
    }
}

function Get-PromptVersionsFile {
    if (-not (Test-Path -LiteralPath $PromptVersionsPath -PathType Leaf)) {
        throw "versioned prompt version file is missing: $PromptVersionsPath"
    }
    $payload = Read-Json $PromptVersionsPath
    if ($payload.format -ne $ExpectedPromptVersionsFormat) {
        throw "prompt version file format mismatch: $($payload.format)"
    }
    $values = @($payload.allowed_work_prompt_versions)
    if ($values.Count -lt 1) { throw "prompt version file lists no work prompt version" }
    foreach ($value in $values) {
        if (-not ($value -is [string]) -or $value.Trim().Length -eq 0) {
            throw "prompt version file contains an empty entry"
        }
    }
    return $values
}

function Stop-StrayPythonListener([int]$Port) {
    $pids = @(Get-PortListenerPids $Port)
    if ($pids.Count -eq 0) { return }
    foreach ($listenerPid in $pids) {
        $process = Get-Process -Id $listenerPid -ErrorAction SilentlyContinue
        $name = if ($null -eq $process) { "unknown" } else { $process.ProcessName }
        Write-Output "stopping stray listener on $Port (pid=$listenerPid, process=$name)"
        try {
            Stop-Process -Id $listenerPid -ErrorAction Stop
        } catch {
            & taskkill /PID $listenerPid /F | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "stray listener on $Port (pid=$listenerPid) could not be stopped" }
        }
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    do { Start-Sleep -Milliseconds 100 } until (@(Get-PortListenerPids $Port).Count -eq 0 -or [DateTime]::UtcNow -ge $deadline)
    if (@(Get-PortListenerPids $Port).Count -ne 0) { throw "port $Port did not release after stopping stray listeners" }
}

function Clear-WorkAiState {
    $removed = @()
    foreach ($file in @(Get-ChildItem -LiteralPath $RuntimeDir -Filter "runtime-binding*.json" -ErrorAction SilentlyContinue)) {
        Remove-Item -LiteralPath $file.FullName -Force
        $removed += $file.Name
    }
    if (Test-Path -LiteralPath $CurrentPath -PathType Leaf) {
        Remove-Item -LiteralPath $CurrentPath -Force
        $removed += (Split-Path -Leaf $CurrentPath)
    }
    if ($removed.Count -gt 0) { Write-Output ("cleared work AI state: " + ($removed -join ", ")) }
    else { Write-Output "work AI state was already clean" }
}

function Assert-Health([string]$Token, [object]$Current) {
    $health = Invoke-Api "/healthz" "GET" $Token
    if ($health.scope -ne "work_order_only") { throw "adapter scope mismatch: $($health.scope)" }
    if ($health.reservation_gate -ne "READY") { throw "adapter reservation gate is $($health.reservation_gate), not READY" }
    if ($health.accepting_requests -ne $true) { throw "adapter is not accepting requests" }
    if ($health.models.qwen -ne $ExpectedQwenModel) {
        throw "adapter qwen model is $($health.models.qwen), expected $ExpectedQwenModel"
    }
    $versions = @($health.prompt_versions.work_order)
    $expected = Get-PromptVersionsFile
    foreach ($value in $expected) {
        if ($versions -notcontains $value) {
            throw "adapter does not accept work prompt version $value (accepts: $($versions -join ','))"
        }
    }
    $source = [string]$health.prompt_versions.source
    if (-not ($source.StartsWith("file:") -or $source.StartsWith("env:"))) {
        throw "adapter prompt version source is '$source'; it fell back to the backend module instead of deploy/$([IO.Path]::GetFileName($PromptVersionsPath))"
    }
    if ($null -ne $Current) {
        if ([string]$Current.source_commit -ne $ExpectedSourceCommit) {
            throw "runtime receipt source_commit is $($Current.source_commit), expected $ExpectedSourceCommit"
        }
        if ([string]$Current.startup_model -eq "" -or $null -eq $Current.startup_model) {
            Write-Output "note: receipt has no startup_model field; prompt versions are asserted from /healthz instead"
        }
    }
    [ordered]@{
        endpoint = $Endpoint
        scope = $health.scope
        reservation_gate = $health.reservation_gate
        accepting_requests = $health.accepting_requests
        qwen = $health.models.qwen
        work_prompt_versions = $versions
        prompt_version_source = $source
        source_commit = $(if ($null -eq $Current) { $null } else { [string]$Current.source_commit })
    } | ConvertTo-Json -Depth 6
}

function Get-CompletionBarrier {
    # Read-only description of the shared barrier: an operator must be able to see why the
    # whole AI chain is BLOCKED without opening the adapter source.
    if (-not (Test-Path -LiteralPath $BarrierPath -PathType Leaf)) {
        return [ordered]@{
            status = "clear"; path = $BarrierPath; gate = "READY"
            call_id = $null; utc = $null; reason = $null; scopes = @(); remediation = $null
        }
    }
    $payload = $null
    try { $payload = Read-Json $BarrierPath } catch { $payload = $null }
    if ($null -eq $payload) {
        return [ordered]@{ status = "unreadable"; path = $BarrierPath; gate = "BLOCKED_RESOLUTION_INVALID" }
    }
    $callId = [string]$payload.call_id
    if (-not $callId) { $callId = [string]$payload.record }
    $scopes = @()
    if ($null -ne $payload.affected_scopes) { $scopes = @($payload.affected_scopes) }
    elseif ($payload.scope) { $scopes = @([string]$payload.scope) }
    [ordered]@{
        status = "blocked"; path = $BarrierPath; gate = "BLOCKED_SHARED_REQUEST_COMPLETION_UNKNOWN_NO_REPLAY"
        call_id = $callId; utc = $payload.utc; reason = $payload.reason
        status_code = $payload.status; remote_completion = $payload.remote_completion
        scopes = $scopes
        remediation = $payload.remediation
        release_command = ("deploy\ai-runtime.ps1 resolve-unknown -Call {0} -Reason `"<why releasing is safe>`"" -f $callId)
    }
}

switch ($Action) {
    "up" {
        $null = Get-PromptVersionsFile
        # 1) nothing unmanaged may own a port this runtime is responsible for.
        # A recorded Ollama runtime is stopped through its launcher first, so the
        # controlled-stop evidence stays intact; only unrecorded listeners are killed.
        foreach ($port in $ManagedPorts) {
            if ($port -eq 11435 -and (Test-Path -LiteralPath $OllamaCurrentPath -PathType Leaf)) {
                $ollamaReceipt = Read-Json $OllamaCurrentPath
                $recordedPid = [int]$ollamaReceipt.pid
                $recordedAlive = $null -ne (Get-CimInstance Win32_Process -Filter "ProcessId=$recordedPid" -ErrorAction SilentlyContinue)
                if ($recordedAlive -or @(Get-PortListenerPids $port).Count -gt 0) {
                    Write-Output "stopping the recorded Ollama runtime through its launcher"
                    & $OllamaLauncher stop
                    if ($LASTEXITCODE -ne 0) { throw "ollama runtime launcher failed to stop the recorded runtime" }
                } else {
                    # the recorded process is gone and its port is free: no execution to
                    # preserve, so archive the orphan receipt instead of tripping the guard
                    $orphanName = "retired-orphan-{0}.json" -f $ollamaReceipt.operation_id
                    $orphanPath = Join-Path $OllamaRuntimeDir $orphanName
                    Move-Item -LiteralPath $OllamaCurrentPath -Destination $orphanPath -Force
                    Write-Output "archived orphan Ollama receipt: $orphanName (pid $recordedPid is gone)"
                }
            }
            Stop-StrayPythonListener $port
        }
        $workPids = @(Get-PortListenerPids $WorkPort)
        if ($workPids.Count -gt 0) {
            if ($null -eq (Read-Json $CurrentPath)) {
                Write-Output "11437 was owned by an unrecorded adapter; stopping it and clearing state"
                Stop-StrayPythonListener $WorkPort
            }
        }
        if (@(Get-PortListenerPids $WorkPort).Count -eq 0) { Clear-WorkAiState }
        # 1b) ollama-runtime.ps1 pins the reviewed Ollama binary by sha256. On this host
        # the reviewed build lives in the portable runtime tree, not in the per-user
        # install directory the launcher falls back to, so point the launcher at it.
        $PortableOllama = Join-Path $RepoRoot "tmp\selfhost-ai-runtime\ollama-0.34.4-portable\ollama.exe"
        $previousOllamaExe = $env:PROPERTY_AI_OLLAMA_EXE
        if (-not $env:PROPERTY_AI_OLLAMA_EXE -and (Test-Path -LiteralPath $PortableOllama -PathType Leaf)) {
            $env:PROPERTY_AI_OLLAMA_EXE = $PortableOllama
        }
        # 2) the versioned prompt selection travels with the process tree
        $env:PROPERTY_AI_PROMPT_VERSIONS_FILE = $PromptVersionsPath
        $previousOverride = $env:PROPERTY_AI_ALLOWED_WORK_PROMPT_VERSION
        $env:PROPERTY_AI_ALLOWED_WORK_PROMPT_VERSION = $null
        try {
            # 3) reviewed launchers do the real work
            $ollama = & $OllamaLauncher start
            if ($LASTEXITCODE -ne 0) { throw "ollama runtime launcher failed" }
            $work = & $WorkAiLauncher start
            if ($LASTEXITCODE -ne 0) { throw "work AI runtime launcher failed" }
            $current = Read-Json $CurrentPath
            $token = Read-Token
            if (-not $token) { throw "internal token is missing after start" }
            Write-Output "--- ollama runtime ---"
            Write-Output $ollama
            Write-Output "--- work AI runtime ---"
            Write-Output $work
            Write-Output "--- verify ---"
            Assert-Health $token $current
        } finally {
            $env:PROPERTY_AI_ALLOWED_WORK_PROMPT_VERSION = $previousOverride
            $env:PROPERTY_AI_OLLAMA_EXE = $previousOllamaExe
        }
    }
    "down" {
        $pids = @(Get-PortListenerPids $WorkPort)
        if ($pids.Count -gt 0) {
            if ($null -ne (Read-Json $CurrentPath)) {
                & $WorkAiLauncher stop
                if ($LASTEXITCODE -ne 0) {
                    Write-Output "recorded adapter refused a clean stop; falling back to exact pid"
                    Stop-StrayPythonListener $WorkPort
                }
            } else {
                Write-Output "11437 is owned by an unrecorded adapter; stopping exact pid"
                Stop-StrayPythonListener $WorkPort
            }
        }
        Clear-WorkAiState
        if (-not $KeepOllama) {
            $ollamaPids = @(Get-PortListenerPids 11435)
            if ($ollamaPids.Count -gt 0) {
                try { & $OllamaLauncher stop } catch { Stop-StrayPythonListener 11435 }
            }
            if (@(Get-PortListenerPids 11435).Count -eq 0 -and (Test-Path -LiteralPath $OllamaCurrentPath -PathType Leaf)) {
                Remove-Item -LiteralPath $OllamaCurrentPath -Force
                Write-Output "cleared Ollama runtime receipt"
            }
        }
        Write-Output "work AI runtime is down; token, models and call ledger were retained"
    }
    "status" {
        $current = Read-Json $CurrentPath
        if ($null -eq $current) { throw "work AI runtime has no current receipt; run: deploy\ai-runtime.ps1 up" }
        $token = Read-Token
        if (-not $token) { throw "internal token is missing" }
        [ordered]@{
            receipt = $current
            health = (Invoke-Api "/healthz" "GET" $token)
            versioned_prompt_versions = @(Get-PromptVersionsFile)
            prompt_versions_file = $PromptVersionsPath
            completion_barrier = (Get-CompletionBarrier)
        } | ConvertTo-Json -Depth 8
    }
    "verify" {
        $current = Read-Json $CurrentPath
        if ($null -eq $current) { throw "work AI runtime has no current receipt; run: deploy\ai-runtime.ps1 up" }
        $token = Read-Token
        if (-not $token) { throw "internal token is missing" }
        Assert-Health $token $current
    }
    "resolve-unknown" {
        # PA-132: the shared completion barrier fails closed on purpose. Releasing it used to
        # mean deleting a file by hand, with no record of who did it or why. This action keeps
        # the same gate semantics and only makes the release explicit and auditable: the
        # barrier is archived under resolved/ with an operator receipt, and nothing restarts.
        if (-not $Call) { throw "resolve-unknown requires -Call <call id> (run: deploy\ai-runtime.ps1 status and read completion_barrier.call_id)" }
        if (-not $Reason) { throw "resolve-unknown requires -Reason '<why releasing is safe>'" }
        if (-not (Test-Path -LiteralPath $BarrierTool -PathType Leaf)) { throw "barrier tool is missing: $BarrierTool" }
        $python = $BarrierPython
        if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { $python = "python" }
        $result = & $python -I -B -X utf8 $BarrierTool --barrier $BarrierPath resolve --call $Call --reason $Reason
        $exitCode = $LASTEXITCODE
        Write-Output $result
        if ($exitCode -ne 0) { throw "completion barrier was not released (exit $exitCode)" }
        $receiptJson = $result | ConvertFrom-Json
        Write-Output ("completion_barrier=released call={0} archive={1} receipt={2} restart_required={3}" -f $receiptJson.call_id, $receiptJson.barrier.archive_path, $receiptJson.utc, $receiptJson.restart_required)
        Write-Output ("completion_barrier_status={0}" -f (Get-CompletionBarrier).status)
    }
}
