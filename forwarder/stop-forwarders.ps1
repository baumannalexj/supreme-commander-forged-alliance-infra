<#
.SYNOPSIS
  Stop ONLY the processes recorded in <EventsDir>\pids.json and <EventsDir>\pids.d (and their child processes).
  A pid is stopped only if a process with that pid still has the recorded start time (guards against pid reuse).
  Closing a terminal tab's last process also closes the tab.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$eventsDir = if ($env:SUPCOM_EVENTS_DIR) { $env:SUPCOM_EVENTS_DIR } else { Join-Path $env:LOCALAPPDATA 'supcom-events' }
$pidFile = Join-Path $eventsDir 'pids.json'
$pidsD = Join-Path $eventsDir 'pids.d'
$procs = @()
if (Test-Path $pidFile) { $procs += @((Get-Content $pidFile -Raw | ConvertFrom-Json).processes) }
if (Test-Path $pidsD) { foreach ($f in Get-ChildItem $pidsD -Filter *.json) { $procs += Get-Content $f.FullName -Raw | ConvertFrom-Json } }
if ($procs.Count -eq 0) { Write-Host "No pids.json / pids.d; nothing to stop."; return }

foreach ($e in $procs) {
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
Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
if (Test-Path $pidsD) { Remove-Item -LiteralPath $pidsD -Recurse -Force }
