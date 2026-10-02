$ErrorActionPreference = "Stop"
$RepoRoot = "D:\property-ai"
$Context = Get-Content -Raw -LiteralPath (Join-Path $RepoRoot "tmp\delivery-auto-8093\fixture-context.json") | ConvertFrom-Json
$ResultPath = Join-Path $RepoRoot "reports\real-ai-integration-027\delivery-auto-8093\native-wo19-generation3-result.json"
$BaseUrl = "http://127.0.0.1:8093/api/v1"
$IntentId = 170
$WorkOrderId = 19

if (Test-Path -LiteralPath $ResultPath) { throw "generation-3 result evidence exists; refusing replacement" }

$loginBody = @{
    phone = [string]$Context.phones.managerA
    password = [string]$Context.passwords.managerA
} | ConvertTo-Json -Compress
$login = Invoke-RestMethod -NoProxy -Method Post -Uri "$BaseUrl/auth/password/login" -ContentType "application/json" -Body $loginBody -Headers @{"X-Request-ID"="ai027-native-wo19-generation3-result-login"}
$token = [string]$login.data.access_token
if ($token.Length -lt 32) { throw "normal manager login did not issue a session" }
$headers = @{ Authorization = "Bearer $token"; "X-Request-ID" = "ai027-native-wo19-generation3-result" }

$intent = $null
for ($i = 0; $i -lt 24; $i += 1) {
    $intent = Invoke-RestMethod -NoProxy -Method Get -Uri "$BaseUrl/admin/task-intents/$IntentId" -Headers $headers
    if ($intent.data.status -in @("SUCCEEDED", "FAILED", "CANCELLED")) { break }
    Start-Sleep -Seconds 5
}
if ($null -eq $intent) { throw "intent result was not returned" }

$detail = Invoke-RestMethod -NoProxy -Method Get -Uri "$BaseUrl/work-orders/$WorkOrderId" -Headers $headers
$analysis = Invoke-RestMethod -NoProxy -Method Get -Uri "$BaseUrl/supervisor/work-orders/$WorkOrderId/analysis" -Headers $headers
$asrText = [string]$detail.data.asr_text
$asrHash = $null
if ($asrText) {
    $bytes = [Text.Encoding]::UTF8.GetBytes($asrText)
    $asrHash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes))
}

$calls = @()
foreach ($number in 28..30) {
    $dir = Join-Path $RepoRoot ("tmp\delivery-work-ai-runtime\calls\call-{0:D3}" -f $number)
    $resultFile = Join-Path $dir "result.json"
    $inputFile = Join-Path $dir "input.json"
    if (-not (Test-Path -LiteralPath $resultFile -PathType Leaf)) { continue }
    $result = Get-Content -Raw -LiteralPath $resultFile | ConvertFrom-Json
    $input = Get-Content -Raw -LiteralPath $inputFile | ConvertFrom-Json
    $calls += [ordered]@{
        call = "call-{0:D3}" -f $number
        kind = [string]$input.kind
        status = [string]$result.status
        reason = [string]$result.reason
        model = [string]$result.model
        total_seconds = [double]$result.total_seconds
        owned_exit_confirmed = [bool]$result.owned_exit_confirmed
        cleanup_error = $result.cleanup_error
        remote_completion = [string]$result.remote_completion
    }
}

$evidence = [ordered]@{
    task_id = "REAL-AI-INTEGRATION-027"
    approval_scope = "WO19 generation 3; at most one ASR and two Qwen calls"
    work_order_id = $WorkOrderId
    intent = [ordered]@{
        id = [long]$intent.data.id
        generation = [long]$intent.data.generation
        status = [string]$intent.data.status
        execution_attempt = [long]$intent.data.execution_attempt
        last_error_code = [string]$intent.data.last_error_code
    }
    work_order = [ordered]@{
        status = [string]$detail.data.work_order.status
        process_reason = [string]$detail.data.work_order.process_reason
        media_count = @($detail.data.media).Count
        media_kinds = @($detail.data.media | ForEach-Object { [string]$_.kind })
        assignment_count = @($detail.data.assignments).Count
        assignments = @($detail.data.assignments | ForEach-Object {
            [ordered]@{ id=[long]$_.id; employee_id=[long]$_.employee_id; status=[string]$_.status; reason=[string]$_.reason }
        })
        asr_text_present = [bool]$asrText
        asr_text_sha256 = $asrHash
    }
    analysis = [ordered]@{
        id = [long]$analysis.data.id
        version = [long]$analysis.data.analysis_version
        status = [string]$analysis.data.status
        category = [string]$analysis.data.category
        severity = [string]$analysis.data.severity
        priority = [string]$analysis.data.priority
        risk = [string]$analysis.data.risk
        required_role = [string]$analysis.data.required_role
        required_skill = [string]$analysis.data.required_skill
        confidence = [double]$analysis.data.confidence
        error_code = [string]$analysis.data.error_code
    }
    model_calls = $calls
    model_call_counts = [ordered]@{
        asr = @($calls | Where-Object { $_.kind -eq "asr" }).Count
        qwen = @($calls | Where-Object { $_.kind -eq "qwen" }).Count
    }
    direct_database_write = $false
    manual_analysis_or_assignment = $false
    sensitive_text_saved = $false
}
[IO.File]::WriteAllText($ResultPath, ($evidence | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
$evidence | ConvertTo-Json -Depth 8
