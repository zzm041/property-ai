$ErrorActionPreference = "Stop"
$RepoRoot = "D:\property-ai"
$Project = Join-Path $RepoRoot "tmp\delivery-check-miniprogram-20260928"
$Context = Get-Content -Raw -LiteralPath (Join-Path $RepoRoot "tmp\delivery-auto-8093\fixture-context.json") | ConvertFrom-Json
$OutDir = Join-Path $RepoRoot "reports\real-ai-integration-027\delivery-auto-8093"
$ResultPath = Join-Path $OutDir "native-wo19-integrated-page-final.json"
$ScreenshotPath = Join-Path $OutDir "28-native-wo19-integrated-waiting.png"
$WechatIde = "D:\微信web开发者工具\wechatide.cmd"
$Phone = [string]$Context.phones.residentA1
$Password = [string]$Context.passwords.residentA1
$Evidence = [ordered]@{
    task_id = "REAL-AI-INTEGRATION-027-NATIVE-MEDIA-ATTACHMENT-COMPLETION"
    work_order_id = 19
    interaction = "WeChat DevTools actual resident login and read-only work-order detail after attachment completion"
    credentials_saved = $false
    direct_database_business_write = $false
    manual_analysis_or_assignment = $false
    model_call_from_script = $false
}

if (Test-Path -LiteralPath $ResultPath) { throw "integrated page result already exists; refusing duplicate page run" }

function Invoke-WechatIde([string]$Tool, [string[]]$Arguments, [bool]$AllowResultFailure = $false) {
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $WechatIde
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    foreach ($value in @("-c", "Codex", $Tool) + $Arguments) { $null = $info.ArgumentList.Add($value) }
    $process = [Diagnostics.Process]::Start($info)
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) {
        $safe = ($stderr + " " + $stdout).Replace($Phone, "[redacted-phone]").Replace($Password, "[redacted-password]")
        throw "$Tool failed: $safe"
    }
    $match = [regex]::Match($stdout, "(?s)\{.*\}\s*$")
    if (-not $match.Success) { throw "$Tool returned no structured result" }
    $result = $match.Value | ConvertFrom-Json
    if ($result.ok -ne $true -or ($result.result.success -eq $false -and -not $AllowResultFailure)) { throw "$Tool did not succeed" }
    return $result.result
}

try {
    $null = Invoke-WechatIde "automation_navigate" @("--project", $Project, "--action", "reLaunch", "--url", "/pages/home/home")
    Start-Sleep -Milliseconds 800
    $page = Invoke-WechatIde "automation_runtime_info" @("--project", $Project, "--action", "currentPage")
    if ($page.currentPage.path -eq "pages/home/home") {
        $null = Invoke-WechatIde "automation_page_action" @("--project", $Project, "--action", "callMethod", "--method", "logout")
        Start-Sleep -Milliseconds 800
    }
    $page = Invoke-WechatIde "automation_runtime_info" @("--project", $Project, "--action", "currentPage")
    if ($page.currentPage.path -ne "pages/login/login") { throw "logout did not reach login page" }
    $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "input", "--selector", "input[placeholder='手机号']", "--value", $Phone, "--wait-for-selector", "input[placeholder='手机号']")
    $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "input", "--selector", "input[placeholder='密码']", "--value", $Password, "--wait-for-selector", "input[placeholder='密码']")
    $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "tap", "--selector", "button.btn", "--wait-for-selector", "button.btn") $true
    Start-Sleep -Seconds 2
    $page = Invoke-WechatIde "automation_runtime_info" @("--project", $Project, "--action", "currentPage")
    if ($page.currentPage.path -ne "pages/home/home") { throw "normal resident login did not reach home" }

    $null = Invoke-WechatIde "automation_navigate" @("--project", $Project, "--action", "navigateTo", "--url", "/pages/work-orders/detail?id=19")
    $data = $null
    for ($attempt = 0; $attempt -lt 40; $attempt += 1) {
        $response = Invoke-WechatIde "automation_page_action" @("--project", $Project, "--action", "getData")
        $data = $response.data
        $codes = @($data.logs | ForEach-Object { [string]$_.event_code })
        if (-not $data.loading -and [long]$data.workOrder.id -eq 19 -and @($data.media).Count -eq 3 -and $codes -contains "AI_ATTACHMENT_COMPLETED" -and $codes -contains "AUTO_DISPATCH_WAIT") { break }
        Start-Sleep -Milliseconds 500
    }
    if ($null -eq $data -or [long]$data.workOrder.id -ne 19) { throw "WO19 detail did not load" }
    $codes = @($data.logs | ForEach-Object { [string]$_.event_code })
    if ($codes -notcontains "AI_ATTACHMENT_COMPLETED") { throw "attachment completion event not visible on the refreshed page" }
    if ($codes -notcontains "AUTO_DISPATCH_WAIT") { throw "legal wait event not visible on the refreshed page" }
    if (@($data.media).Count -ne 3) { throw "page does not show all three accepted media records" }
    if (@($data.item.assignments).Count -ne 0) { throw "page unexpectedly shows an assignment" }

    $null = Invoke-WechatIde "simulator_screenshot" @("--project", $Project, "--path", $ScreenshotPath, "--optimize", "false")
    $Evidence.page_result = [ordered]@{
        status = [string]$data.workOrder.status
        status_label = [string]$data.statusLabel
        media = @($data.media | ForEach-Object { [ordered]@{ id = [long]$_.id; file_id = [long]$_.file_id; kind = [string]$_.kind } })
        attachment_completed_event = $true
        legal_wait_event = $true
        assignment_count = @($data.item.assignments).Count
        auto_dispatched = [bool]$data.autoDispatched
        can_append = [bool]$data.canAppend
        can_cancel = [bool]$data.canCancel
    }
    $Evidence.screenshot = Split-Path -Leaf $ScreenshotPath
    [IO.File]::WriteAllText($ResultPath, ($Evidence | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
    $Evidence.page_result | ConvertTo-Json -Depth 6
} catch {
    $message = [string]$_.Exception.Message
    $message = $message.Replace($Phone, "[redacted-phone]").Replace($Password, "[redacted-password]")
    $Evidence.error = $message
    [IO.File]::WriteAllText($ResultPath, ($Evidence | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
    throw $message
}
