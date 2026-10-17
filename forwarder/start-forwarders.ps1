<#
.SYNOPSIS
  Start the local webhook receiver and one `gh webhook forward` per repo, in background processes.

.DESCRIPTION
  - Receiver: <repo>\.venv python -m receiver, bound to 127.0.0.1:<Port>.
  - Forwarders: `gh webhook forward --repo=<r> --events=... --url=http://127.0.0.1:<Port>/webhook --secret ...`
    (official cli/gh-webhook extension; needs the admin:repo_hook scope and admin on each repo).
  - Pids and start times go to <EventsDir>\pids.json; stop-forwarders.ps1 stops only those.
  - If SUPCOM_WEBHOOK_SECRET is not set, a random per-session secret is generated and used for both
    sides (it is visible to local processes in the forwarder command line; this is a localhost-only tool).
  - If the admin:repo_hook scope is missing, only the receiver starts and the refresh command is printed.

.EXAMPLE
  .\forwarder\start-forwarders.ps1
  .\forwarder\start-forwarders.ps1 -Repos owner/a,owner/b -Port 47801
#>
[CmdletBinding()]
param(
  [string[]]$Repos = @(
    'baumannalexj/supreme-commander-forged-alliance',
    'baumannalexj/supreme-commander-forged-alliance-research',
    'baumannalexj/supreme-commander-forged-alliance-reverse-engineer',
    'baumannalexj/supreme-commander-forged-alliance-infra'
  ),
  [int]$Port = $(if ($env:SUPCOM_RECEIVER_PORT) { [int]$env:SUPCOM_RECEIVER_PORT } else { 47800 }),
  [string]$Events = 'issues,issue_comment,push,pull_request,workflow_run'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$eventsDir = if ($env:SUPCOM_EVENTS_DIR) { $env:SUPCOM_EVENTS_DIR } else { Join-Path $env:LOCALAPPDATA 'supcom-events' }
New-Item -ItemType Directory -Force -Path $eventsDir, (Join-Path $eventsDir 'logs') | Out-Null
$pidFile = Join-Path $eventsDir 'pids.json'

if (Test-Path $pidFile) {
  $old = Get-Content $pidFile -Raw | ConvertFrom-Json
  $alive = @($old.processes | Where-Object { Get-Process -Id $_.pid -ErrorAction SilentlyContinue })
  if ($alive.Count -gt 0) { throw "Already running (pids.json lists live pids). Run stop-forwarders.ps1 first." }
}

# Python environment (pinned by uv.lock)
Push-Location $repoRoot
try { uv sync --frozen | Out-Null } finally { Pop-Location }
$py = Join-Path $repoRoot '.venv\Scripts\python.exe'

$secret = $env:SUPCOM_WEBHOOK_SECRET
if (-not $secret) {
  $bytes = New-Object byte[] 24
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  $secret = [Convert]::ToBase64String($bytes) -replace '[^A-Za-z0-9]', 'x'
}

$procs = @()
function Add-Proc($name, $p) {
  $script:procs += [pscustomobject]@{ name = $name; pid = $p.Id; started = $p.StartTime.ToUniversalTime().ToString('o') }
}

# Receiver
$prevSecret = $env:SUPCOM_WEBHOOK_SECRET; $prevPort = $env:SUPCOM_RECEIVER_PORT
try {
  $env:SUPCOM_WEBHOOK_SECRET = $secret; $env:SUPCOM_RECEIVER_PORT = "$Port"
  $p = Start-Process -FilePath $py -ArgumentList '-m', 'receiver' -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $eventsDir 'logs\receiver.out.log') -RedirectStandardError (Join-Path $eventsDir 'logs\receiver.err.log')
  Add-Proc 'receiver' $p
} finally {
  $env:SUPCOM_WEBHOOK_SECRET = $prevSecret; $env:SUPCOM_RECEIVER_PORT = $prevPort
}

# Wait for /health
$ok = $false
for ($i = 0; $i -lt 40 -and -not $ok; $i++) {
  try { $ok = (Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 2).status -eq 'ok' } catch { Start-Sleep -Milliseconds 250 }
}
if (-not $ok) { $procs | ConvertTo-Json | Set-Content (Join-Path $eventsDir 'pids.json'); throw "Receiver did not become healthy on port $Port; see $eventsDir\logs\receiver.err.log" }

# Forwarders (need cli/gh-webhook + admin:repo_hook)
$canForward = $true
$ext = (gh extension list 2>&1 | Out-String)
if ($ext -notmatch 'gh-webhook|cli/gh-webhook') {
  Write-Warning "gh webhook extension missing. Install: gh extension install cli/gh-webhook"; $canForward = $false
}
$auth = (gh auth status 2>&1 | Out-String)
if ($auth -notmatch 'admin:repo_hook') {
  Write-Warning "gh token lacks admin:repo_hook. Run: gh auth refresh -h github.com -s admin:repo_hook"; $canForward = $false
}
if ($canForward) {
  foreach ($r in $Repos) {
    $safe = $r -replace '[^A-Za-z0-9_.-]', '_'
    $fargs = @('webhook', 'forward', "--repo=$r", "--events=$Events", "--url=http://127.0.0.1:$Port/webhook", "--secret=$secret")
    $p = Start-Process -FilePath (Get-Command gh -CommandType Application | Select-Object -First 1).Source -ArgumentList $fargs `
          -WindowStyle Hidden -PassThru `
          -RedirectStandardOutput (Join-Path $eventsDir "logs\forward-$safe.out.log") -RedirectStandardError (Join-Path $eventsDir "logs\forward-$safe.err.log")
    Add-Proc "forward:$r" $p
  }
}

[pscustomobject]@{ port = $Port; processes = $procs } | ConvertTo-Json -Depth 4 | Set-Content $pidFile -Encoding utf8
$procs | Format-Table name, pid, started -AutoSize
Write-Host "Receiver: http://127.0.0.1:$Port/health   Pids: $pidFile   Logs: $eventsDir\logs"
