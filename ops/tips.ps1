<#
.SYNOPSIS
    Fetch the week's tipster videos and put what they say on the dashboard.

.DESCRIPTION
    Runs on THIS PC, not the server: YouTube refuses the addresses cloud
    servers use, and answers a home connection. Three steps, each of which
    prints what it did:

      1. harvest   the newest Fact Check previews to raw\factcheck, the
                   newest Racing To Win interviews to raw\rtw, and
                   Sportsbet's fixed odds and Racing & Sports comments
                   for the next meeting to out\ (Sportsbet refuses the
                   server)
                   and Horse Detective's latest Threads posts to
                   raw\threads
      2. extract   quotes and picks for every upcoming meeting, to out\
      3. push      those to the dashboard, which replaces what each source
                   said before with what it says now

    WHEN: Fact Check publishes its preview at 20:00 two days before the
    meeting (measured on four: Monday for a Wednesday, Friday for a Sunday);
    Racing To Win posts its interviews around 16:00 the day before. The
    server has the card and its Chinese names by then on its own. So the
    evening before a meeting catches both, and running it again later is
    safe: the dashboard replaces, never duplicates.

    Signs in with HKRD_PASSWORD from the repo's .env file.

.EXAMPLE
    .\ops\tips.ps1               # all three steps
    .\ops\tips.ps1 -DryRun       # harvest, then show what would be sent
#>
[CmdletBinding()]
param(
    [switch] $DryRun,
    [string] $Base
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
# Horse names are Chinese; a console on a Chinese code page must not make
# Python fall over printing them.
$env:PYTHONUTF8 = "1"

$py = ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

function Head ($t) { Write-Host ""; Write-Host "  $t" -ForegroundColor Cyan; Write-Host "  $('-' * $t.Length)" -ForegroundColor DarkGray }

# A Hong Kong season opens in September; the titles carry no year.
$now = Get-Date
$season = if ($now.Month -ge 8) { $now.Year } else { $now.Year - 1 }

$baseArgs = @()
if ($Base) { $baseArgs = @("--base", $Base) }

Head "1. harvest  Fact Check (UCNpEBQatm4NALFlS-HO1nzg)"
& $py tools\harvest_youtube.py --channel UCNpEBQatm4NALFlS-HO1nzg `
    --lang zh-HK yue --season-year $season --kinds preview_zh `
    --pages 2 --limit 6 --out raw\factcheck
if ($LASTEXITCODE -ne 0) {
    Write-Host "  harvest reported a problem; carrying on with what is on disk" -ForegroundColor Yellow
}

Head "1b. harvest  Racing To Win interviews (PLK8zYRjJwINk)"
& $py tools\harvest_youtube.py --playlist PLK8zYRjJwINk `
    --lang en --season-year $season --kinds interview `
    --pages 1 --limit 6 --out raw\rtw
if ($LASTEXITCODE -ne 0) {
    Write-Host "  harvest reported a problem; carrying on with what is on disk" -ForegroundColor Yellow
}

Head "1c. harvest  Sportsbet (fixed odds + Racing & Sports comments)"
# Written to out\ and sent with the rest in step 3. Sportsbet has refused
# this PC as well as the server since 24 Sep; when it does, this step says
# so and the run carries on without it.
& $py tools\harvest_sportsbet.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Sportsbet reported a problem; carrying on without it" -ForegroundColor Yellow
}

Head "1d. harvest  Horse Detective (Threads, via Open RSS)"
# The feed only ever shows the latest four posts, and nothing that has
# scrolled off can be fetched again, so every run reads it. Its picks go
# up on race-day morning (11:49 on 13 Sep): the 10:00 run can miss one.
& $py tools\harvest_threads.py --out raw\threads
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Horse Detective reported a problem; carrying on without it" -ForegroundColor Yellow
}

Head "2. extract  upcoming meetings"
$extractArgs = @("tools\extract_tips.py", "--upcoming") + $baseArgs
if ($DryRun) { $extractArgs += "--dry-run" }
& $py @extractArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($DryRun) {
    Write-Host ""
    Write-Host "  dry run: nothing sent" -ForegroundColor Yellow
    exit 0
}

Head "3. push  to the dashboard"
$pushArgs = @("tools\push_tips.py", "--upcoming") + $baseArgs
& $py @pushArgs
exit $LASTEXITCODE
