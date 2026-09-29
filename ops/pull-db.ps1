<#
.SYNOPSIS
    Pull a consistent copy of the production database to this PC, for analysis.

.DESCRIPTION
    gbm-SPEC 14.9: the research copy is Fly's database. The PC's own hkrd.db
    stops wherever it was last synced (on 29 Sep 2026: 9 Sep, and no trials
    after 2 Sep), and an analysis run on it is an analysis of the past.

    Reads only, on the machine. SQLite's online backup copies the live
    database -- WAL included, consistent at one moment -- into /tmp at nice 19,
    as a reader; the copy is downloaded, then deleted from the machine.

    Writes hkrd-fly.db in the project folder (git-ignored, like every *.db).
    The copy it replaces is kept once, as hkrd-fly.prev.db.

    Never the other way round. Production holds bets, the blackbook and odds
    snapshots no local copy has; sending a database UP is ops/install-db.sh,
    and nothing here does it.

    Use it: python -m hkrd.jobs.replay_gbm --db hkrd-fly.db
            $env:HKRD_DB = "hkrd-fly.db"   (for the lab, claude\tools\model-lab)

.EXAMPLE
    .\ops\pull-db.ps1
#>
[CmdletBinding()]
param([string] $App = "hkrd", [string] $Out = "hkrd-fly.db")

$ErrorActionPreference = "Stop"

$script:FlyExe = $null
$cmd = Get-Command fly -CommandType Application -ErrorAction SilentlyContinue |
       Select-Object -First 1
if ($cmd) { $script:FlyExe = $cmd.Source }
elseif (Test-Path "$env:USERPROFILE\.fly\bin\fly.exe") {
    $script:FlyExe = "$env:USERPROFILE\.fly\bin\fly.exe"
}
if (-not $script:FlyExe) {
    Write-Host "  flyctl is not installed, or not on PATH in this window." -ForegroundColor Red
    exit 1
}

# SilentlyContinue for the reason logs.ps1 gives: 2>&1 on a native program
# wraps stderr in ErrorRecords, and under "Stop" that is terminating. And the
# output is checked rather than the exit code: on Windows `fly ssh console`
# ends every session with "The handle is invalid", success or not.
function Fly-Text {
    $ErrorActionPreference = "SilentlyContinue"
    return (& $script:FlyExe @args 2>&1 | Out-String)
}
function Die ($text) { Write-Host "  $text" -ForegroundColor Red; exit 1 }

$remote = "/tmp/hkrd-snap.db"
$part = "$Out.part"
# No quote of any kind inside the Python: Windows PowerShell 5.1 mangles quotes
# nested in a native program's argument, and flyctl splits -C on the single
# quotes around it. The paths arrive as arguments; the copy's path printed
# back is the sign it finished. (Run by hand this way on 29 Sep 2026.)
$code = "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); " +
        "s.backup(d); d.close(); print(sys.argv[2])"

Write-Host "  1/3  snapshot on the machine (a read, at nice 19)"
$got = Fly-Text ssh console -a $App -C "nice -n 19 python -c '$code' /data/hkrd.db $remote"
if ($got -notmatch [regex]::Escape($remote)) { Die "the snapshot did not complete:`n$got" }

Write-Host "  2/3  download"
if (Test-Path $part) { Remove-Item $part }
$got = Fly-Text ssh sftp get $remote $part -a $App
if (-not (Test-Path $part) -or (Get-Item $part).Length -eq 0) { Die "the download failed:`n$got" }

Write-Host "  3/3  delete the copy on the machine"
[void](Fly-Text ssh console -a $App -C "rm -f $remote")

if (Test-Path $Out) { Move-Item -Force $Out ($Out -replace '\.db$', '.prev.db') }
Move-Item $part $Out
$mb = [math]::Round((Get-Item $Out).Length / 1MB)
Write-Host "  $Out  $mb MB" -ForegroundColor Green

$py = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
if (Test-Path $py) {
    & $py -c ("import sqlite3; c = sqlite3.connect('file:${Out}?mode=ro', uri=True); " +
              "q = lambda s: c.execute(s).fetchone(); " +
              "print('  races   ', *q('select min(race_date), max(race_date), count(*) from races')); " +
              "print('  trials  ', *q('select max(trial_date), count(distinct trial_date) from trials')); " +
              "print('  integrity', q('pragma quick_check')[0])")
}
