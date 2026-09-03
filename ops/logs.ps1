<#
.SYNOPSIS
    Collect everything needed to work out why the deployed app is not answering.

.DESCRIPTION
    Prints to the window AND writes deploy-diagnosis.txt in the project folder,
    so the whole thing can be attached rather than retyped.

    Reads only. Creates nothing, changes nothing, costs nothing.
#>
[CmdletBinding()]
param([string] $App = "hkrd")

$ErrorActionPreference = "Stop"

$script:FlyExe = $null
$cmd = Get-Command fly -CommandType Application -ErrorAction SilentlyContinue |
       Select-Object -First 1
if ($cmd) { $script:FlyExe = $cmd.Source }
elseif (Test-Path "$env:USERPROFILE\.fly\bin\fly.exe") {
    # The installer puts it here and updates PATH for NEW windows only. This
    # one may have been open the whole time.
    $script:FlyExe = "$env:USERPROFILE\.fly\bin\fly.exe"
}
if (-not $script:FlyExe) {
    Write-Host "  flyctl is not installed, or not on PATH in this window." -ForegroundColor Red
    Write-Host "  Close this window, open a new one, and try again."
    exit 1
}

# SilentlyContinue for the same reason deploy.ps1 uses it: 2>&1 on a native
# program wraps stderr in ErrorRecords, and under "Stop" that is terminating.
function Fly-Text {
    $ErrorActionPreference = "SilentlyContinue"
    return (& $script:FlyExe @args 2>&1 | Out-String)
}

$out = New-Object System.Text.StringBuilder
function Section ($title, $text) {
    $bar = "=" * 70
    [void]$out.AppendLine($bar)
    [void]$out.AppendLine("  $title")
    [void]$out.AppendLine($bar)
    [void]$out.AppendLine($text)
    [void]$out.AppendLine("")
    Write-Host ""
    Write-Host "  $title" -ForegroundColor Cyan
    Write-Host $text
}

Write-Host ""
Write-Host "  Collecting diagnosis for $App" -ForegroundColor White
Write-Host "  This reads only. Nothing is created or changed."

Section "flyctl version"        (Fly-Text version)
Section "app status"            (Fly-Text status -a $App)
Section "machines"              (Fly-Text machine list -a $App)
Section "volumes"               (Fly-Text volumes list -a $App)

# Names only. `fly secrets list` never prints values -- it shows a digest --
# so this is safe to paste anywhere. It answers "did all six arrive", which is
# the question, and it cannot answer "are they correct", which it must not.
Section "secret NAMES (no values, by design)" (Fly-Text secrets list -a $App)

# --no-tail, or this never returns.
Section "recent logs"           (Fly-Text logs -a $App --no-tail)

$path = Join-Path (Get-Location) "deploy-diagnosis.txt"
[System.IO.File]::WriteAllText($path, $out.ToString(), (New-Object System.Text.UTF8Encoding $false))

Write-Host ""
Write-Host "  ------------------------------------------------------------" -ForegroundColor Yellow
Write-Host "   Written to: $path" -ForegroundColor White
Write-Host ""
Write-Host "   Attach that file. It holds no secret VALUES -- Fly does not" -ForegroundColor Yellow
Write-Host "   print them, and this asked only for their names." -ForegroundColor Yellow
Write-Host "  ------------------------------------------------------------" -ForegroundColor Yellow
