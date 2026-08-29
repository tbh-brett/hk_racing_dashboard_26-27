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
    [string] $Region     = "hkg",
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
    $cmd = Get-Command fly -CommandType Application -ErrorAction SilentlyContinue
    if ($cmd) { $script:FlyExe = $cmd.Source }
    return [bool]$cmd
}

# Run flyctl and fail loudly. Every step here is one a silent failure would
# make invisible until the dashboard is serving an empty page.
function Invoke-Fly {
    $out = & $script:FlyExe @args 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host ($out | Out-String) -ForegroundColor Red
        Die "fly $($args -join ' ') failed. Nothing after this point ran."
    }
    return $out
}

Write-Host ""
Write-Host "  Deploying $App to Fly.io" -ForegroundColor White
Write-Host "  ------------------------"

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
$who = & $script:FlyExe auth whoami 2>&1
if ($LASTEXITCODE -ne 0) {
    Note "Not signed in. A browser window will open — sign in with the same"
    Note "account you subscribed with, then come back here."
    & $script:FlyExe auth login
    if ($LASTEXITCODE -ne 0) { Die "Sign-in did not complete." }
    $who = & $script:FlyExe auth whoami 2>&1
}
Ok "signed in as $who"

# ── 3. the app ───────────────────────────────────────────────────────────────
Step 3 "Creating the app"
$existing = & $script:FlyExe apps list 2>&1 | Out-String
if ($existing -match "(?m)^\s*$([regex]::Escape($App))\s") {
    Ok "$App already exists"
} else {
    Invoke-Fly apps create $App | Out-Null
    Ok "created $App"
}

# ── 4. the volume ────────────────────────────────────────────────────────────
Step 4 "Creating the volume the database lives on"
$vols = & $script:FlyExe volumes list -a $App 2>&1 | Out-String
if ($vols -match [regex]::Escape($Volume)) {
    Ok "$Volume already exists"
} else {
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

$payload | & $script:FlyExe secrets import -a $App --stage
if ($LASTEXITCODE -ne 0) { Die "Setting the secrets failed." }

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
Invoke-Fly deploy -a $App
Ok "deployed"

# ── 8. upload ────────────────────────────────────────────────────────────────
if (-not $SkipUpload -and (Test-Path "hkrd.db")) {
    Step 8 "Uploading the database"
    # Stopped first: copying a SQLite file while something is writing to it
    # produces a file that opens fine and is subtly wrong.
    $machine = (& $script:FlyExe machine list -a $App --json | ConvertFrom-Json)[0].id
    if (-not $machine) { Die "No machine found for $App." }

    Invoke-Fly machine stop $machine -a $App | Out-Null
    Note "machine $machine stopped"
    Invoke-Fly sftp put hkrd.db /data/hkrd.db -a $App | Out-Null
    Ok "uploaded"
    Invoke-Fly machine start $machine -a $App | Out-Null
    Note "machine restarted"
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

$snaps = & $script:FlyExe ssh console -a $App -C "litestream snapshots -config /app/ops/litestream.yml /data/hkrd.db" 2>&1 | Out-String
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
