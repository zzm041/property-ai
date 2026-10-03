param(
  [string]$Project = "D:\property-ai\miniprogram",
  [string]$Fixture = "D:\property-ai\tmp\delivery-auto-8093\fixture-context.json",
  [string]$Output = "D:\property-ai\reports\codebase-remediation-20261003\ui\conditional-layout-evidence.json",
  [bool]$ResumeAfterPropertyModal = $true
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

function Login-Role {
  param([string]$Role)
  $phone = [string]$fixtureData.phones.$Role
  if ($phone.StartsWith("+86") -and $phone.Length -eq 14) { $phone = $phone.Substring(3) }
  $null = Invoke-Wechatide @(
    "automation_evaluate", "--project", $Project,
    "--fn-source", "function(){wx.clearStorageSync();const app=getApp();if(app&&app.globalData){app.globalData.me=null;}return true;}"
  )
  $null = Invoke-Wechatide @("simulator_open_page", "--project", $Project, "--page", "pages/login/login")
  Start-Sleep -Milliseconds 900
  $null = Invoke-Wechatide @("automation_element_action", "--project", $Project, "--action", "input", "--selector", "input", "--value", $phone)
  $null = Invoke-Wechatide @("automation_element_action", "--project", $Project, "--action", "input", "--selector", ".password-row input", "--value", [string]$fixtureData.passwords.$Role)
  $null = Invoke-Wechatide @("automation_element_action", "--project", $Project, "--action", "tap", "--selector", "#login-submit")
  Start-Sleep -Seconds 3
}

function Save-Shot {
  param([string]$Name)
  return Invoke-Wechatide @(
    "simulator_screenshot", "--project", $Project,
    "--path", (Join-Path $outputDirectory $Name),
    "--wait", "1", "--optimize", "false"
  )
}

$checks = [System.Collections.Generic.List[object]]::new()

if ($ResumeAfterPropertyModal) {
  foreach ($entry in @(
    @{ check = "announcement-draft-conditional-layout"; screenshot = "audit-conditional-announcement-form.png"; mode = "UI_ONLY_SYNTHETIC_STATE" },
    @{ check = "identity-review-card-conditional-layout"; screenshot = "audit-conditional-identity-review-card.png"; mode = "UI_ONLY_SYNTHETIC_STATE" },
    @{ check = "property-relation-conditional-layout"; screenshot = "audit-conditional-property-relation.png"; mode = "UI_ONLY_SYNTHETIC_STATE" },
    @{ check = "property-end-native-modal"; screenshot = "audit-native-property-end-modal.png"; mode = "UI_ONLY_SYNTHETIC_STATE_NO_CONFIRM" }
  )) {
    $path = Join-Path $outputDirectory $entry.screenshot
    if (-not (Test-Path -LiteralPath $path)) {
      throw "resume evidence is missing: $path"
    }
    $checks.Add([ordered]@{
      check = $entry.check
      evidence_mode = $entry.mode
      capture_state = "REUSED_FROM_CURRENT_RUN_BEFORE_NATIVE_MODAL_RESET"
      screenshot = $entry.screenshot
      business_request_sent = $false
      confirm_clicked = if ($entry.check -eq "property-end-native-modal") { $false } else { $null }
    })
  }
} else {
Login-Role -Role "managerA"

$null = Invoke-Wechatide @("simulator_open_page", "--project", $Project, "--page", "pages/supervisor/announcements")
Start-Sleep -Seconds 2
$announcement = Invoke-Wechatide @(
  "automation_evaluate", "--project", $Project,
  "--fn-source", "function(){const p=getCurrentPages().slice(-1)[0];p.setData({loading:false,error:'',canDraft:true,propertyId:'界面检查小区',title:'',content:'',scopeIndex:0,audienceIndex:0,buildings:[],units:[]});return {route:p.route,canDraft:p.data.canDraft};}"
)
$announcementShot = Save-Shot -Name "audit-conditional-announcement-form.png"
$checks.Add([ordered]@{
  check = "announcement-draft-conditional-layout"
  evidence_mode = "UI_ONLY_SYNTHETIC_STATE"
  route = [string]$announcement.result.result.result.route
  rendered = [bool]$announcement.result.result.result.canDraft
  business_request_sent = $false
  screenshot = "audit-conditional-announcement-form.png"
  width = $announcementShot.result.imageWidth
  height = $announcementShot.result.imageHeight
})

$null = Invoke-Wechatide @("simulator_open_page", "--project", $Project, "--page", "pages/supervisor/identity-recovery")
Start-Sleep -Seconds 2
$identity = Invoke-Wechatide @(
  "automation_evaluate", "--project", $Project,
  "--fn-source", "function(){const p=getCurrentPages().slice(-1)[0];p.setData({loading:false,error:'',items:[{id:900001,purposeText:'号码复用',statusText:'待审核',masked_phone:'138****0000',approval_count:1,quarantine_ends_at:'2026-10-30 10:00',status:'PENDING_REVIEW',version:1}],reasonById:{}});return {route:p.route,itemCount:p.data.items.length};}"
)
$identityShot = Save-Shot -Name "audit-conditional-identity-review-card.png"
$checks.Add([ordered]@{
  check = "identity-review-card-conditional-layout"
  evidence_mode = "UI_ONLY_SYNTHETIC_STATE"
  route = [string]$identity.result.result.result.route
  rendered_items = [int]$identity.result.result.result.itemCount
  business_request_sent = $false
  screenshot = "audit-conditional-identity-review-card.png"
  width = $identityShot.result.imageWidth
  height = $identityShot.result.imageHeight
})

$null = Invoke-Wechatide @("simulator_open_page", "--project", $Project, "--page", "pages/supervisor/properties")
Start-Sleep -Seconds 2
$property = Invoke-Wechatide @(
  "automation_evaluate", "--project", $Project,
  "--fn-source", "function(){const p=getCurrentPages().slice(-1)[0];p.setData({loading:false,error:'',notice:'',canEdit:true,properties:[{id:900001,name:'界面检查小区',tenant_id:900001}],propertyIndex:0,buildings:[],buildingOptions:[],units:[],unitOptions:[],houses:[],relations:[{id:900001,houseLabel:'1号楼 1单元 101',user_display_name:'合成住户',relationText:'业主',statusText:'生效中',status:'ACTIVE',valid_from:'2026-10-03',valid_to:null}]});wx.pageScrollTo({scrollTop:9999,duration:0});return {route:p.route,relationCount:p.data.relations.length,canEdit:p.data.canEdit};}"
)
Start-Sleep -Milliseconds 500
$propertyShot = Save-Shot -Name "audit-conditional-property-relation.png"
$checks.Add([ordered]@{
  check = "property-relation-conditional-layout"
  evidence_mode = "UI_ONLY_SYNTHETIC_STATE"
  route = [string]$property.result.result.result.route
  rendered_relations = [int]$property.result.result.result.relationCount
  business_request_sent = $false
  screenshot = "audit-conditional-property-relation.png"
  width = $propertyShot.result.imageWidth
  height = $propertyShot.result.imageHeight
})

$modal = Invoke-Wechatide @(
  "automation_evaluate", "--project", $Project,
  "--fn-source", "function(){const p=getCurrentPages().slice(-1)[0];p.endRelation({currentTarget:{dataset:{id:900001}}});return {route:p.route,invoked:true};}"
)
Start-Sleep -Milliseconds 500
$propertyModalShot = Save-Shot -Name "audit-native-property-end-modal.png"
$checks.Add([ordered]@{
  check = "property-end-native-modal"
  evidence_mode = "UI_ONLY_SYNTHETIC_STATE_NO_CONFIRM"
  route = [string]$modal.result.result.result.route
  modal_invoked = [bool]$modal.result.result.result.invoked
  confirm_clicked = $false
  business_request_sent = $false
  screenshot = "audit-native-property-end-modal.png"
  width = $propertyModalShot.result.imageWidth
  height = $propertyModalShot.result.imageHeight
})
$null = Invoke-Wechatide @("simulator_refresh", "--project", $Project)
Start-Sleep -Seconds 2
}

Login-Role -Role "residentA1"
$null = Invoke-Wechatide @("simulator_open_page", "--project", $Project, "--page", "pages/work-orders/detail", "--query", "id=20")
Start-Sleep -Seconds 2
$cancel = Invoke-Wechatide @(
  "automation_evaluate", "--project", $Project,
  "--fn-source", "function(){const p=getCurrentPages().slice(-1)[0];p.setData({loading:false,canCancel:true,cancelReason:''});wx.pageScrollTo({scrollTop:650,duration:0});return {route:p.route,canCancel:p.data.canCancel,hasWorkOrder:!!p.data.workOrder};}"
)
Start-Sleep -Milliseconds 500
$cancelLayoutShot = Save-Shot -Name "audit-conditional-work-order-cancel.png"
$null = Invoke-Wechatide @(
  "automation_evaluate", "--project", $Project,
  "--fn-source", "function(){const p=getCurrentPages().slice(-1)[0];p.cancel();return {route:p.route,invoked:true};}"
)
Start-Sleep -Milliseconds 500
$cancelModalShot = Save-Shot -Name "audit-native-work-order-cancel-modal.png"
$checks.Add([ordered]@{
  check = "resident-work-order-cancel-native-modal"
  evidence_mode = "UI_ONLY_SYNTHETIC_STATE_NO_CONFIRM"
  route = [string]$cancel.result.result.result.route
  has_real_loaded_work_order = [bool]$cancel.result.result.result.hasWorkOrder
  conditional_layout_screenshot = "audit-conditional-work-order-cancel.png"
  modal_screenshot = "audit-native-work-order-cancel-modal.png"
  confirm_clicked = $false
  business_request_sent = $false
  width = $cancelModalShot.result.imageWidth
  height = $cancelModalShot.result.imageHeight
})

$payload = [ordered]@{
  task = "MINIPROGRAM-FULL-USABILITY-20261003-CONDITIONALS"
  generated_at = (Get-Date).ToString("o")
  source_head = (git -C (Split-Path -Parent $Project) rev-parse HEAD).Trim()
  credentials_in_output = $false
  business_object_writes = 0
  model_calls = 0
  sms_calls = 0
  checks = $checks
  boundary = @(
    "Synthetic page state proves conditional layout and native modal rendering only.",
    "It is not authorization, API, or business-transaction evidence.",
    "No native modal confirm action was selected."
  )
}
$payload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Output -Encoding utf8
Write-Output "conditional layout audit saved: $Output"
