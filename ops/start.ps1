<#
.SYNOPSIS
    Open the dashboard on this computer. Nothing else needed.

.DESCRIPTION
    No accounts, no hosting, no internet beyond what pip needs the first time.
    It sets up Python's packages, builds the database from the old repo if it
    has not been built, starts the dashboard and opens your browser.

    First run takes a few minutes. Every run after that takes seconds.
    Press Ctrl+C in this window to stop it.

.EXAMPLE
    cd C:\path\to\hk_racing_dashboard_26-27
    .\ops\start.ps1
#>
[CmdletBinding()]
param(
    [int]    $Port       = 8000,
    [string] $LegacyRepo = "..\hk_race_dashboard",
    [switch] $Rebuild
)

$ErrorActionPreference = "Stop"

function Step ($n, $t) { Write-Host ""; Write-Host "[$n] $t" -ForegroundColor Cyan }
function Ok   ($t)     { Write-Host "    $t" -ForegroundColor Green }
function Note ($t)     { Write-Host "    $t" -ForegroundColor DarkGray }
function Die  ($t)     { Write-Host ""; Write-Host "    $t" -ForegroundColor Red; Write-Host ""; exit 1 }

Write-Host ""
Write-Host "  Starting the dashboard" -ForegroundColor White
Write-Host "  ----------------------"

# ── 1. Python ────────────────────────────────────────────────────────────────
Step 1 "Looking for Python"
# -First 1 matters. Get-Command returns EVERY match, and on Windows there are
# usually two — the real install and the Microsoft Store alias — so without it
# $py.Source is all of them joined into one unusable string. Found by running
# this: "The term '/usr/local/bin/python /usr/bin/python /bin/python' is not
# recognized".
$py = Get-Command python -CommandType Application -ErrorAction SilentlyContinue |
      Select-Object -First 1
if (-not $py) {
    $py = Get-Command python3 -CommandType Application -ErrorAction SilentlyContinue |
          Select-Object -First 1
}
if (-not $py) {
    Die "Python is not installed. Get it from https://python.org/downloads (tick `"Add Python to PATH`" in the installer), then run this again."
}
$ver = & $py.Source --version 2>&1
Ok "$ver"

# Needs 3.11 or newer — the package uses syntax older versions cannot parse,
# so this fails here with a sentence rather than a traceback halfway through.
if ($ver -match "(\d+)\.(\d+)") {
    $major = [int]$Matches[1]; $minor = [int]$Matches[2]
    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 11)) {
        Die "Python $major.$minor is too old. This needs 3.11 or newer — https://python.org/downloads"
    }
}

# ── 2. the virtual environment ───────────────────────────────────────────────
# Its own folder, so installing this cannot disturb anything else on the
# machine and deleting .venv undoes it completely.
Step 2 "Setting up the Python packages"
if (-not (Test-Path ".venv")) {
    Note "First time — creating .venv. This takes a couple of minutes."
    & $py.Source -m venv .venv
    if ($LASTEXITCODE -ne 0) { Die "Could not create the virtual environment." }
}

$venvPy = Join-Path (Resolve-Path ".venv") "Scripts\python.exe"
if (-not (Test-Path $venvPy)) { $venvPy = Join-Path (Resolve-Path ".venv") "bin/python" }
if (-not (Test-Path $venvPy)) { Die "The .venv folder looks broken. Delete it and run this again." }

# A marker rather than reinstalling every run: pip is slow enough that a
# five-second start becomes a forty-second one.
$stamp = ".venv\.installed"
if ($Rebuild -or -not (Test-Path $stamp)) {
    Note "Installing packages (numpy, pandas, scipy, fastapi) — a few minutes."
    & $venvPy -m pip install --quiet --upgrade pip
    & $venvPy -m pip install --quiet -e ".[dev,migrate]"
    if ($LASTEXITCODE -ne 0) { Die "Installing the packages failed. The message above says why." }
    New-Item -ItemType File -Path $stamp -Force | Out-Null
}
Ok "packages ready"

# ── 3. the database ──────────────────────────────────────────────────────────
Step 3 "Checking the database"
if ($Rebuild -and (Test-Path "hkrd.db")) {
    Remove-Item "hkrd.db","hkrd.db-wal","hkrd.db-shm" -ErrorAction SilentlyContinue
    Note "removed the old one (-Rebuild)"
}

if (Test-Path "hkrd.db") {
    $mb = [math]::Round((Get-Item "hkrd.db").Length / 1MB, 1)
    Ok "hkrd.db, $mb MB"
} else {
    if (-not (Test-Path $LegacyRepo)) {
        Die "Cannot find the old repo at '$LegacyRepo'. It holds hkjc.db, reports\, cache\ and blackbook.json — everything the dashboard shows comes from there. Put it next to this folder, or pass the path: .\ops\start.ps1 -LegacyRepo C:\somewhere\hk_race_dashboard"
    }
    Note "Building it — about twenty seconds. Each line is a step that landed."
    & $venvPy -m hkrd.jobs.bootstrap --legacy $LegacyRepo
    if ($LASTEXITCODE -ne 0) { Die "Building the database failed. The output above says which step." }
    Ok "built"
}

# ── 4. run ───────────────────────────────────────────────────────────────────
Step 4 "Starting"
$url = "http://127.0.0.1:$Port/pages/raceday.html"

# No password on a local instance. The app refuses to start without one
# otherwise — it serves the whole betting ledger — and this is the explicit
# opt-out for a machine nothing else can reach.
$env:HKRD_ALLOW_NO_AUTH = "1"

Write-Host ""
Write-Host "    $url" -ForegroundColor White
Write-Host ""
Note "Your browser will open in a moment."
Note "Leave this window open. Press Ctrl+C here to stop the dashboard."
Write-Host ""

# Opened from a job so the browser fires once the server is actually listening
# rather than onto a connection-refused page.
Start-Job -ScriptBlock {
    param($u)
    for ($i = 0; $i -lt 40; $i++) {
        try { Invoke-WebRequest "$u" -UseBasicParsing -TimeoutSec 2 | Out-Null; break }
        catch { Start-Sleep -Milliseconds 500 }
    }
    Start-Process $u
} -ArgumentList $url | Out-Null

& $venvPy -m hkrd.serve --port $Port
