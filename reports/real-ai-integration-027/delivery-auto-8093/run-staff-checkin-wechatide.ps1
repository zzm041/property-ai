$ErrorActionPreference = "Stop"
$RepoRoot = "D:\property-ai"
$Project = Join-Path $RepoRoot "tmp\delivery-check-miniprogram-20260928"
$ContextPath = Join-Path $RepoRoot "tmp\delivery-auto-8093\fixture-context.json"
$OutDir = Join-Path $RepoRoot "reports\real-ai-integration-027\delivery-auto-8093"
$ResultPath = Join-Path $OutDir "new-image-canary-staff-checkin.json"
$WechatIde = "D:\微信web开发者工具\wechatide.cmd"
$Client = "Codex"
$Context = Get-Content -Raw -LiteralPath $ContextPath | ConvertFrom-Json
$Phone = [string]$Context.phones.staffB1
$Password = [string]$Context.passwords.staffB1
$PropertyId = [long]$Context.organizations[1].property_id
$Evidence = [ordered]@{
    task_id = "REAL-AI-INTEGRATION-027-NEW-IMAGE-CANARY"
    interaction = "wechatide supported element/page automation"
    property_id = $PropertyId
    normal_password_page_login = $true
    actual_checkin_button = $true
    direct_database_write = $false
    manual_assignment = $false
    credentials_saved = $false
    screenshots = @()
}

if (Test-Path -LiteralPath $ResultPath) { throw "check-in evidence exists; refusing duplicate action" }

function Invoke-WechatIde([string]$Tool, [string[]]$Arguments, [bool]$AllowResultFailure = $false) {
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $WechatIde
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    foreach ($value in @("-c", $Client, $Tool) + $Arguments) { $null = $info.ArgumentList.Add($value) }
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
    if ($result.ok -ne $true -or ($result.result.success -ne $true -and -not $AllowResultFailure)) { throw "$Tool did not succeed" }
    return $result.result
}

try {
    $null = Invoke-WechatIde "automation_navigate" @("--project", $Project, "--action", "reLaunch", "--url", "/pages/home/home")
    Start-Sleep -Seconds 1
    $page = Invoke-WechatIde "automation_runtime_info" @("--project", $Project, "--action", "currentPage")
    if ($page.currentPage.path -eq "pages/home/home") {
        $null = Invoke-WechatIde "automation_page_action" @("--project", $Project, "--action", "callMethod", "--method", "logout")
        Start-Sleep -Seconds 1
    }
    $page = Invoke-WechatIde "automation_runtime_info" @("--project", $Project, "--action", "currentPage")
    if ($page.currentPage.path -ne "pages/login/login") { throw "logout did not reach login page" }
    $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "input", "--selector", "input[placeholder='手机号']", "--value", $Phone, "--wait-for-selector", "input[placeholder='手机号']")
    $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "input", "--selector", "input[placeholder='密码']", "--value", $Password, "--wait-for-selector", "input[placeholder='密码']")
    $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "tap", "--selector", "button.btn", "--wait-for-selector", "button.btn") $true
    Start-Sleep -Seconds 2
    $page = Invoke-WechatIde "automation_runtime_info" @("--project", $Project, "--action", "currentPage")
    if ($page.currentPage.path -ne "pages/home/home") { throw "normal staff password login did not reach home" }

    $null = Invoke-WechatIde "automation_navigate" @("--project", $Project, "--action", "navigateTo", "--url", "/pages/attendance/attendance?propertyId=$PropertyId")
    $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "tap", "--selector", "button.btn", "--wait-for-selector", "button.btn") $true
    Start-Sleep -Seconds 2
    $data = Invoke-WechatIde "automation_page_action" @("--project", $Project, "--action", "getData")
    if (-not $data.data.snapshot.business_date -or $data.data.error) { throw "staff check-in did not persist a clean snapshot" }
    $Evidence.business_date = [string]$data.data.snapshot.business_date
    $Evidence.on_duty = [string]$data.data.snapshot.on_duty
    $shot = Join-Path $OutDir "20-new-canary-staff-checkin.png"
    $null = Invoke-WechatIde "simulator_screenshot" @("--project", $Project, "--path", $shot, "--optimize", "false")
    $Evidence.screenshots += "20-new-canary-staff-checkin.png"
    [IO.File]::WriteAllText($ResultPath, ($Evidence | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
    $Evidence | ConvertTo-Json -Depth 6
} catch {
    $safe = [string]$_.Exception.Message
    $safe = $safe.Replace($Phone, "[redacted-phone]").Replace($Password, "[redacted-password]")
    $Evidence.error = $safe
    [IO.File]::WriteAllText($ResultPath, ($Evidence | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
    throw $safe
}
