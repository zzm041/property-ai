$ErrorActionPreference = "Stop"
$RepoRoot = "D:\property-ai"
$Project = Join-Path $RepoRoot "tmp\delivery-check-miniprogram-20260928"
$Context = Get-Content -Raw -LiteralPath (Join-Path $RepoRoot "tmp\delivery-auto-8093\fixture-context.json") | ConvertFrom-Json
$OutDir = Join-Path $RepoRoot "reports\real-ai-integration-027\delivery-auto-8093"
$ResultPath = Join-Path $OutDir "overlap-staffA-checkins.json"
$WechatIde = "D:\微信web开发者工具\wechatide.cmd"
$PropertyId = [long]$Context.organizations[0].property_id
$Evidence = [ordered]@{
    task_id = "REAL-AI-INTEGRATION-027-OVERLAP"
    property_id = $PropertyId
    normal_password_page_login = $true
    actual_checkin_button = $true
    direct_database_write = $false
    manual_assignment = $false
    credentials_saved = $false
    checkins = @()
}

if (Test-Path -LiteralPath $ResultPath) { throw "check-in evidence exists; refusing duplicate actions" }

function Invoke-WechatIde([string]$Tool, [string[]]$Arguments, [string]$Phone, [string]$Password, [bool]$AllowResultFailure = $false) {
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
    if ($result.ok -ne $true -or ($result.result.success -ne $true -and -not $AllowResultFailure)) { throw "$Tool did not succeed" }
    return $result.result
}

try {
    $shotIndex = 21
    foreach ($key in @("staffA1", "staffA2")) {
        $phone = [string]$Context.phones.$key
        $password = [string]$Context.passwords.$key
        $null = Invoke-WechatIde "automation_navigate" @("--project", $Project, "--action", "reLaunch", "--url", "/pages/home/home") $phone $password
        Start-Sleep -Seconds 1
        $page = Invoke-WechatIde "automation_runtime_info" @("--project", $Project, "--action", "currentPage") $phone $password
        if ($page.currentPage.path -eq "pages/home/home") {
            $null = Invoke-WechatIde "automation_page_action" @("--project", $Project, "--action", "callMethod", "--method", "logout") $phone $password
            Start-Sleep -Seconds 1
        }
        $page = Invoke-WechatIde "automation_runtime_info" @("--project", $Project, "--action", "currentPage") $phone $password
        if ($page.currentPage.path -ne "pages/login/login") { throw "logout did not reach login page for $key" }
        $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "input", "--selector", "input[placeholder='手机号']", "--value", $phone, "--wait-for-selector", "input[placeholder='手机号']") $phone $password
        $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "input", "--selector", "input[placeholder='密码']", "--value", $password, "--wait-for-selector", "input[placeholder='密码']") $phone $password
        $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "tap", "--selector", "button.btn", "--wait-for-selector", "button.btn") $phone $password $true
        Start-Sleep -Seconds 2
        $page = Invoke-WechatIde "automation_runtime_info" @("--project", $Project, "--action", "currentPage") $phone $password
        if ($page.currentPage.path -ne "pages/home/home") { throw "normal password login did not reach home for $key" }
        $null = Invoke-WechatIde "automation_navigate" @("--project", $Project, "--action", "navigateTo", "--url", "/pages/attendance/attendance?propertyId=$PropertyId") $phone $password
        $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "tap", "--selector", "button.btn", "--wait-for-selector", "button.btn") $phone $password $true
        Start-Sleep -Seconds 2
        $data = Invoke-WechatIde "automation_page_action" @("--project", $Project, "--action", "getData") $phone $password
        if (-not $data.data.snapshot.business_date -or $data.data.error) { throw "staff check-in failed for $key" }
        $shotName = "$shotIndex-overlap-$key-checkin.png"
        $null = Invoke-WechatIde "simulator_screenshot" @("--project", $Project, "--path", (Join-Path $OutDir $shotName), "--optimize", "false") $phone $password
        $Evidence.checkins += [ordered]@{ key = $key; business_date = [string]$data.data.snapshot.business_date; screenshot = $shotName }
        $shotIndex += 1
    }
    [IO.File]::WriteAllText($ResultPath, ($Evidence | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
    $Evidence | ConvertTo-Json -Depth 6
} catch {
    $Evidence.error = [string]$_.Exception.Message
    [IO.File]::WriteAllText($ResultPath, ($Evidence | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
    throw
}
