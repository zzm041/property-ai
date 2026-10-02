$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Project = Join-Path $RepoRoot "miniprogram"
$WechatIde = "D:\微信web开发者工具\wechatide.cmd"
$Api = "http://127.0.0.1:8083/api/v1"
$Output = Join-Path $PSScriptRoot "page-expiry-evidence.json"

if (Test-Path -LiteralPath $Output) {
    throw "page expiry evidence already exists"
}

function Invoke-Ide([string]$Tool, [string[]]$Arguments) {
    $raw = (& $WechatIde -c Codex $Tool @Arguments 2>&1 | Out-String)
    $start = $raw.IndexOf("{")
    if ($start -lt 0) { throw "wechatide returned no JSON for $Tool" }
    $parsed = $raw.Substring($start) | ConvertFrom-Json
    if (-not $parsed.ok -or -not $parsed.result.success) {
        $reason = if ($parsed.message) { [string]$parsed.message } else { "unknown" }
        throw "wechatide $Tool failed during $script:Phase ($reason)"
    }
    return $parsed.result
}

function Element([string]$Action, [string]$Selector, [string]$Value = "") {
    $arguments = @("--project", $Project, "--action", $Action, "--selector", $Selector)
    if ($Action -eq "input") { $arguments += @("--value", $Value) }
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try { return Invoke-Ide "automation_element_action" $arguments }
        catch {
            if ($attempt -eq 3) { throw }
            Start-Sleep -Milliseconds 600
        }
    }
}

function PageData() {
    return (Invoke-Ide "automation_page_action" @(
        "--project", $Project, "--action", "getData"
    )).data
}

function CurrentPage() {
    return (Invoke-Ide "automation_runtime_info" @(
        "--project", $Project, "--action", "currentPage"
    )).currentPage
}

function ChallengeAnswer([string]$Prompt) {
    $match = [regex]::Match($Prompt, "^\s*(\d+)\s*\+\s*(\d+)\s*=\s*\?\s*$")
    if (-not $match.Success) { throw "unexpected development challenge" }
    return ([int]$match.Groups[1].Value + [int]$match.Groups[2].Value).ToString()
}

function PhoneHash([string]$Phone) {
    $bytes = [Text.Encoding]::UTF8.GetBytes($Phone)
    return [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
}

$script:Phase = "initialize"
$phone = "195{0:D8}" -f [Security.Cryptography.RandomNumberGenerator]::GetInt32(0, 100000000)
$started = [DateTime]::UtcNow
$timer = [Diagnostics.Stopwatch]::StartNew()

Invoke-Ide "simulator_open_page" @(
    "--project", $Project, "--page", "pages/login/login"
) | Out-Null
$script:Phase = "enter-phone"
Start-Sleep -Seconds 1
Element "input" 'input[placeholder="手机号"]' $phone | Out-Null
$script:Phase = "switch-to-sms"
Element "tap" "#login-mode-toggle" | Out-Null
$script:Phase = "request-challenge"
Element "tap" "#sms-send" | Out-Null
Start-Sleep -Milliseconds 800
$challenge = PageData
if (-not $challenge.challengePrompt) { throw "challenge prompt was not rendered" }
Element "input" 'input[placeholder="挑战答案"]' (ChallengeAnswer $challenge.challengePrompt) | Out-Null
$script:Phase = "send-otp"
Element "tap" "#sms-send" | Out-Null
Start-Sleep -Milliseconds 1200
$sent = PageData
if ($sent.cooldown -le 0 -or $sent.error -notmatch "开发短信替身") {
    throw "development OTP was not sent through the page"
}

$inboxBody = @{ phone = $phone; purpose = "login" } | ConvertTo-Json -Compress
$inbox = Invoke-RestMethod -Method Post -Uri "$Api/auth/sms/test-inbox" `
    -ContentType "application/json" -Body $inboxBody -TimeoutSec 10
$code = [string]$inbox.data.code
if ($code -notmatch "^\d{6}$") { throw "development inbox did not return one OTP" }
$sentAt = [DateTime]::UtcNow

# Exercise the real 300-second server TTL. Do not change the database clock or TTL.
Start-Sleep -Seconds 302

$script:Phase = "submit-expired-otp"
Element "input" 'input[placeholder="短信验证码"]' $code | Out-Null
Element "tap" "#login-submit" | Out-Null
Start-Sleep -Seconds 2
$finalPage = CurrentPage
$finalData = PageData
$timer.Stop()

$passed = (
    $finalPage.path -eq "pages/login/login" -and
    [string]$finalData.error -match "验证码|过期|认证失败" -and
    [string]::IsNullOrWhiteSpace([string]$finalData.password)
)
if (-not $passed) { throw "expired OTP was not rejected on the login page" }

$evidence = [ordered]@{
    task_id = "AUTH-SMS-EXPIRE-PAGE-20261002"
    result = "PASS_SCOPED_LOCAL_ISOLATED_PAGE_REAL_WAIT"
    source_head = (git -C $RepoRoot rev-parse HEAD).Trim()
    fixed_api_source = "15977bfe5729d8581b713fac1435886f295e7efc"
    environment = "127.0.0.1:8083 -> 127.0.0.1:3317/property_ai_auth001_test@0014"
    phone_sha256 = PhoneHash $phone
    started_at_utc = $started.ToString("o")
    otp_sent_at_utc = $sentAt.ToString("o")
    completed_at_utc = [DateTime]::UtcNow.ToString("o")
    elapsed_seconds = [Math]::Round($timer.Elapsed.TotalSeconds, 3)
    configured_ttl_seconds = 300
    page_path_after_submit = $finalPage.path
    page_error = [string]$finalData.error
    stayed_logged_out = $finalPage.path -eq "pages/login/login"
    model_calls = 0
    paid_sms_calls = 0
    system_clock_changed = $false
    database_ttl_changed = $false
    sensitive_values_in_evidence = $false
    boundary = "Development SMS test inbox was used only to obtain the synthetic OTP; the send and expired login attempt were actual mini-program page operations. This is not paid or Production SMS acceptance."
}
[IO.Directory]::CreateDirectory($PSScriptRoot) | Out-Null
[IO.File]::WriteAllText(
    $Output,
    ($evidence | ConvertTo-Json -Depth 6),
    [Text.UTF8Encoding]::new($false)
)
Write-Output ($evidence | ConvertTo-Json -Depth 6 -Compress)
