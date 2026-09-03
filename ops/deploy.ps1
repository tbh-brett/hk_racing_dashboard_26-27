<#
.SYNOPSIS
    Deploy the dashboard to Fly.io. One command, start to finish.

.DESCRIPTION
    Everything from "nothing exists" to "the dashboard is up with its database
    replicating to R2". Safe to re-run: every step checks whether it has
    already been done and skips it.

    The R2 secret is read with Read-Host -AsSecureString and piped to
    `fly secrets import` on stdin. It is never typed as an argument, so it
    does not land in PowerShell history, in the process list, or on disk.

.EXAMPLE
    cd C:\path\to\hk_racing_dashboard_26-27
    .\ops\deploy.ps1

.EXAMPLE
    .\ops\deploy.ps1 -LegacyRepo C:\code\hk_race_dashboard
#>
[CmdletBinding()]
param(
    [string] $App        = "hkrd",
    [string] $Region     = "",      # blank: read primary_region from fly.toml
    [string] $Volume     = "hkrd_data",
    [string] $Bucket     = "hkrd-backups",
    [string] $AccountId  = "e5d11ee7921c7b9774b52de38dd915bf",
    [string] $LegacyRepo = "..\hk_race_dashboard",
    [switch] $SkipUpload
)

$ErrorActionPreference = "Stop"

function Step ($n, $text) { Write-Host ""; Write-Host "[$n] $text" -ForegroundColor Cyan }
function Ok   ($text)     { Write-Host "    $text" -ForegroundColor Green }
function Note ($text)     { Write-Host "    $text" -ForegroundColor DarkGray }
function Warn ($text)     { Write-Host "    $text" -ForegroundColor Yellow }
function Die  ($text)     { Write-Host ""; Write-Host "    $text" -ForegroundColor Red; exit 1 }

# The flyctl binary, resolved to a full path once and called through that.
#
# NOT a function named `Fly` calling `& fly`. PowerShell resolves names
# case-insensitively and checks functions before external commands, so
# `& fly` inside `function Fly` calls the function — the script died with
# "call depth overflow" on its first step. Found by running it.
$script:FlyExe = $null

function Resolve-Fly {
    # -First 1: Get-Command returns every match on PATH, and the .Source of a
    # multi-match result is the paths joined together, which is not a program.
    $cmd = Get-Command fly -CommandType Application -ErrorAction SilentlyContinue |
           Select-Object -First 1
    if ($cmd) { $script:FlyExe = $cmd.Source }
    return [bool]$cmd
}

# Running flyctl. Three ways, and NONE of them redirects with `2>&1` at the
# call site — which is the whole reason these exist.
#
# In Windows PowerShell 5.1, `2>&1` on a NATIVE program does not merge two text
# streams. It wraps every stderr line in an ErrorRecord, and with
# $ErrorActionPreference = "Stop" set at the top of this script that ErrorRecord
# is a TERMINATING error. The script dies AT the redirect and never reaches the
# next line — the one that reads $LASTEXITCODE and decides what to do.
#
# Which killed this deploy at step 2, in precisely the case step 2 exists to
# handle. `fly auth whoami` reports "no access token available" on stderr and
# exits non-zero; the sign-in that should have followed never ran, and the
# window closed on a NativeCommandError naming a command that had worked
# correctly. Found by running it on a machine that was not yet signed in.
#
# $ErrorActionPreference assigned INSIDE a function is function-scoped, so it
# reverts on return with no try/finally to get wrong.

# Output captured as text, exit code in $script:FlyExit. Never throws — for
# the questions whose answer is "no" as often as "yes".
#
# SilentlyContinue, not Continue. flyctl writes a "Metrics token unavailable"
# warning to stderr on most commands, and with Continue that warning is DISPLAYED
# as a red NativeCommandError quoting this function's own source line — six
# times over a deploy, each one looking like the thing that just went wrong.
# Nothing is lost by silencing it: the 2>&1 above still captures the text into
# $text, and Invoke-Fly prints all of it when the exit code is non-zero. The
# display is suppressed; the evidence is not.
function Invoke-FlyText {
    $ErrorActionPreference = "SilentlyContinue"
    $text = (& $script:FlyExe @args 2>&1 | Out-String)
    $script:FlyExit = $LASTEXITCODE
    return $text
}

# Output captured and swallowed, non-zero exit is fatal. For short commands
# whose success is the only interesting thing about them.
function Invoke-Fly {
    $out = Invoke-FlyText @args
    if ($script:FlyExit -ne 0) {
        Write-Host $out -ForegroundColor Red
        Die "fly $($args -join ' ') failed. Nothing after this point ran."
    }
    return $out
}

# Feed text to a flyctl command on stdin, byte for byte. Returns the exit code.
#
# NOT `$text | & $script:FlyExe ...`, which is what this was and which broke on
# the very first secret. Two independent faults in PowerShell 5.1's native
# pipe, both measured against a program that prints the bytes it receives
# rather than reasoned about:
#
#   A BOM ARRIVES FIRST. .NET Framework builds the child's stdin StreamWriter
#   from [Console]::InputEncoding and sets AutoFlush on it, and that flush
#   writes the encoding's preamble before any content does. Under the UTF-8
#   console this runs in, flyctl received "﻿HKRD_PASSWORD" and refused it
#   as a secret name. Setting $OutputEncoding does nothing — it is the INPUT
#   encoding that builds this writer, and that is the part that took a while.
#
#   A CRLF ARRIVES LAST. Piping a string appends PowerShell's line terminator,
#   so the final line gains a stray \r. The final line here is the R2 secret,
#   and a secret access key with a carriage return on the end fails against R2
#   as a 401 — indistinguishable from wrong keys, which is precisely the
#   afternoon docs/deploy.md warns about losing.
#
# Writing bytes to the raw stream answers both: no preamble, and nothing
# appended that we did not put there. It stays in memory — no temp file, so
# the secret still never touches disk, which is the whole point of using stdin
# rather than `fly secrets set KEY=value`.
function Send-FlyStdin {
    param([string] $Text, [string[]] $FlyArgs)

    # Must be BOM-less BEFORE Start(). The writer is constructed during Start
    # and its preamble is decided there; setting this afterwards is too late.
    try { [Console]::InputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName              = $script:FlyExe
    # .Arguments, not .ArgumentList — the latter is .NET Core only and this is
    # Windows PowerShell 5.1, on .NET Framework.
    $psi.Arguments             = ($FlyArgs | ForEach-Object {
                                    if ($_ -match '\s') { '"' + $_ + '"' } else { $_ }
                                  }) -join ' '
    $psi.RedirectStandardInput = $true
    $psi.UseShellExecute       = $false

    $proc  = [System.Diagnostics.Process]::Start($psi)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)   # GetBytes omits the preamble
    $proc.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
    $proc.StandardInput.BaseStream.Flush()
    $proc.StandardInput.Close()
    $proc.WaitForExit()
    return $proc.ExitCode
}

# Output streams to the window as it happens, non-zero exit is fatal. For the
# slow ones — the build, the 31 MB transfer — where a silent five minutes is
# indistinguishable from a hang, and for install-db.sh, whose report is the
# point of running it.
function Invoke-FlyLive {
    $ErrorActionPreference = "Continue"
    & $script:FlyExe @args
    if ($LASTEXITCODE -ne 0) {
        Die "fly $($args -join ' ') failed. Nothing after this point ran."
    }
}

# The region is declared in fly.toml, and read from there rather than repeated
# here. Two copies drift, and the way this one drifts is quiet: the volume is
# created in one region, `fly deploy` places the machine per fly.toml in
# another, and the machine comes up unable to find the volume it exists to
# mount. -Region still overrides, for trying somewhere else without editing
# the file.
if (-not $Region) {
    if (-not (Test-Path "fly.toml")) {
        Die "No fly.toml here. Run this from the project folder, not from ops\."
    }
    $m = Select-String -Path "fly.toml" -Pattern '^\s*primary_region\s*=\s*"([a-z]{3})"' |
         Select-Object -First 1
    if (-not $m) { Die "Could not read primary_region from fly.toml." }
    $Region = $m.Matches[0].Groups[1].Value
}

Write-Host ""
Write-Host "  Deploying $App to Fly.io, region $Region" -ForegroundColor White
Write-Host "  ----------------------------------------"

# ── 1. flyctl ────────────────────────────────────────────────────────────────
Step 1 "Checking for flyctl"
if (-not (Resolve-Fly)) {
    Warn "flyctl is not installed. Installing it now."
    Invoke-RestMethod https://fly.io/install.ps1 -UseBasicParsing | Invoke-Expression
    $env:PATH = "$env:USERPROFILE\.fly\bin;$env:PATH"
    if (-not (Resolve-Fly)) {
        Die "flyctl installed but is not on PATH. Close this window, open a new one, and run the script again."
    }
}
Ok "flyctl $((& $script:FlyExe version) -join ' ')"

# ── 2. login ─────────────────────────────────────────────────────────────────
Step 2 "Checking you are signed in to Fly"
$who = (Invoke-FlyText auth whoami).Trim()
if ($script:FlyExit -ne 0) {
    Note "Not signed in. A browser window will open — sign in with the same"
    Note "account you subscribed with, then come back here."
    Note "If no browser opens, copy the URL it prints and open that yourself."

    # Not captured: this one prints a URL and waits, and a captured prompt is
    # a hang with nothing on screen to explain it.
    Invoke-FlyLive auth login

    $who = (Invoke-FlyText auth whoami).Trim()
    if ($script:FlyExit -ne 0) {
        Die "Signed in, but Fly still reports no account. Run 'fly auth login' by hand and then run this again."
    }
}
Ok "signed in as $who"

# ── 3. the app ───────────────────────────────────────────────────────────────
Step 3 "Creating the app"
$existing = Invoke-FlyText apps list
if ($existing -match "(?m)^\s*$([regex]::Escape($App))\s") {
    Ok "$App already exists"
} else {
    Invoke-Fly apps create $App | Out-Null
    Ok "created $App"
}

# ── 4. the volume ────────────────────────────────────────────────────────────
Step 4 "Creating the volume the database lives on"
$vols = Invoke-FlyText volumes list -a $App
if ($vols -match [regex]::Escape($Volume)) {
    Ok "$Volume already exists"
} else {
    # Checked BEFORE anything is created, because Fly's answer on its own does
    # not lead anywhere. This deploy stopped at `Error: region hkg not found`
    # — true, and no help: hkg was retired in Fly's 2025 region consolidation
    # along with sixteen others, and nothing on screen said so or named a
    # replacement. Listing what IS on offer turns a dead end into a choice.
    #
    # A loose substring match on purpose. The point is to catch a region that
    # has been withdrawn entirely, and a stricter column-anchored regex would
    # start rejecting valid codes the day flyctl changes its table layout.
    $regions = Invoke-FlyText platform regions
    if ($script:FlyExit -eq 0 -and $regions -and $regions -notmatch [regex]::Escape($Region)) {
        Write-Host ""
        Write-Host $regions -ForegroundColor DarkGray
        Warn "Fly does not offer '$Region' any more."
        Warn "Pick a code from the list above, then run this again as:"
        Warn "    .\ops\deploy.ps1 -Region <code>"
        Die  "Nothing was created."
    }

    # 1 GB is about thirty times the current database. Fly volumes can be
    # grown later and cannot be shrunk.
    Invoke-Fly volumes create $Volume -a $App --region $Region --size 1 --yes | Out-Null
    Ok "created $Volume, 1 GB, $Region"
}

# ── 5. the secrets ───────────────────────────────────────────────────────────
Step 5 "Setting the secrets"
Note "Paste from the Cloudflare R2 token page. The secret is not echoed and"
Note "is not stored anywhere on this machine."
Write-Host ""

$keyId = Read-Host "    R2 Access Key ID    "
if ([string]::IsNullOrWhiteSpace($keyId)) { Die "No access key ID given." }

$secure = Read-Host "    R2 Secret Access Key" -AsSecureString
# NetworkCredential, not PtrToStringAuto. `Auto` picks the platform's default
# character set — UTF-16 on Windows, UTF-8 on Unix — while SecureStringToBSTR
# always writes UTF-16, so it silently returns mangled text off Windows. Caught
# by round-tripping a known value under pwsh on Linux: it came back unequal.
# This form is correct on Windows PowerShell 5.1 and on 7, everywhere.
$r2Secret = [System.Net.NetworkCredential]::new("", $secure).Password
if ([string]::IsNullOrWhiteSpace($r2Secret)) { Die "No secret access key given." }

# The password in front of the whole betting ledger. Generated here so it is
# never something guessable and never something reused.
$bytes = [byte[]]::new(24)
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$dashPassword = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')

Write-Host ""
Write-Host "    ------------------------------------------------------------" -ForegroundColor Yellow
Write-Host "     DASHBOARD PASSWORD — save this in your password manager NOW" -ForegroundColor Yellow
Write-Host ""
Write-Host "       $dashPassword" -ForegroundColor White
Write-Host ""
Write-Host "     It is the only thing in front of the betting ledger, and" -ForegroundColor Yellow
Write-Host "     nothing stores it anywhere you can read it back." -ForegroundColor Yellow
Write-Host "    ------------------------------------------------------------" -ForegroundColor Yellow
Write-Host ""
Read-Host "    Press Enter once you have saved it" | Out-Null

# stdin, not arguments. `fly secrets set KEY=value` would put the secret in
# PowerShell history and in the process list; `import` reads NAME=VALUE pairs
# from stdin, so neither happens.
#
# The endpoint is the HOST ONLY. R2's bucket page shows it with the bucket on
# the end, and Litestream appends the bucket from the replica URL itself — the
# request becomes /hkrd-backups/hkrd-backups and comes back 401, which sends
# you hunting through the keys, which are fine. See docs/deploy.md.
$payload = @(
    "HKRD_PASSWORD=$dashPassword"
    "LITESTREAM_REPLICA_URL=s3://$Bucket/hkrd"
    "LITESTREAM_ENDPOINT=https://$AccountId.r2.cloudflarestorage.com"
    "LITESTREAM_REGION=auto"
    "LITESTREAM_ACCESS_KEY_ID=$keyId"
    "LITESTREAM_SECRET_ACCESS_KEY=$r2Secret"
) -join "`n"

$code = Send-FlyStdin -Text $payload -FlyArgs @("secrets", "import", "-a", $App, "--stage")
if ($code -ne 0) { Die "Setting the secrets failed." }

$payload = $null; $r2Secret = $null; $secure.Dispose()
[System.GC]::Collect()
Ok "six secrets staged (applied on the next deploy)"

# ── 6. the database ──────────────────────────────────────────────────────────
Step 6 "Building the database locally"
if ($SkipUpload) {
    Note "skipped (-SkipUpload)"
} elseif (Test-Path "hkrd.db") {
    $mb = [math]::Round((Get-Item "hkrd.db").Length / 1MB, 1)
    Ok "hkrd.db already built, $mb MB"
} else {
    if (-not (Test-Path $LegacyRepo)) {
        Die "Cannot find the old repo at '$LegacyRepo'. It holds hkjc.db, reports/, cache/ and blackbook.json, which is where all the data comes from. Pass the right path with -LegacyRepo, or -SkipUpload to deploy without data."
    }
    Note "This takes about twenty seconds and prints what landed at each step."
    & python -m hkrd.jobs.bootstrap --legacy $LegacyRepo
    if ($LASTEXITCODE -ne 0) { Die "bootstrap failed. The output above says which step." }
    Ok "built"
}

# ── 7. deploy ────────────────────────────────────────────────────────────────
Step 7 "Deploying"
Note "First build pulls Python, numpy, scipy and pandas — several minutes."
Invoke-FlyLive deploy -a $App
Ok "deployed"

# ── 8. upload ────────────────────────────────────────────────────────────────
if (-not $SkipUpload -and (Test-Path "hkrd.db")) {
    Step 8 "Uploading the database"

    $machine = ((Invoke-FlyText machine list -a $App --json) | ConvertFrom-Json)[0].id
    if (-not $machine) { Die "No machine found for $App." }

    # Sent up BESIDE the live file, with the machine left RUNNING.
    #
    # Stopping first and writing straight over /data/hkrd.db is the obvious
    # sequence and it does not work: hallpass, the SSH server flyctl connects
    # to, is a process inside the VM, so a stopped machine has no SSH server
    # for SFTP to reach. And the running machine holds that database open with
    # a WAL beside it, so overwriting it in place leaves SQLite recovering the
    # old WAL onto the new file. ops/install-db.sh does the swap safely and
    # explains it at length.
    Note "sending 31 MB to /data/hkrd.db.new — a minute or two"
    Invoke-FlyLive sftp put hkrd.db /data/hkrd.db.new -a $App
    Ok "uploaded"

    # Verifies the transfer, renames it over the live file and clears the WAL
    # and Litestream state belonging to the database it replaced. Refuses, and
    # changes nothing, if what arrived is not a readable database.
    Invoke-FlyLive ssh console -a $App -C "/app/ops/install-db.sh"
    Ok "installed"

    # Restart, so uvicorn and Litestream reopen the file that is now there.
    Invoke-Fly machine restart $machine -a $App | Out-Null
    Note "machine $machine restarted on the uploaded database"
}

# ── 9. check ─────────────────────────────────────────────────────────────────
Step 9 "Checking it works"
Start-Sleep -Seconds 20      # boot, then Litestream's 10s sync interval

try {
    $health = Invoke-RestMethod "https://$App.fly.dev/api/health" -TimeoutSec 20
    if ($health.ok) { Ok "https://$App.fly.dev/api/health — ok" }
    else            { Warn "health check answered but not ok: $($health | ConvertTo-Json -Compress)" }
} catch {
    Warn "health check did not answer yet. Give it a minute, then: fly logs -a $App"
}

$snaps = Invoke-FlyText ssh console -a $App -C "litestream snapshots -config /app/ops/litestream.yml /data/hkrd.db"
if ($snaps -match "generation") {
    Ok "replication is live — R2 has a snapshot"
} else {
    Warn "no snapshot in R2 yet. If this persists, the usual cause is the"
    Warn "endpoint carrying /$Bucket on the end. Check with:"
    Warn "  fly logs -a $App | Select-String litestream"
}

Write-Host ""
Write-Host "  Done. Open it with:  fly open -a $App" -ForegroundColor White
Write-Host "  Sign in with the password you saved in step 5."
Write-Host ""
