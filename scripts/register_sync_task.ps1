<#
  Register the twice-daily Meta pull with Windows Task Scheduler.

  RUN THIS YOURSELF. It is not run for you: it installs something that wakes up
  and touches a live API on your machine twice a day, and that is a decision
  rather than a build step.

      powershell -ExecutionPolicy Bypass -File scripts\register_sync_task.ps1

  To see it afterwards:      Get-ScheduledTask -TaskName 'GrowthEngine-MetaSync'
  To run it once, now:       Start-ScheduledTask -TaskName 'GrowthEngine-MetaSync'
  To read what happened:     Get-Content logs\sync.log -Tail 20
  To remove it:              Unregister-ScheduledTask -TaskName 'GrowthEngine-MetaSync'

  WHY TWICE A DAY AND NOT ONCE

  Meta restates attributed conversions for about three days, so the pull re-reads
  a rolling window regardless -- a second run costs one extra window, not a
  second full import. What it buys is halving the staleness: with one nightly
  run, a question asked at 5pm is answered from data up to 17 hours old, and
  "spend is down" can mean "the morning has not been imported".

  06:00 and 18:00 local. The morning run lands before anyone looks; the evening
  one catches the day's spend while it is still worth reacting to.
#>

param(
    [string]$TaskName = 'GrowthEngine-MetaSync',
    [string]$Morning  = '06:00',
    [string]$Evening  = '18:00'
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$bat  = Join-Path $PSScriptRoot 'sync.bat'

if (-not (Test-Path $bat)) { throw "sync.bat not found at $bat" }

Write-Host "Project : $root"
Write-Host "Command : $bat"
Write-Host "Times   : $Morning and $Evening, daily"

$action = New-ScheduledTaskAction -Execute $bat -WorkingDirectory $root

$triggers = @(
    New-ScheduledTaskTrigger -Daily -At $Morning
    New-ScheduledTaskTrigger -Daily -At $Evening
)

# StartWhenAvailable: a laptop asleep at 06:00 should pull when it wakes, not
# skip the morning entirely and leave a gap nobody notices.
#
# MultipleInstances IgnoreNew: belt and braces. sync.py already takes a lock,
# but two pulls racing each other against a rate limiter is worth refusing in
# two places.
#
# ExecutionTimeLimit 2h: matches the stale-lock window in sync.py, so a hung
# run is killed rather than blocking every later one.
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries

# Runs as you, interactively. NOT as SYSTEM: the pull reads .env for the Meta
# token and the database url, and those belong to your profile. A SYSTEM task
# would also be a service account nobody remembers granting anything to.
$principal = New-ScheduledTaskPrincipal `
    -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
    -Settings $settings -Principal $principal -Force | Out-Null

Write-Host ""
Write-Host "Registered '$TaskName'."
Write-Host "Test it now with:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Then read:         Get-Content '$root\logs\sync.log' -Tail 20"
