$ErrorActionPreference = "Stop"
$RepoRoot = "D:\property-ai"
$Context = Get-Content -Raw -LiteralPath (Join-Path $RepoRoot "tmp\delivery-auto-8093\fixture-context.json") | ConvertFrom-Json
$ResultPath = Join-Path $RepoRoot "reports\real-ai-integration-027\delivery-auto-8093\native-wo19-attachment-completion-generation7.json"
$BaseUrl = "http://127.0.0.1:8093/api/v1"
$SourceIntentId = 189
$WorkOrderId = 19
$AsrStartRamMiB = 11264

if (Test-Path -LiteralPath $ResultPath) { throw "generation-7 evidence already exists; refusing duplicate recovery" }
$availableRamMiB = [math]::Floor((Get-Counter "\Memory\Available MBytes").CounterSamples.CookedValue)
if ($availableRamMiB -lt $AsrStartRamMiB) { throw "available RAM is below the unchanged ASR start floor" }
$internalToken = (Get-Content -Raw -LiteralPath (Join-Path $RepoRoot "tmp\delivery-work-ai-runtime\internal-token.txt")).Trim()
$health = Invoke-RestMethod -NoProxy -Method Get -Uri "http://127.0.0.1:11437/healthz" -Headers @{Authorization="Bearer $internalToken"}
if ($health.scope -ne "work_order_only" -or $health.accepting_requests -ne $true -or
    $health.request_active -ne $false -or $health.reservation_gate -ne "READY") {
    throw "work AI adapter is not idle, accepting, and reservation-ready"
}
$login = Invoke-RestMethod -NoProxy -Method Post -Uri "$BaseUrl/auth/password/login" -ContentType "application/json" -Body (@{phone=[string]$Context.phones.managerA;password=[string]$Context.passwords.managerA}|ConvertTo-Json -Compress) -Headers @{"X-Request-ID"="ai027-native-wo19-generation7-login"}
$token = [string]$login.data.access_token
if ($token.Length -lt 32) { throw "normal manager login did not issue a session" }
$headers = @{Authorization="Bearer $token";"X-Request-ID"="ai027-native-wo19-generation7"}
$before = Invoke-RestMethod -NoProxy -Method Get -Uri "$BaseUrl/admin/task-intents/$SourceIntentId" -Headers $headers
if ([long]$before.data.resource_id -ne $WorkOrderId -or $before.data.operation -ne "ANALYZE_WORK_ORDER" -or
    $before.data.status -ne "CANCELLED" -or [long]$before.data.generation -ne 6) {
    throw "source task is not the expected cancelled attachment-completion generation 6"
}
$workOrder = (Invoke-RestMethod -NoProxy -Method Get -Uri "$BaseUrl/work-orders/$WorkOrderId" -Headers $headers).data
if ([long]$workOrder.work_order.id -ne $WorkOrderId -or $workOrder.work_order.status -ne "PENDING_ASSIGNMENT" -or
    @($workOrder.assignments).Count -ne 0) { throw "WO19 is no longer the approved unassigned target" }
$retry = Invoke-RestMethod -NoProxy -Method Post -Uri "$BaseUrl/admin/task-intents/$SourceIntentId/retry" -ContentType "application/json" -Body (@{reason_code="DEPENDENCY_RECOVERED"}|ConvertTo-Json -Compress) -Headers $headers
if ([long]$retry.data.resource_id -ne $WorkOrderId -or [long]$retry.data.generation -ne 7 -or $retry.data.status -ne "PENDING") {
    throw "recovery successor did not match attachment-completion generation 7"
}
$evidence=[ordered]@{
    task_id="REAL-AI-INTEGRATION-027-NATIVE-MEDIA-ATTACHMENT-COMPLETION";work_order_id=$WorkOrderId
    source_intent=[ordered]@{id=[long]$before.data.id;generation=[long]$before.data.generation;status=[string]$before.data.status}
    successor_intent=[ordered]@{id=[long]$retry.data.id;generation=[long]$retry.data.generation;status=[string]$retry.data.status}
    reason_code="DEPENDENCY_RECOVERED"
    preflight=[ordered]@{available_ram_mib=$availableRamMiB;asr_start_ram_floor_mib=$AsrStartRamMiB;adapter_idle=$true;reservation_gate=[string]$health.reservation_gate;no_assignment_before_retry=$true}
    required_checkpoint_behavior=[ordered]@{file16_vlm="REUSE";file17_asr="REUSE";file18_asr="EXECUTE_ONCE";final_aggregate_llm="ONLY_AFTER_ALL_ATTACHMENTS_BOUND"}
    runtime_source_commit="3d7c6df"
    normal_password_manager_session=$true;new_work_order_created=$false;direct_database_write=$false;manual_analysis_or_assignment=$false;credentials_or_tokens_saved=$false
}
[IO.File]::WriteAllText($ResultPath,($evidence|ConvertTo-Json -Depth 7),[Text.UTF8Encoding]::new($false))
$evidence|ConvertTo-Json -Depth 7
