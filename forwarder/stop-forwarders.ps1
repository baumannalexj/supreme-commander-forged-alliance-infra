<#
.SYNOPSIS
  Stop ONLY the processes recorded in <EventsDir>\pids.json (and their child processes).
  A pid is stopped only if a process with that pid still has the recorded start time (guards against pid reuse).
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$eventsDir = if ($env:SUPCOM_EVENTS_DIR) { $env:SUPCOM_EVENTS_DIR } else { Join-Path $env:LOCALAPPDATA 'supcom-events' }
$pidFile = Join-Path $eventsDir 'pids.json'
if (-not (Test-Path $pidFile)) { Write-Host "No pids.json; nothing to stop."; return }

$doc = Get-Content $pidFile -Raw | ConvertFrom-Json
foreach ($e in @($doc.processes)) {
  $p = Get-Process -Id $e.pid -ErrorAction SilentlyContinue
  if (-not $p) { Write-Host "$($e.name) pid $($e.pid): already gone"; continue }
  $recorded = ([datetime]$e.started).ToUniversalTime()
  if ([math]::Abs(($p.StartTime.ToUniversalTime() - $recorded).TotalSeconds) -gt 2) {
    Write-Warning "$($e.name) pid $($e.pid): start time differs (pid reused?); not touching it"; continue
  }
  # /T also ends children spawned by this pid (gh -> gh-webhook), nothing else.
  & taskkill.exe /PID $e.pid /T /F | Out-Null
  Write-Host "$($e.name) pid $($e.pid): stopped"
}
Remove-Item -LiteralPath $pidFile
