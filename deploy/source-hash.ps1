# PA-170: source-file pins must not depend on the checkout's line endings.
#
# `git archive` honours core.autocrlf (this repo's default checkout is
# core.autocrlf=true, so an exported candidate carries CRLF) while
# `git show <rev>:<path> | sha256sum` yields the stored blob (LF). A pin recorded
# from one form can therefore never match a file materialised in the other, and
# which form you happened to measure is invisible in the pin itself.
#
# Every comparison that covers a *source* file therefore goes through
# Get-CanonicalSha256, which normalises CRLF to LF before hashing. Runtime
# artefacts (receipts, dumps, executables, JSON evidence) keep using
# Get-FileHash: for those, a byte change must be detected, never normalised away.

$script:CanonicalHashMemo = @{}

function Get-CanonicalSha256 {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)

    $key = [string]$LiteralPath
    if ($script:CanonicalHashMemo.ContainsKey($key)) { return $script:CanonicalHashMemo[$key] }

    $bytes = [System.IO.File]::ReadAllBytes($LiteralPath)
    $buffer = New-Object byte[] $bytes.Length
    $written = 0
    for ($i = 0; $i -lt $bytes.Length; $i++) {
        if ($bytes[$i] -eq 13 -and ($i + 1) -lt $bytes.Length -and $bytes[$i + 1] -eq 10) { continue }
        $buffer[$written] = $bytes[$i]
        $written++
    }
    if ($written -ne $bytes.Length) {
        $trimmed = New-Object byte[] $written
        [Array]::Copy($buffer, $trimmed, $written)
        $buffer = $trimmed
    }

    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { $digest = $sha.ComputeHash($buffer) } finally { $sha.Dispose() }
    $hex = [System.BitConverter]::ToString($digest).Replace("-", "")
    $script:CanonicalHashMemo[$key] = $hex
    return $hex
}
