<#
.SYNOPSIS
    Fill in the racing data the database is missing.

.DESCRIPTION
    Shows what is there, fetches what is not, shows it again.

    Missing data is usually not a broken dashboard. The database was built from
    an archive folder that stops at some point, and everything after that has
    to come from HKJC. This is the job that goes and gets it.

    It is polite about it — one request every 1.2 seconds, and it only asks
    about dates it cannot already answer from the database — so a wide catch-up
    takes a while. Leave it running; it prints what it finds as it goes.

.EXAMPLE
    .\ops\catch-up.ps1              # the last 60 days
    .\ops\catch-up.ps1 -Days 120    # further back
    .\ops\catch-up.ps1 -ShowOnly    # just tell me what is missing
#>
[CmdletBinding()]
param(
    [int]    $Days       = 60,
    [int]    $Trials     = 30,
    [switch] $ShowOnly
)

$ErrorActionPreference = "Stop"

function Head ($t) { Write-Host ""; Write-Host "  $t" -ForegroundColor Cyan; Write-Host "  $('-' * $t.Length)" -ForegroundColor DarkGray }
function Note ($t) { Write-Host "    $t" -ForegroundColor DarkGray }
function Die  ($t) { Write-Host ""; Write-Host "    $t" -ForegroundColor Red; Write-Host ""; exit 1 }

$venvPy = ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { $venvPy = ".venv/bin/python" }
if (-not (Test-Path $venvPy)) {
    Die "No .venv here. Run .\ops\start.ps1 once first — it sets everything up."
}

Head "What the database has now"
& $venvPy -m hkrd.jobs.coverage
if ($LASTEXITCODE -ne 0) { Die "Could not read the database." }

if ($ShowOnly) {
    Write-Host "  (-ShowOnly: nothing fetched)" -ForegroundColor DarkGray
    Write-Host ""
    exit 0
}

Head "Fetching race meetings"
Note "Looking back $Days days. Most dates are not race days and cost one"
Note "request each; a real meeting takes about half a minute to fetch."
Note "Leave this running."
Write-Host ""
& $venvPy -m hkrd.jobs.nightly --back $Days
$nightly = $LASTEXITCODE

Head "Fetching barrier trials"
Note "HKJC publishes which days had trials; this takes the ones we lack."
Write-Host ""
& $venvPy -m hkrd.jobs.scrape_trials --limit $Trials
$trials = $LASTEXITCODE

Head "What the database has now"
& $venvPy -m hkrd.jobs.coverage

Write-Host ""
if ($nightly -ne 0 -or $trials -ne 0) {
    Write-Host "  Something did not come back cleanly — the messages above say what." -ForegroundColor Yellow
    Write-Host "  Re-running is safe: every write is an upsert, so nothing duplicates." -ForegroundColor Yellow
} else {
    Write-Host "  Done. Start the dashboard with .\ops\start.ps1" -ForegroundColor Green
}

# Bets are deliberately NOT here. They come from your account statements, not
# from HKJC — no scrape can know what you staked. Drop a statement in and run:
#     .venv\Scripts\python -m hkrd.jobs.import_statement --src <path to the file>
Write-Host ""
Write-Host "  Bets are not fetched by this — HKJC does not know what you staked." -ForegroundColor DarkGray
Write-Host "  For those, import an account statement:" -ForegroundColor DarkGray
Write-Host "    .venv\Scripts\python -m hkrd.jobs.import_statement --src `"C:\folder\statement.txt`"" -ForegroundColor DarkGray
Write-Host ""
