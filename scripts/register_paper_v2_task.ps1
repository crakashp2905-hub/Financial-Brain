# Registers the weekday 09:06 paper live-v2 cohort as a Windows task, using the same user as the existing "FinanceBrain Paper Intraday"
# task. Do this only after `fb learn approve` has put a skip rule in force: with none active the launcher exits at once. Paper only. Run it
# yourself: powershell -ExecutionPolicy Bypass -File scripts\register_paper_v2_task.ps1
# Remove it with: Unregister-ScheduledTask -TaskName 'FinanceBrain Paper Intraday v2' -Confirm:$false
$ref = Get-ScheduledTask -TaskName 'FinanceBrain Paper Intraday'
$root = Split-Path -Parent $PSScriptRoot
$act = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('"{0}\scripts\run_hidden.vbs" "{0}\scripts\paper_intraday_v2.cmd"' -f $root)
$trg = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 09:06
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable:$false -ExecutionTimeLimit (New-TimeSpan -Hours 7) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -Force -TaskName 'FinanceBrain Paper Intraday v2' -Action $act -Trigger $trg -Settings $set -Principal $ref.Principal -Description 'Paper live-v2 cohort: live-v1 plus the owner-approved skip rules, own ledger. Paper only.'
