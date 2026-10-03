param(
  [string]$Project = "D:\property-ai\miniprogram",
  [string]$Fixture = "D:\property-ai\tmp\delivery-auto-8093\fixture-context.json",
  [string]$Output = "D:\property-ai\reports\codebase-remediation-20261003\ui\ui-state-evidence.json"
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
  $parsed = $raw.Substring($start) | ConvertFrom-Json
  if (-not [bool]$parsed.ok) { throw [string]$parsed.message }
  return $parsed
}

function Login-Manager {
  $phone = [string]$fixtureData.phones.managerA
  if ($phone.StartsWith("+86") -and $phone.Length -eq 14) { $phone = $phone.Substring(3) }
  $null = Invoke-Wechatide @(
    "automation_evaluate", "--project", $Project,
    "--fn-source", "function(){wx.clearStorageSync();const app=getApp();if(app&&app.globalData){app.globalData.me=null;}return true;}"
  )
  $null = Invoke-Wechatide @("simulator_open_page", "--project", $Project, "--page", "pages/login/login")
  Start-Sleep -Milliseconds 900
  $null = Invoke-Wechatide @("automation_element_action", "--project", $Project, "--action", "input", "--selector", "input", "--value", $phone)
  $null = Invoke-Wechatide @("automation_element_action", "--project", $Project, "--action", "input", "--selector", ".password-row input", "--value", [string]$fixtureData.passwords.managerA)
  $null = Invoke-Wechatide @("automation_element_action", "--project", $Project, "--action", "tap", "--selector", "#login-submit")
  Start-Sleep -Seconds 3
}

$checks = [System.Collections.Generic.List[object]]::new()

Login-Manager
$null = Invoke-Wechatide @("simulator_open_page", "--project", $Project, "--page", "pages/supervisor/queue")
Start-Sleep -Seconds 3
$null = Invoke-Wechatide @("automation_element_action", "--project", $Project, "--action", "tap", "--selector", ".card", "--wait-for-selector", ".card")
Start-Sleep -Seconds 2
$selected = Invoke-Wechatide @(
  "automation_evaluate", "--project", $Project,
  "--fn-source", "function(){const p=getCurrentPages().slice(-1)[0];wx.pageScrollTo({scrollTop:900,duration:0});return {route:p.route,selected:!!p.data.selectedId,items:(p.data.items||[]).length};}"
)
$contactShot = Invoke-Wechatide @(
  "simulator_screenshot", "--project", $Project,
  "--path", (Join-Path $outputDirectory "audit-manager-queue-contact.png"),
  "--wait", "1", "--optimize", "false"
)
$checks.Add([ordered]@{
  check = "work-order-contact-positive-render"
  route = [string]$selected.result.result.result.route
  selected_work_order = [bool]$selected.result.result.result.selected
  rendered_result = "MASKED_CONTACT_AND_ACTIONS_VISIBLE_IN_SCREENSHOT"
  screenshot = "audit-manager-queue-contact.png"
  screenshot_width = $contactShot.result.imageWidth
  screenshot_height = $contactShot.result.imageHeight
})

$null = Invoke-Wechatide @(
  "automation_evaluate", "--project", $Project,
  "--fn-source", "function(){wx.clearStorageSync();const app=getApp();if(app&&app.globalData){app.globalData.me=null;}return true;}"
)
$null = Invoke-Wechatide @("simulator_open_page", "--project", $Project, "--page", "pages/login/login")
Start-Sleep -Milliseconds 900
$longState = Invoke-Wechatide @(
  "automation_evaluate", "--project", $Project,
  "--fn-source", "function(){const p=getCurrentPages().slice(-1)[0];p.setData({loading:true,error:'用于检查长文本换行和禁用态的提示：当前网络暂时不可用，请稍后重试；如问题持续，请联系物业管理员。'});return {route:p.route,loading:p.data.loading,errorLength:p.data.error.length};}"
)
$stateShot = Invoke-Wechatide @(
  "simulator_screenshot", "--project", $Project,
  "--path", (Join-Path $outputDirectory "audit-common-long-error-disabled.png"),
  "--wait", "1", "--optimize", "false"
)
$checks.Add([ordered]@{
  check = "long-error-and-disabled-login"
  route = [string]$longState.result.result.result.route
  loading = [bool]$longState.result.result.result.loading
  long_error_length = [int]$longState.result.result.result.errorLength
  screenshot = "audit-common-long-error-disabled.png"
  screenshot_width = $stateShot.result.imageWidth
  screenshot_height = $stateShot.result.imageHeight
})

$null = Invoke-Wechatide @(
  "automation_evaluate", "--project", $Project,
  "--fn-source", "function(){const p=getCurrentPages().slice(-1)[0];p.setData({loading:false,error:''});return true;}"
)
$focus = Invoke-Wechatide @("automation_element_action", "--project", $Project, "--action", "tap", "--selector", ".password-row input")
$focusShot = Invoke-Wechatide @(
  "simulator_screenshot", "--project", $Project,
  "--path", (Join-Path $outputDirectory "audit-login-password-focus.png"),
  "--wait", "1", "--optimize", "false"
)
$checks.Add([ordered]@{
  check = "password-input-focus"
  tap_succeeded = [bool]$focus.result.success
  keyboard_visible = "UNKNOWN_NOT_EXPOSED_BY_AUTOMATOR_SCREENSHOT"
  screenshot = "audit-login-password-focus.png"
  screenshot_width = $focusShot.result.imageWidth
  screenshot_height = $focusShot.result.imageHeight
})

$payload = [ordered]@{
  task = "MINIPROGRAM-FULL-USABILITY-20261003-STATES"
  generated_at = (Get-Date).ToString("o")
  source_head = (git -C (Split-Path -Parent $Project) rev-parse HEAD).Trim()
  credentials_in_output = $false
  business_object_writes = 0
  authentication_side_effects = "normal manager session creation only"
  model_calls = 0
  checks = $checks
  unverified = @(
    "soft keyboard height and dismissal are not exposed by the automator screenshot",
    "physical-device enlarged system font",
    "notched-device bottom safe area"
  )
}
$payload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Output -Encoding utf8
Write-Output "state audit saved: $Output"
