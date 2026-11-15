[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [Parameter(Mandatory = $true)]
    [string] $ConfigPath,

    [string] $RepositoryRoot = (Split-Path -Parent $PSScriptRoot),

    [string] $TaskName = 'FinancialBrain-Paper-Daily-Finalize',

    [datetime] $At = [datetime]::ParseExact('16:15', 'HH:mm', [Globalization.CultureInfo]::InvariantCulture)
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath $RepositoryRoot).Path
$config = (Resolve-Path -LiteralPath $ConfigPath).Path
$wrapper = Join-Path $repo 'scripts\paper_daily_finalize.cmd'
$python = Join-Path $repo '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $wrapper -PathType Leaf)) {
    throw "Paper daily wrapper was not found: $wrapper"
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Create the repository virtual environment before registering the task: $python"
}
$configDocument = Get-Content -LiteralPath $config -Raw | ConvertFrom-Json
if ($configDocument.schema_version -ne 1) {
    throw 'Daily-cycle config must use schema_version 1.'
}
if ($WhatIfPreference) {
    Write-Output "What if: Register $TaskName for weekdays at $($At.ToString('HH:mm')) local system time."
    Write-Output "What if: Run `"$wrapper`" `"$config`" as $([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)."
    return
}

$atTime = Get-Date -Date ([datetime]::Today.Add($At.TimeOfDay))
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek @(
    [System.DayOfWeek]::Monday,
    [System.DayOfWeek]::Tuesday,
    [System.DayOfWeek]::Wednesday,
    [System.DayOfWeek]::Thursday,
    [System.DayOfWeek]::Friday
) -At $atTime
$escapedWrapper = $wrapper.Replace('"', '""')
$escapedConfig = $config.Replace('"', '""')
$arguments = '/d /s /c ""{0}" "{1}""' -f $escapedWrapper, $escapedConfig
$action = New-ScheduledTaskAction -Execute $env:ComSpec -Argument $arguments -WorkingDirectory $repo
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description 'Runs the local registered paper-book close and evidence workflow. Sends no broker orders.'

if ($PSCmdlet.ShouldProcess($TaskName, 'Register or replace weekday paper-close task')) {
    Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
    Write-Output "Registered $TaskName for weekdays at $($At.ToString('HH:mm')) local system time."
    Write-Output "The task runs as $([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) and requires that user to be logged on."
}
