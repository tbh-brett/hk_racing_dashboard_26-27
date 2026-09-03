<#
.SYNOPSIS
    Download the latest dashboard update and open it locally.

.EXAMPLE
    .\ops\update.ps1
#>
[CmdletBinding()]
param(
    [int] $Port = 8000
)

$ErrorActionPreference = "Stop"

function Step ($number, $text) {
    Write-Host ""
    Write-Host "[$number] $text" -ForegroundColor Cyan
}

function Note ($text) {
    Write-Host "    $text" -ForegroundColor DarkGray
}

function Ok ($text) {
    Write-Host "    $text" -ForegroundColor Green
}

function Die ($text) {
    Write-Host ""
    Write-Host "    $text" -ForegroundColor Red
    Write-Host ""
    exit 1
}

function Stop-ProcessTree ([int] $ProcessId) {
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId" `
                    -ErrorAction SilentlyContinue)
    foreach ($child in $children) {
        Stop-ProcessTree -ProcessId ([int] $child.ProcessId)
    }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Stop-Dashboard ([int] $DashboardPort) {
    $listener = Get-NetTCPConnection -LocalPort $DashboardPort -State Listen `
                -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listener) {
        Note "nothing is running on port $DashboardPort"
        return
    }

    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)" `
               -ErrorAction SilentlyContinue
    if (-not $process -or $process.CommandLine -notmatch "hkrd\.serve") {
        Die "Port $DashboardPort is being used by another program. Stop that program, then run this again."
    }

    $root = $process
    while ($root.ParentProcessId) {
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId = $($root.ParentProcessId)" `
                  -ErrorAction SilentlyContinue
        if (-not $parent -or $parent.CommandLine -notmatch "hkrd\.serve") {
            break
        }
        $root = $parent
    }

    Stop-ProcessTree -ProcessId ([int] $root.ProcessId)
    Wait-Process -Id $root.ProcessId -ErrorAction SilentlyContinue
    Ok "stopped the previous dashboard"
}

Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Get-Command git -CommandType Application -ErrorAction SilentlyContinue)) {
    Die "Git is not installed. Get it from https://git-scm.com/download/win, then run this again."
}

$branch = (& git branch --show-current).Trim()
if (-not $branch) {
    Die "This folder is not on a named Git branch. Ask the person who supplied the update which branch to use."
}

$upstream = (& git rev-parse --abbrev-ref --symbolic-full-name "@{u}" 2>$null).Trim()
if ($LASTEXITCODE -ne 0 -or -not $upstream) {
    Die "The '$branch' branch does not track an online branch, so there is nothing safe to update from."
}

$trackedChanges = @(& git status --porcelain | Where-Object { $_ -notmatch "^\?\? " })
if ($trackedChanges.Count) {
    Die "This folder has local code changes. They were left untouched; commit or stash them before updating."
}

Write-Host ""
Write-Host "  Updating the racing dashboard" -ForegroundColor White
Write-Host "  -----------------------------"

Step 1 "Checking for updates"
& git fetch origin --prune
if ($LASTEXITCODE -ne 0) {
    Die "Could not reach GitHub. Check your connection and run this again."
}

$behind = [int]((& git rev-list --count "HEAD..@{u}").Trim())
if ($behind) {
    Note "$behind new commit(s) on $upstream"
} else {
    Note "already up to date on $upstream"
}

Step 2 "Stopping the current dashboard"
Stop-Dashboard -DashboardPort $Port

Step 3 "Downloading the update"
& git pull --ff-only
if ($LASTEXITCODE -ne 0) {
    Die "Git could not apply the update. Your existing dashboard was stopped, but your files were not changed."
}
Ok "now on $((& git log -1 --oneline).Trim())"

$venvPy = ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    $venvPy = ".venv/bin/python"
}

if (Test-Path $venvPy) {
    Step 4 "Updating Python packages"
    & $venvPy -m pip install --quiet -e ".[dev,migrate]"
    if ($LASTEXITCODE -ne 0) {
        Die "The package update failed. The message above says why."
    }
    Ok "packages ready"
} else {
    Step 4 "Preparing Python packages"
    Note "no existing virtual environment; the starter will set it up"
}

Step 5 "Starting the dashboard"
& .\ops\start.ps1 -Port $Port