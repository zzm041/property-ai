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
$ModelRoot = Join-Path $RepoRoot "tmp\selfhost-ai-local\models"
$OllamaExe = if ($env:PROPERTY_AI_OLLAMA_EXE) { $env:PROPERTY_AI_OLLAMA_EXE } else { "C:\Users\lmj\AppData\Local\Programs\Ollama\ollama.exe" }
$ExpectedExeSha256 = "D571E4CF0AD4456FCCD33D5B985B6D63BA5669C6E5A812391704083952AAE3D5"
$ExpectedVersion = "0.34.4"
$Endpoint = "127.0.0.1:11438"
$ContextLength = 4096

function Get-ListenerPid {
    $ids = @()
    foreach ($line in (& netstat -ano -p TCP)) {
        if ($line -match "^\s*TCP\s+127\.0\.0\.1:11438\s+\S+\s+LISTENING\s+(\d+)\s*$") { $ids += [int]$Matches[1] }
    }
    if ($ids.Count -gt 1) { throw "multiple listeners found on the RAG Ollama port" }
    if ($ids.Count -eq 1) { return $ids[0] }
    return $null
}
function Read-State { if (Test-Path -LiteralPath $StatePath -PathType Leaf) { Get-Content -Raw -LiteralPath $StatePath | ConvertFrom-Json } }
function Invoke-Json([string]$Path) {
    $handler=[Net.Http.HttpClientHandler]::new();$handler.UseProxy=$false
    $client=[Net.Http.HttpClient]::new($handler)
    try { $client.Timeout=[TimeSpan]::FromSeconds(2);return $client.GetStringAsync("http://127.0.0.1:11438$Path").GetAwaiter().GetResult() | ConvertFrom-Json }
    finally { $client.Dispose();$handler.Dispose() }
}
function Assert-State([object]$State) {
    $process=Get-Process -Id ([int]$State.pid) -ErrorAction Stop
    $started=([DateTimeOffset]$State.started_at_utc).UtcDateTime
    if ((Get-ListenerPid) -ne $process.Id -or [IO.Path]::GetFullPath($process.Path) -ne [IO.Path]::GetFullPath($OllamaExe) -or
        [Math]::Abs(($process.StartTime.ToUniversalTime()-$started).TotalMilliseconds) -gt 1 -or
        (Get-FileHash -Algorithm SHA256 -LiteralPath $process.Path).Hash -ne $ExpectedExeSha256 -or
        $State.host -ne $Endpoint -or [int]$State.context_length -ne $ContextLength -or $State.no_cloud -ne $true) {
        throw "RAG Ollama receipt does not bind the current isolated process"
    }
    $version=Invoke-Json "/api/version"
    if ($version.version -ne $ExpectedVersion) { throw "RAG Ollama version changed" }
    return $process
}

switch ($Action) {
    "start" {
        New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
        foreach($path in @($OllamaExe,$ModelRoot)){if(-not(Test-Path -LiteralPath $path)){throw "required RAG Ollama asset is missing"}}
        if((Get-FileHash -Algorithm SHA256 -LiteralPath $OllamaExe).Hash -ne $ExpectedExeSha256){throw "RAG Ollama executable hash changed"}
        $listener=Get-ListenerPid;$state=Read-State
        if($listener){if($null -eq $state){throw "11438 is occupied without a receipt"};$null=Assert-State $state;$state|ConvertTo-Json -Depth 6;break}
        if($null -ne $state){throw "stale RAG Ollama receipt requires explicit evidence; start refused"}
        $id=[guid]::NewGuid().ToString("N");$stdout=Join-Path $RuntimeDir "server-$id.stdout.log";$stderr=Join-Path $RuntimeDir "server-$id.stderr.log"
        $saved=@{host=$env:OLLAMA_HOST;models=$env:OLLAMA_MODELS;cloud=$env:OLLAMA_NO_CLOUD;context=$env:OLLAMA_CONTEXT_LENGTH}
        try {
            $env:OLLAMA_HOST=$Endpoint;$env:OLLAMA_MODELS=$ModelRoot;$env:OLLAMA_NO_CLOUD="1";$env:OLLAMA_NOPRUNE="1"
            $env:OLLAMA_MAX_LOADED_MODELS="1";$env:OLLAMA_NUM_PARALLEL="1";$env:OLLAMA_CONTEXT_LENGTH="4096";$env:OLLAMA_MAX_QUEUE="2"
            $env:OLLAMA_KEEP_ALIVE="30s";$env:OLLAMA_LOAD_TIMEOUT="120s";$env:OLLAMA_IGPU_ENABLE="0";$env:OLLAMA_DEBUG="0"
            $process=Start-Process -FilePath $OllamaExe -ArgumentList "serve" -WorkingDirectory $RepoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        } finally { $env:OLLAMA_HOST=$saved.host;$env:OLLAMA_MODELS=$saved.models;$env:OLLAMA_NO_CLOUD=$saved.cloud;$env:OLLAMA_CONTEXT_LENGTH=$saved.context }
        $state=[ordered]@{format="property-ai-delivery-rag-ollama-runtime-v1";operation_id=$id;started_at_utc=$process.StartTime.ToUniversalTime().ToString("o");pid=$process.Id;executable=$OllamaExe;executable_sha256=$ExpectedExeSha256;host=$Endpoint;models=$ModelRoot;stdout=$stdout;stderr=$stderr;no_cloud=$true;max_loaded_models=1;concurrency=1;context_length=4096;inference_on_start=$false}
        [IO.File]::WriteAllText($StatePath,($state|ConvertTo-Json -Depth 6),[Text.UTF8Encoding]::new($false))
        $deadline=[DateTime]::UtcNow.AddSeconds(15);do{Start-Sleep -Milliseconds 250;$process.Refresh();if($process.HasExited){throw "RAG Ollama exited during startup"};try{$version=Invoke-Json "/api/version"}catch{$version=$null}}until($version -or [DateTime]::UtcNow -ge $deadline)
        $null=Assert-State $state;$tags=Invoke-Json "/api/tags";$resident=Invoke-Json "/api/ps"
        if(-not(@($tags.models)|Where-Object{$_.name -eq "qwen3.5:4b" -and $_.digest -eq "2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd"}) -or @($resident.models).Count -ne 0){throw "RAG 4B visibility or no-inference startup check failed"}
        $state|ConvertTo-Json -Depth 6
    }
    "status" { $state=Read-State;if($null -eq $state){throw "RAG Ollama has no current receipt"};$null=Assert-State $state;$resident=Invoke-Json "/api/ps";[ordered]@{state=$state;resident_models=@($resident.models).Count;inference_on_status=$false}|ConvertTo-Json -Depth 7 }
    "stop" { $state=Read-State;if($null -eq $state){throw "RAG Ollama has no current receipt"};$process=Assert-State $state;if((Invoke-Json "/api/ps").models.Count -ne 0){throw "RAG model is resident"};if((Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 11436 -State Listen -ErrorAction SilentlyContinue)){throw "RAG adapter is still listening"};& taskkill /PID $process.Id /T /F | Out-Null;if($LASTEXITCODE -ne 0){throw "RAG Ollama stop failed"};$archive=Join-Path $RuntimeDir ("stopped-{0}.json" -f [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ"));Move-Item -LiteralPath $StatePath -Destination $archive }
}
