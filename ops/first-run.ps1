<#
.SYNOPSIS
    First time on a new PC: find the old dashboard, download this one, start it.

.DESCRIPTION
    Meant to be run as a single line, because pasting a multi-line block into
    the PowerShell console does not work — the console reads line by line, so
    an `if {...}` / `else {...}` split across lines executes the if and then
    chokes on the orphaned else:

        iwr -useb https://raw.githubusercontent.com/tbh-brett/hk_racing_dashboard_26-27/main/ops/first-run.ps1 | iex

    Downloading and running is also how this gets past the execution policy.
    Windows PowerShell refuses to run a .ps1 file by default, which would stop
    .\ops\start.ps1 dead; a script that arrives as text and is piped to iex is
    not a file, so it runs, and the first thing it does is lift that
    restriction for THIS WINDOW ONLY (-Scope Process, gone when it closes).
#>

$ErrorActionPreference = "Stop"

function Say  ($t) { Write-Host "  $t" }
function Good ($t) { Write-Host "  $t" -ForegroundColor Green }
function Bad  ($t) { Write-Host "  $t" -ForegroundColor Red }

Write-Host ""
Write-Host "  Setting up the racing dashboard" -ForegroundColor White
Write-Host "  -------------------------------"
Write-Host ""

# ── the execution policy ─────────────────────────────────────────────────────
# Process scope: this window only, no administrator needed, and it is gone the
# moment the window closes. Nothing about the machine is changed permanently.
try {
    Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
    Say "scripts allowed in this window"
} catch {
    Bad "Could not change the execution policy: $_"
}

# ── git ──────────────────────────────────────────────────────────────────────
if (-not (Get-Command git -CommandType Application -ErrorAction SilentlyContinue)) {
    Bad "Git is not installed."
    Say "Get it from https://git-scm.com/download/win, click through the"
    Say "installer accepting the defaults, then open a NEW PowerShell window"
    Say "and paste the same line again."
    return
}

# ── find the old dashboard ───────────────────────────────────────────────────
# Located by its database rather than by folder name, because the folder may
# have been renamed but hkjc.db is what actually matters: it is where every
# race, bet and blackbook entry comes from.
Say "looking for your old dashboard folder (up to a minute)..."
$hit = Get-ChildItem $HOME -Recurse -Depth 6 -Filter hkjc.db -File -ErrorAction SilentlyContinue |
       Select-Object -First 1

if (-not $hit) {
    Bad "Could not find hkjc.db anywhere under $HOME"
    Say ""
    Say "That file is in the OLD dashboard folder, usually called"
    Say "hk_race_dashboard. Find it in File Explorer, then tell Claude the"
    Say "full path and it will adjust this."
    return
}

$oldRepo = $hit.Directory.FullName
$parent  = Split-Path $oldRepo -Parent
$newRepo = Join-Path $parent "hk_racing_dashboard_26-27"
Good "found it: $oldRepo"

# ── get this repo, NEXT TO the old one ───────────────────────────────────────
# Side by side on purpose: the build step reads the old repo at ..\ and that is
# the default it looks for.
if (Test-Path $newRepo) {
    Say "already downloaded — updating"
    Set-Location $newRepo
    & git pull
} else {
    Say "downloading the dashboard..."
    Set-Location $parent
    & git clone https://github.com/tbh-brett/hk_racing_dashboard_26-27
    if ($LASTEXITCODE -ne 0) { Bad "Download failed."; return }
    Set-Location $newRepo
}
Good "ready: $(Get-Location)"

# ── start it ─────────────────────────────────────────────────────────────────
Write-Host ""
Say "Starting the dashboard. First time takes a few minutes."
Write-Host ""
& .\ops\start.ps1
