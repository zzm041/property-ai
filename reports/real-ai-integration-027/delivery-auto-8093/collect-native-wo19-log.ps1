$ErrorActionPreference = "Stop"
$RepoRoot = "D:\property-ai"
$Context = Get-Content -Raw -LiteralPath (Join-Path $RepoRoot "tmp\delivery-auto-8093\fixture-context.json") | ConvertFrom-Json
$ResultPath = Join-Path $RepoRoot "reports\real-ai-integration-027\delivery-auto-8093\native-wo19-log-result.json"
$BaseUrl = "http://127.0.0.1:8093/api/v1"
if (Test-Path -LiteralPath $ResultPath) { throw "log result evidence exists; refusing replacement" }
$body = @{ phone=[string]$Context.phones.managerA; password=[string]$Context.passwords.managerA } | ConvertTo-Json -Compress
$login = Invoke-RestMethod -NoProxy -Method Post -Uri "$BaseUrl/auth/password/login" -ContentType "application/json" -Body $body -Headers @{"X-Request-ID"="ai027-native-wo19-log-login"}
$headers = @{ Authorization = "Bearer $([string]$login.data.access_token)"; "X-Request-ID"="ai027-native-wo19-log" }
$detail = Invoke-RestMethod -NoProxy -Method Get -Uri "$BaseUrl/work-orders/19" -Headers $headers
$logs = @($detail.data.logs | ForEach-Object {
    [ordered]@{
        id = [long]$_.id
        actor_type = [string]$_.actor_type
        from_status = [string]$_.from_status
        to_status = [string]$_.to_status
        process_reason = [string]$_.process_reason
        event_code = [string]$_.event_code
        reason = [string]$_.reason
        created_at = [string]$_.created_at
    }
})
$evidence = [ordered]@{
    task_id = "REAL-AI-INTEGRATION-027"
    work_order_id = 19
    logs = $logs
    sensitive_text_saved = $false
    credentials_or_tokens_saved = $false
}
[IO.File]::WriteAllText($ResultPath, ($evidence | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
$evidence | ConvertTo-Json -Depth 6
