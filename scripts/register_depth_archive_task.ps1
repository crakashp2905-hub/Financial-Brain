# Registers the weekday 09:10 depth and open-interest archive as a Windows task, using the same user as the existing
# "FinanceBrain Paper Intraday" task. It runs scripts\depth_archive.cmd (fb depth-archive): read-only against Kite, never
# places an order. Run it once yourself:
#   powershell -ExecutionPolicy Bypass -File scripts\register_depth_archive_task.ps1
# Re-running it replaces the task (-Force), e.g. to move from the interim launcher to this tracked script.
# Remove it with: Unregister-ScheduledTask -TaskName 'FinanceBrain Depth Archive' -Confirm:$false
$ref = Get-ScheduledTask -TaskName 'FinanceBrain Paper Intraday'
$root = Split-Path -Parent $PSScriptRoot
$act = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('"{0}\scripts\run_hidden.vbs" "{0}\scripts\depth_archive.cmd"' -f $root)
$trg = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 09:10
# StartWhenAvailable is off on purpose: a late start after the open only collects part of a day.
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable:$false -ExecutionTimeLimit (New-TimeSpan -Hours 7) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -Force -TaskName 'FinanceBrain Depth Archive' -Action $act -Trigger $trg -Settings $set -Principal $ref.Principal -Description 'Market-hours order-book and open-interest archive for every F&O name (data\depth_archive.duckdb). Read-only; never places an order.'
