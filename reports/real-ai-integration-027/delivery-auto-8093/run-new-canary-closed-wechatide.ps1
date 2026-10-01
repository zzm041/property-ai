$ErrorActionPreference = "Stop"
$RepoRoot = "D:\property-ai"
$Project = Join-Path $RepoRoot "tmp\delivery-check-miniprogram-20260928"
$Context = Get-Content -Raw -LiteralPath (Join-Path $RepoRoot "tmp\delivery-auto-8093\fixture-context.json") | ConvertFrom-Json
$OutDir = Join-Path $RepoRoot "reports\real-ai-integration-027\delivery-auto-8093"
$ResultPath = Join-Path $OutDir "new-image-canary-12-closed-wechatide.json"
$WechatIde = "D:\微信web开发者工具\wechatide.cmd"
$WorkOrderId = 12
$Evidence = [ordered]@{
    task_id = "REAL-AI-INTEGRATION-027-NEW-CANARY-CLOSED"
    work_order_id = $WorkOrderId
    interaction = "WeChat DevTools actual page inputs and button taps"
    credentials_saved = $false
    direct_database_business_write = $false
    manual_assignment = $false
    screenshots = @()
    stages = @()
}

if (Test-Path -LiteralPath $ResultPath) { throw "closed-page result already exists; refusing duplicate lifecycle actions" }

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
    if ($result.ok -ne $true -or ($result.result.success -eq $false -and -not $AllowResultFailure)) { throw "$Tool did not succeed" }
    return $result.result
}

function Login-Page([string]$Key) {
    $phone = [string]$Context.phones.$Key
    $password = [string]$Context.passwords.$Key
    $null = Invoke-WechatIde "automation_navigate" @("--project", $Project, "--action", "reLaunch", "--url", "/pages/home/home") $phone $password
    Start-Sleep -Milliseconds 800
    $page = Invoke-WechatIde "automation_runtime_info" @("--project", $Project, "--action", "currentPage") $phone $password
    if ($page.currentPage.path -eq "pages/home/home") {
        $null = Invoke-WechatIde "automation_page_action" @("--project", $Project, "--action", "callMethod", "--method", "logout") $phone $password
        Start-Sleep -Milliseconds 800
    }
    $page = Invoke-WechatIde "automation_runtime_info" @("--project", $Project, "--action", "currentPage") $phone $password
    if ($page.currentPage.path -ne "pages/login/login") { throw "logout did not reach login page for $Key" }
    $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "input", "--selector", "input[placeholder='手机号']", "--value", $phone, "--wait-for-selector", "input[placeholder='手机号']") $phone $password
    $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "input", "--selector", "input[placeholder='密码']", "--value", $password, "--wait-for-selector", "input[placeholder='密码']") $phone $password
    $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "tap", "--selector", "button.btn", "--wait-for-selector", "button.btn") $phone $password $true
    Start-Sleep -Seconds 2
    $page = Invoke-WechatIde "automation_runtime_info" @("--project", $Project, "--action", "currentPage") $phone $password
    if ($page.currentPage.path -ne "pages/home/home") { throw "normal password login did not reach home for $Key" }
    return [ordered]@{ phone = $phone; password = $password }
}

function Page-Data([System.Collections.IDictionary]$Auth) {
    return Invoke-WechatIde "automation_page_action" @("--project", $Project, "--action", "getData") $Auth.phone $Auth.password
}

function Wait-Status([System.Collections.IDictionary]$Auth, [string]$Expected) {
    for ($i = 0; $i -lt 30; $i += 1) {
        $data = Page-Data $Auth
        if ([string]$data.data.workOrder.status -eq $Expected) { return $data.data }
        if ($data.data.error) { throw "page error while waiting for $Expected" }
        Start-Sleep -Milliseconds 500
    }
    throw "page status did not reach $Expected"
}

function Screenshot-Page([System.Collections.IDictionary]$Auth, [string]$Name) {
    $null = Invoke-WechatIde "simulator_screenshot" @("--project", $Project, "--path", (Join-Path $OutDir $Name), "--optimize", "false") $Auth.phone $Auth.password
    $Evidence.screenshots += $Name
}

try {
    $staff = Login-Page "staffB1"
    $null = Invoke-WechatIde "automation_navigate" @("--project", $Project, "--action", "navigateTo", "--url", "/pages/staff-orders/detail?id=$WorkOrderId") $staff.phone $staff.password
    $data = Wait-Status $staff "ASSIGNED"
    $Evidence.stages += [ordered]@{ actor = "staffB1"; status = $data.workOrder.status; action = "view_auto_assignment" }
    Screenshot-Page $staff "23-new-canary-auto-assigned-staff.png"

    $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "tap", "--selector", "#accept-button", "--wait-for-selector", "#accept-button") $staff.phone $staff.password
    $data = Wait-Status $staff "ACCEPTED"
    $Evidence.stages += [ordered]@{ actor = "staffB1"; status = $data.workOrder.status; action = "tap_accept" }
    $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "tap", "--selector", "#start-button", "--wait-for-selector", "#start-button") $staff.phone $staff.password
    $data = Wait-Status $staff "PROCESSING"
    $Evidence.stages += [ordered]@{ actor = "staffB1"; status = $data.workOrder.status; action = "tap_start" }
    Screenshot-Page $staff "24-new-canary-processing.png"

    $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "input", "--selector", "#problem-type", "--value", "阀门渗水检查", "--wait-for-selector", "#problem-type") $staff.phone $staff.password
    $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "input", "--selector", "#method", "--value", "已检查阀门和连接位置，完成紧固与密封处理；复测后无继续渗水，现场已清理。", "--wait-for-selector", "#method") $staff.phone $staff.password
    $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "input", "--selector", "#materials", "--value", "密封材料", "--wait-for-selector", "#materials") $staff.phone $staff.password
    $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "input", "--selector", "#follow-up", "--value", "住户后续观察，如再渗水可重新反馈", "--wait-for-selector", "#follow-up") $staff.phone $staff.password
    $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "tap", "--selector", "#resolve-button", "--wait-for-selector", "#resolve-button") $staff.phone $staff.password
    $data = Wait-Status $staff "WAIT_CONFIRM"
    $Evidence.stages += [ordered]@{ actor = "staffB1"; status = $data.workOrder.status; action = "submit_complete_receipt" }
    Screenshot-Page $staff "25-new-canary-wait-confirm.png"

    $resident = Login-Page "residentB1"
    $null = Invoke-WechatIde "automation_navigate" @("--project", $Project, "--action", "navigateTo", "--url", "/pages/work-orders/detail?id=$WorkOrderId") $resident.phone $resident.password
    $data = Wait-Status $resident "WAIT_CONFIRM"
    if ($data.autoDispatched -ne $true) { throw "resident page did not show AUTO dispatch" }
    $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "tap", "--selector", "#confirm-button", "--wait-for-selector", "#confirm-button") $resident.phone $resident.password
    $data = Wait-Status $resident "CLOSED"
    $Evidence.stages += [ordered]@{ actor = "residentB1"; status = $data.workOrder.status; action = "tap_confirm"; auto_dispatched = [bool]$data.autoDispatched; receipt_present = [bool]$data.latestReceipt }
    Screenshot-Page $resident "26-new-canary-resident-closed.png"

    [IO.File]::WriteAllText($ResultPath, ($Evidence | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
    [ordered]@{ work_order_id = $WorkOrderId; status = $data.workOrder.status; screenshots = $Evidence.screenshots } | ConvertTo-Json -Depth 4
} catch {
    $message = [string]$_.Exception.Message
    foreach ($value in @($Context.phones.PSObject.Properties.Value) + @($Context.passwords.PSObject.Properties.Value)) {
        $message = $message.Replace([string]$value, "[redacted]")
    }
    $Evidence.error = $message
    [IO.File]::WriteAllText($ResultPath, ($Evidence | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
    throw $message
}
