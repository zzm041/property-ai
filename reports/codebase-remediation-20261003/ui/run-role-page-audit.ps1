param(
  [string]$Project = "D:\property-ai\miniprogram",
  [string]$Fixture = "D:\property-ai\tmp\delivery-auto-8093\fixture-context.json",
  [string]$Output = "D:\property-ai\reports\codebase-remediation-20261003\ui\role-page-coverage.json"
)

$ErrorActionPreference = "Stop"
$wechatide = "D:\微信web开发者工具\wechatide.cmd"
$fixtureData = Get-Content -LiteralPath $Fixture -Raw | ConvertFrom-Json
$outputDirectory = Split-Path -Parent $Output

function Invoke-Wechatide {
  param([string[]]$Arguments)
  $lines = & $wechatide -c Codex @Arguments 2>&1
  $raw = ($lines | ForEach-Object { $_.ToString() }) -join "`n"
  $start = $raw.IndexOf("{")
  if ($start -lt 0) { throw "wechatide returned no JSON" }
  return ($raw.Substring($start) | ConvertFrom-Json)
}

function Inspect-CurrentPage {
  param(
    [string]$Role,
    [string]$Route,
    [string]$Query = "",
    [string]$Screenshot = ""
  )
  $currentPath = ""
  $currentTitle = ""
  for ($attempt = 0; $attempt -lt 3 -and -not $currentPath; $attempt += 1) {
    $current = Invoke-Wechatide @("automation_runtime_info", "--project", $Project, "--action", "currentPage")
    $currentPath = [string]$current.result.currentPage.path
    $currentTitle = [string]$current.result.currentPage.title
    if (-not $currentPath) { Start-Sleep -Milliseconds 250 }
  }
  $errorText = ""
  try {
    $errorResult = Invoke-Wechatide @("automation_page_action", "--project", $Project, "--action", "getData", "--data-path", "error")
    if ($null -ne $errorResult.result.data) { $errorText = [string]$errorResult.result.data }
  } catch {
    $errorText = ""
  }
  $screenshotWidth = $null
  $screenshotHeight = $null
  if ($Screenshot) {
    $path = Join-Path $outputDirectory $Screenshot
    $shot = Invoke-Wechatide @("simulator_screenshot", "--project", $Project, "--path", $path, "--wait", "1", "--optimize", "false")
    $screenshotWidth = $shot.result.imageWidth
    $screenshotHeight = $shot.result.imageHeight
  }
  return [ordered]@{
    role = $Role
    requested_route = $Route
    query_present = [bool]$Query
    opened = $true
    route_read_status = $(if ($currentPath) { "CONFIRMED" } else { "UNKNOWN_AUTOMATOR_RESPONSE" })
    actual_route = $currentPath
    title = $currentTitle
    redirected = $(if ($currentPath) { $currentPath -ne $Route } else { $null })
    error_present = [bool]$errorText
    screenshot = $Screenshot
    screenshot_width = $screenshotWidth
    screenshot_height = $screenshotHeight
  }
}

function Open-Page {
  param(
    [string]$Role,
    [string]$Route,
    [string]$Query = "",
    [string]$Screenshot = ""
  )
  $arguments = @("simulator_open_page", "--project", $Project, "--page", $Route)
  if ($Query) { $arguments += @("--query", $Query) }
  $open = Invoke-Wechatide $arguments
  if (-not [bool]$open.result.success) { throw "failed to open $Route" }
  Start-Sleep -Milliseconds 3500
  return Inspect-CurrentPage -Role $Role -Route $Route -Query $Query -Screenshot $Screenshot
}

function Login-Normally {
  param([string]$Role)
  $null = Invoke-Wechatide @(
    "automation_evaluate", "--project", $Project,
    "--fn-source", "function(){wx.clearStorageSync();const app=getApp();if(app&&app.globalData){app.globalData.me=null;}return true;}"
  )
  $null = Invoke-Wechatide @("simulator_open_page", "--project", $Project, "--page", "pages/login/login")
  Start-Sleep -Milliseconds 700
  $loginPhone = [string]$fixtureData.phones.$Role
  if ($loginPhone.StartsWith("+86") -and $loginPhone.Length -eq 14) {
    $loginPhone = $loginPhone.Substring(3)
  }
  $inputReady = $false
  for ($attempt = 0; $attempt -lt 3 -and -not $inputReady; $attempt += 1) {
    try {
      $null = Invoke-Wechatide @(
        "automation_element_action", "--project", $Project,
        "--action", "input", "--selector", "input", "--value", $loginPhone
      )
      $null = Invoke-Wechatide @(
        "automation_element_action", "--project", $Project,
        "--action", "input", "--selector", ".password-row input",
        "--value", [string]$fixtureData.passwords.$Role
      )
      $lengths = Invoke-Wechatide @(
        "automation_evaluate", "--project", $Project,
        "--fn-source", "function(){const p=getCurrentPages().slice(-1)[0];return {phone:String(p.data.phone||'').length,password:String(p.data.password||'').length};}"
      )
      $inputReady = ($lengths.result.result.result.phone -eq 11 -and $lengths.result.result.result.password -gt 0)
    } catch {
      Start-Sleep -Milliseconds 350
    }
  }
  if (-not $inputReady) { throw "native login inputs were not populated for role $Role" }
  $null = Invoke-Wechatide @(
    "automation_element_action", "--project", $Project,
    "--action", "tap", "--selector", "#login-submit"
  )
  for ($attempt = 0; $attempt -lt 15; $attempt += 1) {
    Start-Sleep -Milliseconds 400
    $current = Invoke-Wechatide @("automation_runtime_info", "--project", $Project, "--action", "currentPage")
    if ([string]$current.result.currentPage.path -eq "pages/home/home") { return }
  }
  throw "normal password login did not reach home for role $Role"
}

$results = [System.Collections.Generic.List[object]]::new()
$conditionalChecks = [System.Collections.Generic.List[object]]::new()

$null = Invoke-Wechatide @(
  "automation_evaluate", "--project", $Project,
  "--fn-source", "function(){wx.clearStorageSync();return true;}"
)
$results.Add((Open-Page -Role "unauthenticated" -Route "pages/login/login" -Screenshot "audit-unauth-login.png"))
$null = Invoke-Wechatide @(
  "automation_element_action", "--project", $Project,
  "--action", "tap", "--selector", "#forgot-password-link"
)
Start-Sleep -Milliseconds 3500
$results.Add((Inspect-CurrentPage -Role "unauthenticated" -Route "pages/password-reset/password-reset" -Screenshot "audit-unauth-password-reset.png"))
$results.Add((Open-Page -Role "unauthenticated" -Route "pages/dev-login/dev-login" -Screenshot "audit-unauth-dev-login.png"))

Login-Normally -Role "residentA1"
foreach ($page in @(
  @{ route = "pages/home/home"; shot = "audit-resident-home.png" },
  @{ route = "pages/profile/profile"; shot = "audit-resident-profile.png" },
  @{ route = "pages/identity-recovery/identity-recovery"; shot = "audit-resident-identity-recovery.png" },
  @{ route = "pages/work-orders/create"; shot = "audit-resident-work-order-create.png" },
  @{ route = "pages/work-orders/list"; shot = "audit-resident-work-order-list.png" },
  @{ route = "pages/work-orders/detail"; query = "id=20"; shot = "audit-resident-work-order-detail.png" },
  @{ route = "pages/houses/houses"; shot = "audit-resident-houses.png" },
  @{ route = "pages/announcements/list"; shot = "audit-resident-announcements-list.png" },
  @{ route = "pages/announcements/detail"; shot = "audit-resident-announcement-detail.png" },
  @{ route = "pages/assistant/assistant"; shot = "audit-resident-assistant.png" },
  @{ route = "pages/notifications/list"; shot = "audit-resident-notifications.png" }
)) {
  $results.Add((Open-Page -Role "resident" -Route $page.route -Query $page.query -Screenshot $page.shot))
}

Login-Normally -Role "staffA1"
foreach ($page in @(
  @{ route = "pages/home/home"; shot = "audit-staff-home.png" },
  @{ route = "pages/profile/profile"; shot = "audit-staff-profile.png" },
  @{ route = "pages/employee-claim/employee-claim"; shot = "audit-staff-employee-claim.png" },
  @{ route = "pages/attendance/attendance"; query = "propertyId=$($fixtureData.organizations[0].property_id)"; shot = "audit-staff-attendance.png" },
  @{ route = "pages/staff-orders/list"; shot = "audit-staff-order-list.png" },
  @{ route = "pages/staff-orders/detail"; query = "id=20"; shot = "audit-staff-work-order-detail.png" },
  @{ route = "pages/supervisor/incidents"; shot = "audit-staff-incidents.png" },
  @{ route = "pages/announcements/list"; shot = "audit-staff-announcements.png" },
  @{ route = "pages/assistant/assistant"; shot = "audit-staff-assistant.png" },
  @{ route = "pages/notifications/list"; shot = "audit-staff-notifications.png" }
)) {
  $results.Add((Open-Page -Role "staff" -Route $page.route -Query $page.query -Screenshot $page.shot))
}

Login-Normally -Role "managerA"
foreach ($page in @(
  @{ route = "pages/home/home"; shot = "audit-manager-home.png" },
  @{ route = "pages/profile/profile"; shot = "audit-manager-profile.png" },
  @{ route = "pages/supervisor/queue"; shot = "audit-manager-queue.png" },
  @{ route = "pages/supervisor/incidents"; shot = "audit-manager-incidents.png" },
  @{ route = "pages/supervisor/announcements"; shot = "audit-manager-announcements.png" },
  @{ route = "pages/supervisor/bindings"; shot = "audit-manager-bindings.png" },
  @{ route = "pages/supervisor/staff"; shot = "audit-manager-staff.png" },
  @{ route = "pages/supervisor/properties"; shot = "audit-manager-properties.png" },
  @{ route = "pages/supervisor/knowledge"; shot = "audit-manager-knowledge.png" },
  @{ route = "pages/supervisor/sla"; shot = "audit-manager-sla.png" },
  @{ route = "pages/supervisor/dashboard"; shot = "audit-manager-dashboard.png" },
  @{ route = "pages/supervisor/identity-recovery"; shot = "audit-manager-identity-recovery.png" },
  @{ route = "pages/announcements/list"; shot = "audit-manager-announcements-list.png" },
  @{ route = "pages/assistant/assistant"; shot = "audit-manager-assistant.png" },
  @{ route = "pages/notifications/list"; shot = "audit-manager-notifications.png" },
  @{ route = "pages/system/organizations"; shot = "audit-system-organizations-under-manager.png" }
)) {
  $results.Add((Open-Page -Role "manager" -Route $page.route -Query $page.query -Screenshot $page.shot))
}

$null = Open-Page -Role "manager" -Route "pages/supervisor/queue"
$queueSelection = $false
$contactComponentRendered = $false
try {
  $null = Invoke-Wechatide @(
    "automation_element_action", "--project", $Project,
    "--action", "tap", "--selector", ".card", "--wait-for-selector", ".card"
  )
  Start-Sleep -Milliseconds 1500
  $selected = Invoke-Wechatide @(
    "automation_page_action", "--project", $Project,
    "--action", "getData", "--data-path", "selectedId"
  )
  $queueSelection = [bool]$selected.result.data
  $component = Invoke-Wechatide @(
    "automation_page_action", "--project", $Project,
    "--action", "querySelector", "--selector", "work-order-contact"
  )
  $contactComponentRendered = [bool]$component.result.success
} catch {
  $queueSelection = $false
  $contactComponentRendered = $false
}
$queueShot = Invoke-Wechatide @(
  "simulator_screenshot", "--project", $Project,
  "--path", (Join-Path $outputDirectory "audit-manager-queue-contact.png"),
  "--wait", "1", "--optimize", "false"
)
$conditionalChecks.Add([ordered]@{
  check = "work-order-contact-positive-render"
  selected_work_order = $queueSelection
  component_query_succeeded = $contactComponentRendered
  screenshot = "audit-manager-queue-contact.png"
  screenshot_width = $queueShot.result.imageWidth
  screenshot_height = $queueShot.result.imageHeight
})

$null = Invoke-Wechatide @(
  "automation_evaluate", "--project", $Project,
  "--fn-source", "function(){wx.clearStorageSync();const app=getApp();if(app&&app.globalData){app.globalData.me=null;}return true;}"
)
$null = Open-Page -Role "unauthenticated" -Route "pages/login/login"
$longError = "当前网络暂时不可用，请稍后重试；如问题持续，请联系物业管理员。此文本用于检查小屏长错误换行与禁用态，不会发送请求。"
$statePatch = [ordered]@{ loading = $true; error = $longError } | ConvertTo-Json -Compress
$null = Invoke-Wechatide @(
  "automation_page_action", "--project", $Project,
  "--action", "setData", "--patch", $statePatch
)
$stateData = Invoke-Wechatide @(
  "automation_page_action", "--project", $Project,
  "--action", "getData"
)
$stateShot = Invoke-Wechatide @(
  "simulator_screenshot", "--project", $Project,
  "--path", (Join-Path $outputDirectory "audit-common-long-error-disabled.png"),
  "--wait", "1", "--optimize", "false"
)
$conditionalChecks.Add([ordered]@{
  check = "long-error-and-disabled-login"
  loading = [bool]$stateData.result.data.loading
  long_error_length = ([string]$stateData.result.data.error).Length
  screenshot = "audit-common-long-error-disabled.png"
  screenshot_width = $stateShot.result.imageWidth
  screenshot_height = $stateShot.result.imageHeight
})
$normalPatch = [ordered]@{ loading = $false; error = "" } | ConvertTo-Json -Compress
$null = Invoke-Wechatide @(
  "automation_page_action", "--project", $Project,
  "--action", "setData", "--patch", $normalPatch
)
$focusSucceeded = $false
try {
  $focus = Invoke-Wechatide @(
    "automation_element_action", "--project", $Project,
    "--action", "tap", "--selector", ".password-row input"
  )
  $focusSucceeded = [bool]$focus.result.success
} catch {
  $focusSucceeded = $false
}
$focusShot = Invoke-Wechatide @(
  "simulator_screenshot", "--project", $Project,
  "--path", (Join-Path $outputDirectory "audit-login-password-focus.png"),
  "--wait", "1", "--optimize", "false"
)
$conditionalChecks.Add([ordered]@{
  check = "password-input-focus"
  tap_succeeded = $focusSucceeded
  keyboard_visible = "UNKNOWN"
  screenshot = "audit-login-password-focus.png"
  screenshot_width = $focusShot.result.imageWidth
  screenshot_height = $focusShot.result.imageHeight
})

$payload = [ordered]@{
  task = "MINIPROGRAM-FULL-USABILITY-20261003"
  generated_at = (Get-Date).ToString("o")
  source_head = (git -C (Split-Path -Parent $Project) rev-parse HEAD).Trim()
  project = $Project
  fixture_run_id = [string]$fixtureData.run_id
  credentials_in_output = $false
  authentication = "normal password login; no DevAuth or seed login"
  business_object_writes = 0
  authentication_side_effects = "normal session creation only"
  model_calls = 0
  routes = $results
  conditional_checks = $conditionalChecks
  unverified_device_states = @(
    "physical-device enlarged system font",
    "IME keyboard height and dismissal across device models",
    "notched-device bottom safe area"
  )
}
$payload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Output -Encoding utf8
Write-Output "role page audit saved: $Output ($($results.Count) checks)"
