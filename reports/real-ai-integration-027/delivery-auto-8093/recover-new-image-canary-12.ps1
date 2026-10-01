$ErrorActionPreference = "Stop"
$RepoRoot = "D:\property-ai"
$Context = Get-Content -Raw -LiteralPath (Join-Path $RepoRoot "tmp\delivery-auto-8093\fixture-context.json") | ConvertFrom-Json
$ResultPath = Join-Path $RepoRoot "reports\real-ai-integration-027\delivery-auto-8093\new-image-canary-12-recovery.json"
$BaseUrl = "http://127.0.0.1:8093/api/v1"
$SourceIntentId = 50
$WorkOrderId = 12

if (Test-Path -LiteralPath $ResultPath) { throw "recovery evidence exists; refusing duplicate recovery" }

$phone = [string]$Context.phones.managerB
$password = [string]$Context.passwords.managerB
$loginBody = @{ phone = $phone; password = $password } | ConvertTo-Json -Compress
$login = Invoke-RestMethod -NoProxy -Method Post -Uri "$BaseUrl/auth/password/login" -ContentType "application/json" -Body $loginBody -Headers @{"X-Request-ID"="ai027-canary12-recovery-login"}
$token = [string]$login.data.access_token
if ($token.Length -lt 32) { throw "normal manager login did not issue a session" }
$headers = @{ Authorization = "Bearer $token"; "X-Request-ID" = "ai027-canary12-recovery" }

$before = Invoke-RestMethod -NoProxy -Method Get -Uri "$BaseUrl/admin/task-intents/$SourceIntentId" -Headers $headers
if ([long]$before.data.resource_id -ne $WorkOrderId -or $before.data.operation -ne "ANALYZE_WORK_ORDER" -or $before.data.status -ne "FAILED") {
    throw "source task is not the expected failed canary analysis"
}
$retryBody = @{ reason_code = "DEPENDENCY_RECOVERED" } | ConvertTo-Json -Compress
$retry = Invoke-RestMethod -NoProxy -Method Post -Uri "$BaseUrl/admin/task-intents/$SourceIntentId/retry" -ContentType "application/json" -Body $retryBody -Headers $headers
if ([long]$retry.data.resource_id -ne $WorkOrderId -or [long]$retry.data.generation -ne ([long]$before.data.generation + 1) -or $retry.data.status -ne "PENDING") {
    throw "recovery successor did not match the exact canary task"
}

$evidence = [ordered]@{
    task_id = "REAL-AI-INTEGRATION-027"
    work_order_id = $WorkOrderId
    source_intent = [ordered]@{
        id = [long]$before.data.id
        generation = [long]$before.data.generation
        status = [string]$before.data.status
        error_code = [string]$before.data.last_error_code
    }
    successor_intent = [ordered]@{
        id = [long]$retry.data.id
        generation = [long]$retry.data.generation
        status = [string]$retry.data.status
    }
    reason_code = "DEPENDENCY_RECOVERED"
    normal_password_manager_session = $true
    direct_database_write = $false
    manual_analysis_or_assignment = $false
    credentials_or_tokens_saved = $false
    adapter_source_commit = "f234402629d621a26eb1ce9f9077a465d5c0b5eb"
}
[IO.File]::WriteAllText($ResultPath, ($evidence | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
$evidence | ConvertTo-Json -Depth 6
