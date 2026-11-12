# Requires -Version 5.1
<#
.SYNOPSIS
    Pure decision helper for retiring a stale RAG Ollama receipt.

.DESCRIPTION
    ``deploy/rag-ollama-runtime.ps1`` refuses to start when a receipt on disk has
    no listener behind it, because a receipt is evidence that some process owns
    the port. That refusal is right when the recorded process may still be alive,
    and a deadlock when the machine proves the opposite: the recorded pid is
    gone, nothing listens on the port, and the executable is byte-for-byte the
    one the receipt recorded.

    This file holds *only* the decision, so the three conditions can be tested
    without starting a model. The launcher gathers the evidence and acts on the
    verdict; this helper never touches the filesystem, the process table or the
    network.
#>

function Get-StaleReceiptDecision {
    param(
        [Parameter(Mandatory = $true)] [object] $State,
        [Parameter(Mandatory = $true)] [bool] $RecordedPidExists,
        [Parameter(Mandatory = $true)] [int] $ListenerCount,
        [Parameter(Mandatory = $true)] [string] $CurrentExecutableSha256,
        [Parameter(Mandatory = $true)] [string] $ExpectedExecutableSha256
    )

    $reasons = @()
    if ($RecordedPidExists) {
        $reasons += "the receipt's pid still exists"
    }
    if ($ListenerCount -gt 0) {
        $reasons += ("{0} listener(s) still hold the RAG Ollama port" -f $ListenerCount)
    }
    $current = ([string]$CurrentExecutableSha256).ToUpperInvariant()
    $expected = ([string]$ExpectedExecutableSha256).ToUpperInvariant()
    $recorded = ([string]$State.executable_sha256).ToUpperInvariant()
    if ($current -ne $expected) {
        $reasons += "the executable in use is not the pinned RAG Ollama build"
    }
    if ([string]::IsNullOrWhiteSpace($recorded)) {
        $reasons += "the receipt records no executable sha256"
    } elseif ($recorded -ne $current) {
        $reasons += "the executable changed since the receipt was written"
    }

    return [pscustomobject]@{
        retire  = ($reasons.Count -eq 0)
        reasons = $reasons
        evidence = [pscustomobject]@{
            operation_id   = [string]$State.operation_id
            pid            = [int]$State.pid
            started_at_utc = [string]$State.started_at_utc
            recorded_pid_exists     = $RecordedPidExists
            listener_count          = $ListenerCount
            current_executable_sha256  = $current
            expected_executable_sha256 = $expected
            receipt_executable_sha256  = $recorded
        }
    }
}
