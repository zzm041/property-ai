$ErrorActionPreference = "Stop"
$RepoRoot = "D:\property-ai"
$Context = Get-Content -Raw -LiteralPath (Join-Path $RepoRoot "tmp\delivery-auto-8093\fixture-context.json") | ConvertFrom-Json
$ResultPath = Join-Path $RepoRoot "reports\real-ai-integration-027\delivery-auto-8093\native-wo19-dispatch-result.json"
$BaseUrl = "http://127.0.0.1:8093/api/v1"

if (Test-Path -LiteralPath $ResultPath) { throw "dispatch result evidence exists; refusing replacement" }
$body = @{ phone=[string]$Context.phones.managerA; password=[string]$Context.passwords.managerA } | ConvertTo-Json -Compress
$login = Invoke-RestMethod -NoProxy -Method Post -Uri "$BaseUrl/auth/password/login" -ContentType "application/json" -Body $body -Headers @{"X-Request-ID"="ai027-native-wo19-dispatch-login"}
$headers = @{ Authorization = "Bearer $([string]$login.data.access_token)"; "X-Request-ID"="ai027-native-wo19-dispatch" }
$response = Invoke-RestMethod -NoProxy -Method Get -Uri "$BaseUrl/supervisor/work-orders/19/recommendations" -Headers $headers
$items = @($response.data.items | ForEach-Object {
    [ordered]@{
        employee_id = [long]$_.employee_id
        score = [double]$_.score
        eligible = [bool]$_.eligible
        reason_codes = @($_.reason_codes)
        filter_reason = [string]$_.filter_reason
    }
})
$evidence = [ordered]@{
    task_id = "REAL-AI-INTEGRATION-027"
    work_order_id = 19
    query = "read-only dispatch recommendations"
    count = $items.Count
    items = $items
    assignment_created = $false
    credentials_or_tokens_saved = $false
}
[IO.File]::WriteAllText($ResultPath, ($evidence | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
$evidence | ConvertTo-Json -Depth 6
