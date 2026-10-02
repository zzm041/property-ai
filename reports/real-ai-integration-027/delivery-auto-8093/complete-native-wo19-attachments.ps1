$ErrorActionPreference = "Stop"
$RepoRoot = "D:\property-ai"
$Context = Get-Content -Raw -LiteralPath (Join-Path $RepoRoot "tmp\delivery-auto-8093\fixture-context.json") | ConvertFrom-Json
$ResultPath = Join-Path $RepoRoot "reports\real-ai-integration-027\delivery-auto-8093\native-wo19-attachment-completion-request.json"
$BaseUrl = "http://127.0.0.1:8093/api/v1"
$SourceIntentId = 170
$WorkOrderId = 19

if (Test-Path -LiteralPath $ResultPath) {
    throw "attachment-completion request evidence already exists; refusing a duplicate request"
}

$loginBody = @{
    phone = [string]$Context.phones.managerA
    password = [string]$Context.passwords.managerA
} | ConvertTo-Json -Compress
$login = Invoke-RestMethod -NoProxy -Method Post -Uri "$BaseUrl/auth/password/login" -ContentType "application/json" -Body $loginBody -Headers @{"X-Request-ID"="ai027-native-wo19-attachment-login"}
$token = [string]$login.data.access_token
if ($token.Length -lt 32) { throw "normal manager login did not issue a session" }
$headers = @{ Authorization = "Bearer $token"; "X-Request-ID" = "ai027-native-wo19-attachment-completion" }

$before = Invoke-RestMethod -NoProxy -Method Get -Uri "$BaseUrl/admin/task-intents/$SourceIntentId" -Headers $headers
if (
    [long]$before.data.resource_id -ne $WorkOrderId -or
    $before.data.operation -ne "ANALYZE_WORK_ORDER" -or
    $before.data.status -ne "SUCCEEDED" -or
    [long]$before.data.generation -ne 3
) {
    throw "source task is not the expected successful native-media generation 3"
}

$body = @{ reason_code = "ATTACHMENT_COMPLETION" } | ConvertTo-Json -Compress
$completion = Invoke-RestMethod -NoProxy -Method Post -Uri "$BaseUrl/admin/task-intents/$SourceIntentId/complete-analysis" -ContentType "application/json" -Body $body -Headers $headers
if (
    [long]$completion.data.resource_id -ne $WorkOrderId -or
    [long]$completion.data.generation -ne 4 -or
    $completion.data.status -ne "PENDING"
) {
    throw "attachment-completion successor did not match the approved generation 4"
}

$evidence = [ordered]@{
    task_id = "REAL-AI-INTEGRATION-027-NATIVE-MEDIA-ATTACHMENT-COMPLETION"
    work_order_id = $WorkOrderId
    source_intent = [ordered]@{
        id = [long]$before.data.id
        generation = [long]$before.data.generation
        status = [string]$before.data.status
    }
    successor_intent = [ordered]@{
        id = [long]$completion.data.id
        generation = [long]$completion.data.generation
        status = [string]$completion.data.status
    }
    reason_code = "ATTACHMENT_COMPLETION"
    normal_password_manager_session = $true
    attachment_logic_source_commit = "1451e5572932ca7d4261e743110c7b27aeb743e6"
    runtime_source_commit = "17581e4e6a8534e55c181084ec4d201b2025ea92"
    new_work_order_created = $false
    old_analysis_or_task_rewritten = $false
    direct_database_write = $false
    manual_analysis_or_assignment = $false
    credentials_or_tokens_saved = $false
}
[IO.File]::WriteAllText($ResultPath, ($evidence | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
$evidence | ConvertTo-Json -Depth 6
