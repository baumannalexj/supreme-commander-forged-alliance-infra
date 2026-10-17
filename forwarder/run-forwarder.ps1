<#
.SYNOPSIS
  Run `gh webhook forward` for one repo in the foreground of this terminal tab and restart it whenever it exits.
  `gh webhook forward` exits when its websocket drops (e.g. "close 1006 unexpected EOF"); without this loop the
  pipeline silently goes dead after the first disconnect. Every exit and restart is logged with a timestamp to the
  tab and to logs\forwarder-loop.log. Started by start-forwarders.ps1; registers its pid in <EventsDir>\pids.d.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$Repo,
  [Parameter(Mandatory)][string]$Name,
  [Parameter(Mandatory)][int]$Port,
  [Parameter(Mandatory)][string]$Secret,
  [Parameter(Mandatory)][string]$EventsDir,
  [string]$Events = 'issues,issue_comment,push,pull_request,workflow_run'
)
$logDir = Join-Path $EventsDir 'logs'
New-Item -ItemType Directory -Force -Path (Join-Path $EventsDir 'pids.d'), $logDir | Out-Null
$me = Get-Process -Id $PID
[pscustomobject]@{ name = "forward:$Name"; pid = $PID; started = $me.StartTime.ToUniversalTime().ToString('o') } |
  ConvertTo-Json | Set-Content (Join-Path $EventsDir "pids.d\$Name.json")
$safe = $Repo -replace '[^A-Za-z0-9_.-]', '_'
function Log($msg) {
  $line = "$((Get-Date).ToString('s')) [$Name] $msg"
  Write-Host $line
  Add-Content (Join-Path $logDir 'forwarder-loop.log') $line
}
$delay = 2
while ($true) {
  $t0 = Get-Date
  Log "starting gh webhook forward for $Repo"
  & gh webhook forward "--repo=$Repo" "--events=$Events" "--url=http://127.0.0.1:$Port/webhook" "--secret=$Secret" 2>&1 |
    Tee-Object -FilePath (Join-Path $logDir "forward-$safe.out.log") -Append
  # reset backoff after a healthy run (>60 s), otherwise back off up to 60 s
  if (((Get-Date) - $t0).TotalSeconds -gt 60) { $delay = 2 } else { $delay = [Math]::Min($delay * 2, 60) }
  Log "forwarder exited; restarting in ${delay}s"
  Start-Sleep -Seconds $delay
}
