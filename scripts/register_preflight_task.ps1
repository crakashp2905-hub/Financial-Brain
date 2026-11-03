# Registers the weekday 08:30 pre-open health check as a Windows task, using the same user as the existing
# "FinanceBrain Paper Intraday" task. Read-only: it runs scripts\preflight.cmd (fb preflight). Run it once yourself:
#   powershell -ExecutionPolicy Bypass -File scripts\register_preflight_task.ps1
# Remove it with: Unregister-ScheduledTask -TaskName 'FinanceBrain Preflight' -Confirm:$false
$ref = Get-ScheduledTask -TaskName 'FinanceBrain Paper Intraday'
$root = Split-Path -Parent $PSScriptRoot
$act = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('"{0}\scripts\run_hidden.vbs" "{0}\scripts\preflight.cmd"' -f $root)
$trg = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 08:30
# StartWhenAvailable is off on purpose: a preflight that runs at 15:00 after a missed start is only noise.
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable:$false -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'FinanceBrain Preflight' -Action $act -Trigger $trg -Settings $set -Principal $ref.Principal -Description 'Pre-open health check: network, Kite login, disk, research DB, data contracts. Read-only; alerts if the paper session may not run.'
