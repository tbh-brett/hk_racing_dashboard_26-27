<#
.SYNOPSIS
    Fix pace figures, comments on running, and tags that earlier scrapes left out.

.DESCRIPTION
    Shows what is damaged first and fetches nothing until you say so.

    Three different problems with three different costs:

      pace       Nothing to fetch. The figures are computed from data already
                 here, and a bug was dropping them — one horse that pulled up
                 used to void the pace for its entire field. Instant.
      comments   The narrowest fetch there is: the comments-on-running page,
                 about eleven requests per meeting. Tags come free, because
                 they are read back out of the comments.
      headers    A handful of races lost their distance and class. Those need
                 the meeting re-read properly.

    Re-running is always safe. Every write replaces the same row.

.EXAMPLE
    .\ops\repair.ps1                    # what is damaged
    .\ops\repair.ps1 -Fix               # repair all of it
    .\ops\repair.ps1 -Fix -Only pace    # the instant half only
    .\ops\repair.ps1 -Fix -Limit 5      # try five meetings first
#>
[CmdletBinding()]
param(
    [switch] $Fix,
    [string] $Only  = "",
    [int]    $Limit = 0
)

$ErrorActionPreference = "Stop"

$venvPy = ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { $venvPy = ".venv/bin/python" }
if (-not (Test-Path $venvPy)) {
    Write-Host ""
    Write-Host "    No .venv here. Run .\ops\start.ps1 once first." -ForegroundColor Red
    Write-Host ""
    exit 1
}

$argv = @("-m", "hkrd.jobs.repair")
if ($Fix)   { $argv += "--fix" }
if ($Only)  { $argv += @("--only", $Only) }
if ($Limit) { $argv += @("--limit", "$Limit") }

Write-Host ""
if ($Fix) {
    Write-Host "  Repairing. The comments pass fetches from HKJC at one request" -ForegroundColor Cyan
    Write-Host "  every 1.2 seconds, so leave this running." -ForegroundColor Cyan
} else {
    Write-Host "  Checking what is damaged. Nothing will be fetched." -ForegroundColor Cyan
}

& $venvPy @argv
$code = $LASTEXITCODE

Write-Host ""
if ($code -ne 0) {
    Write-Host "  Something did not come back cleanly — the messages above say what." -ForegroundColor Yellow
    Write-Host "  Re-running is safe." -ForegroundColor Yellow
} elseif ($Fix) {
    Write-Host "  Done. Restart the dashboard to see it: .\ops\start.ps1" -ForegroundColor Green
}
Write-Host ""
exit $code
