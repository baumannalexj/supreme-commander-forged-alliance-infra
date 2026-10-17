<#
.SYNOPSIS
  Run the webhook receiver in the foreground of this terminal tab (output is tee'd to logs\receiver.out.log).
  Started by start-forwarders.ps1 in the "receiver" tab; registers its pid in <EventsDir>\pids.d for stop-forwarders.ps1.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][int]$Port,
  [Parameter(Mandatory)][string]$Secret,
  [Parameter(Mandatory)][string]$EventsDir
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
New-Item -ItemType Directory -Force -Path (Join-Path $EventsDir 'pids.d'), (Join-Path $EventsDir 'logs') | Out-Null
$me = Get-Process -Id $PID
[pscustomobject]@{ name = 'receiver'; pid = $PID; started = $me.StartTime.ToUniversalTime().ToString('o') } |
  ConvertTo-Json | Set-Content (Join-Path $EventsDir 'pids.d\receiver.json')
$env:SUPCOM_WEBHOOK_SECRET = $Secret
$env:SUPCOM_RECEIVER_PORT = "$Port"
Set-Location $repoRoot
Write-Host "receiver on http://127.0.0.1:$Port (nothing to do here; every webhook shows up as a POST line)"
& (Join-Path $repoRoot '.venv\Scripts\python.exe') -m receiver 2>&1 | Tee-Object -FilePath (Join-Path $EventsDir 'logs\receiver.out.log') -Append
