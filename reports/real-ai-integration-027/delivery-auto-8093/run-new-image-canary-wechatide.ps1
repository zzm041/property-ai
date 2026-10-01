$ErrorActionPreference = "Stop"
$RepoRoot = "D:\property-ai"
$Project = Join-Path $RepoRoot "tmp\delivery-check-miniprogram-20260928"
$ContextPath = Join-Path $RepoRoot "tmp\delivery-auto-8093\fixture-context.json"
$ImagePath = Join-Path $RepoRoot "reports\auth-identity-ai-001\local-qwen\repair-images-1024\ans-ball-valve.jpg"
$OutDir = Join-Path $RepoRoot "reports\real-ai-integration-027\delivery-auto-8093"
$ResultPath = Join-Path $OutDir "new-image-canary-wechatide-6.json"
$ChoosePath = Join-Path $RepoRoot "tmp\delivery-auto-8093\new-image-canary-choose-media.json"
$DownloadArgsPath = Join-Path $RepoRoot "tmp\delivery-auto-8093\download-canary-image-args.json"
$WechatIde = "D:\微信web开发者工具\wechatide.cmd"
$Client = "Codex"
$Context = Get-Content -Raw -LiteralPath $ContextPath | ConvertFrom-Json
$Marker = "$($Context.run_id)-NEW-CANARY-VALVE-20261002"
$Description = "$Marker：卫生间阀门开关费力且接口有少量渗水，请统一维修组联系检查。"
$Phone = [string]$Context.phones.residentB1
$Password = [string]$Context.passwords.residentB1
$HouseId = [long]$Context.organizations[1].house_ids[0]
$Evidence = [ordered]@{
    task_id = "REAL-AI-INTEGRATION-027-NEW-IMAGE-CANARY"
    run_id = $Context.run_id
    interaction = "wechatide supported element/page/wx API automation"
    marker_sha256 = ([BitConverter]::ToString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($Marker)))).Replace("-", "")
    source_image_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $ImagePath).Hash
    submit_started = $false
    stage = "initialized"
    screenshots = @()
    boundaries = [ordered]@{
        normal_password_page_login = $true
        actual_page_controls = $true
        choose_media_dialog_only_mocked = $true
        direct_database_business_write = $false
        manual_enqueue_or_assign = $false
        model_call_from_script = $false
        credentials_saved = $false
    }
}

if (Test-Path -LiteralPath $ResultPath) { throw "canary result exists; refusing duplicate submission" }
if ($Phone -notmatch "^(\+86)?\d{11}$" -or $Password.Length -lt 8) { throw "private synthetic credential is invalid" }

function Save-Evidence {
    [IO.File]::WriteAllText($ResultPath, ($Evidence | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
}

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
        $Evidence.stage = "logout_existing_session"
        Save-Evidence
        $null = Invoke-WechatIde "automation_page_action" @("--project", $Project, "--action", "callMethod", "--method", "logout")
        Start-Sleep -Seconds 1
    }
    $page = Invoke-WechatIde "automation_runtime_info" @("--project", $Project, "--action", "currentPage")
    if ($page.currentPage.path -ne "pages/login/login") { throw "logout did not reach login page" }
    $Evidence.stage = "resident_login"
    Save-Evidence
    $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "input", "--selector", "input[placeholder='手机号']", "--value", $Phone, "--wait-for-selector", "input[placeholder='手机号']")
    $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "input", "--selector", "input[placeholder='密码']", "--value", $Password, "--wait-for-selector", "input[placeholder='密码']")
    $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "tap", "--selector", "button.btn", "--wait-for-selector", "button.btn") $true
    Start-Sleep -Seconds 2
    $page = Invoke-WechatIde "automation_runtime_info" @("--project", $Project, "--action", "currentPage")
    if ($page.currentPage.path -ne "pages/home/home") { throw "normal password login did not reach home" }
    $Evidence.login = [ordered]@{ mode = "normal password page login"; page = $page.currentPage.path }

    $Evidence.stage = "work_order_form"
    Save-Evidence
    $null = Invoke-WechatIde "automation_navigate" @("--project", $Project, "--action", "navigateTo", "--url", "/pages/work-orders/create")
    $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "tap", "--selector", ".house-choice", "--wait-for-selector", ".house-choice")
    $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "input", "--selector", "#work-order-description", "--value", $Description)
    $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "input", "--selector", "#work-order-contact", "--value", $Phone)
    $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "trigger", "--selector", "#confirm-work-order-contact", "--type", "change", "--detail", '{"value":["confirmed"]}')

    $download = Invoke-WechatIde "automation_wx_api" @("--project", $Project, "--action", "call", "--method", "downloadFile", "--args-file", $DownloadArgsPath)
    if ($download.result.statusCode -ne 200 -or -not $download.result.tempFilePath) { throw "simulator image download failed" }
    $image = Get-Item -LiteralPath $ImagePath
    $choose = [ordered]@{ type = "image"; tempFiles = @([ordered]@{ tempFilePath = [string]$download.result.tempFilePath; size = $image.Length; fileType = "image" }) }
    [IO.File]::WriteAllText($ChoosePath, ($choose | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
    $null = Invoke-WechatIde "automation_wx_api" @("--project", $Project, "--action", "mock", "--method", "chooseMedia", "--result-file", $ChoosePath)
    try {
        $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "tap", "--selector", "#choose-work-order-image")
        Start-Sleep -Seconds 2
        $data = Invoke-WechatIde "automation_page_action" @("--project", $Project, "--action", "getData")
        if (@($data.data.fileIds).Count -ne 1 -or $data.data.contactConfirmed -ne $true) { throw "page media/contact state is incomplete" }
        $Evidence.file_ids = @($data.data.fileIds)
    } finally {
        $null = Invoke-WechatIde "automation_wx_api" @("--project", $Project, "--action", "restore", "--method", "chooseMedia")
    }

    $formShot = Join-Path $OutDir "18g-new-canary-image-form.png"
    $null = Invoke-WechatIde "simulator_screenshot" @("--project", $Project, "--path", $formShot, "--optimize", "false")
    $Evidence.screenshots += "18g-new-canary-image-form.png"
    $Evidence.stage = "submit_work_order"
    $Evidence.submit_started = $true
    Save-Evidence
    $null = Invoke-WechatIde "automation_element_action" @("--project", $Project, "--action", "tap", "--selector", "#submit-work-order") $true
    Start-Sleep -Seconds 3
    $page = Invoke-WechatIde "automation_runtime_info" @("--project", $Project, "--action", "currentPage")
    if ($page.currentPage.path -ne "pages/work-orders/detail") { throw "page submission did not navigate to work-order detail" }
    $detail = Invoke-WechatIde "automation_page_action" @("--project", $Project, "--action", "getData", "--data-path", "workOrder")
    if (-not $detail.data.id) { throw "detail page has no work order" }
    $Evidence.result = [ordered]@{
        work_order_id = [long]$detail.data.id
        status = [string]$detail.data.status
        version = [long]$detail.data.version
        house_id = $HouseId
        file_ids = @($Evidence.file_ids)
    }
    $detailShot = Join-Path $OutDir "19g-new-canary-image-submitted.png"
    $null = Invoke-WechatIde "simulator_screenshot" @("--project", $Project, "--path", $detailShot, "--optimize", "false")
    $Evidence.screenshots += "19g-new-canary-image-submitted.png"
    $Evidence.stage = "submitted"
    Save-Evidence
    [ordered]@{ work_order_id = $Evidence.result.work_order_id; status = $Evidence.result.status; page = $page.currentPage.path } | ConvertTo-Json -Compress
} catch {
    $safe = [string]$_.Exception.Message
    $safe = $safe.Replace($Phone, "[redacted-phone]").Replace($Password, "[redacted-password]")
    $Evidence.error = $safe
    Save-Evidence
    throw $safe
}
