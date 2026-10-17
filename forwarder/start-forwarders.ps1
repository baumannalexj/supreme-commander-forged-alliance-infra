<#
.SYNOPSIS
  Start the local webhook receiver and one `gh webhook forward` per repo, each in its own tab of a NEW
  Windows Terminal window named "supcom-events" (tabs: receiver, then one fwd-* tab per repo).

.DESCRIPTION
  - Repos come from -Repos, else forwarder\repos.local.txt (git-ignored; one `owner/name [tab-title]` per line,
    see repos.example.txt). The script fails if that file is missing.
  - Receiver tab: run-receiver.ps1 -> .venv python -m receiver on 127.0.0.1:<Port>.
  - Forwarder tabs: run-forwarder.ps1 -> `gh webhook forward` in a restart loop (needs the admin:repo_hook scope
    and admin on each repo). Output is shown live and tee'd to <EventsDir>\logs.
  - Each tab registers its pid in <EventsDir>\pids.d; stop-forwarders.ps1 stops only those.
  - If SUPCOM_WEBHOOK_SECRET is not set, a random per-session secret is generated and used for both sides
    (visible to local processes in the command lines; this is a localhost-only tool).

.EXAMPLE
  .\forwarder\start-forwarders.ps1
  .\forwarder\start-forwarders.ps1 -Repos owner/a,owner/b -Port 47801
#>
[CmdletBinding()]
param(
  [string[]]$Repos,
  [int]$Port = $(if ($env:SUPCOM_RECEIVER_PORT) { [int]$env:SUPCOM_RECEIVER_PORT } else { 47800 }),
  [string]$Events = 'issues,issue_comment,push,pull_request,workflow_run'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$eventsDir = if ($env:SUPCOM_EVENTS_DIR) { $env:SUPCOM_EVENTS_DIR } else { Join-Path $env:LOCALAPPDATA 'supcom-events' }
New-Item -ItemType Directory -Force -Path $eventsDir, (Join-Path $eventsDir 'logs') | Out-Null
$pidFile = Join-Path $eventsDir 'pids.json'
$pidsD = Join-Path $eventsDir 'pids.d'

function Default-Title($repo) { 'fwd-' + (($repo -split '/')[-1] -split '-')[-1] }

# Repo list: -Repos, else forwarder\repos.local.txt ("owner/name [tab-title]" per line, # comments allowed).
$entries = @()
if ($Repos) {
  foreach ($r in $Repos) { $entries += [pscustomobject]@{ repo = $r; title = Default-Title $r } }
} else {
  $repoFile = Join-Path $PSScriptRoot 'repos.local.txt'
  if (-not (Test-Path $repoFile)) {
    throw "Missing $repoFile. Copy forwarder\repos.example.txt to forwarder\repos.local.txt and list your repos (one 'owner/name [tab-title]' per line), or pass -Repos."
  }
  foreach ($line in Get-Content $repoFile) {
    $t = $line.Trim()
    if (-not $t -or $t.StartsWith('#')) { continue }
    $parts = $t -split '\s+'
    $title = if ($parts.Count -gt 1) { $parts[1] } else { Default-Title $parts[0] }
    $entries += [pscustomobject]@{ repo = $parts[0]; title = $title }
  }
  if ($entries.Count -eq 0) { throw "$repoFile lists no repos." }
}

$live = @()
if (Test-Path $pidsD) {
  foreach ($f in Get-ChildItem $pidsD -Filter *.json) {
    $e = Get-Content $f.FullName -Raw | ConvertFrom-Json
    if (Get-Process -Id $e.pid -ErrorAction SilentlyContinue) { $live += $e }
  }
}
if (Test-Path $pidFile) {
  $old = Get-Content $pidFile -Raw | ConvertFrom-Json
  $live += @($old.processes | Where-Object { Get-Process -Id $_.pid -ErrorAction SilentlyContinue })
}
if ($live.Count -gt 0) { throw "Already running (pids.json / pids.d list live pids). Run stop-forwarders.ps1 first." }
Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
if (Test-Path $pidsD) { Remove-Item -LiteralPath $pidsD -Recurse -Force }
New-Item -ItemType Directory -Force -Path $pidsD | Out-Null

# Python environment (pinned by uv.lock)
Push-Location $repoRoot
try { uv sync --frozen | Out-Null } finally { Pop-Location }

$secret = $env:SUPCOM_WEBHOOK_SECRET
if (-not $secret) {
  $bytes = New-Object byte[] 24
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  $secret = [Convert]::ToBase64String($bytes) -replace '[^A-Za-z0-9]', 'x'
}

$canForward = $true
if ((gh extension list 2>&1 | Out-String) -notmatch 'gh-webhook|cli/gh-webhook') {
  Write-Warning "gh webhook extension missing. Install: gh extension install cli/gh-webhook"; $canForward = $false
}
if ((gh auth status 2>&1 | Out-String) -notmatch 'admin:repo_hook') {
  Write-Warning "gh token lacks admin:repo_hook. Run: gh auth refresh -h github.com -s admin:repo_hook"; $canForward = $false
}

# One new Windows Terminal window "supcom-events", one tab per process.
$pwsh = (Get-Command pwsh -CommandType Application | Select-Object -First 1).Source
$tabs = @()
$tabs += "new-tab --title receiver `"$pwsh`" -NoExit -NoProfile -File `"$PSScriptRoot\run-receiver.ps1`" -Port $Port -Secret $secret -EventsDir `"$eventsDir`""
if ($canForward) {
  foreach ($en in $entries) {
    $tabs += "new-tab --title $($en.title) `"$pwsh`" -NoExit -NoProfile -File `"$PSScriptRoot\run-forwarder.ps1`" -Repo $($en.repo) -Name $($en.title) -Port $Port -Secret $secret -EventsDir `"$eventsDir`" -Events $Events"
  }
}
Start-Process -FilePath wt.exe -ArgumentList ('-w supcom-events ' + ($tabs -join ' ; '))

# Wait for /health
$ok = $false
for ($i = 0; $i -lt 60 -and -not $ok; $i++) {
  try { $ok = (Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 2).status -eq 'ok' } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $ok) { throw "Receiver did not become healthy on port $Port; see the receiver tab and $eventsDir\logs\receiver.out.log" }
Write-Host "Started window 'supcom-events' ($($tabs.Count) tabs). Health: http://127.0.0.1:$Port/health   Pids: $pidsD   Logs: $eventsDir\logs"
