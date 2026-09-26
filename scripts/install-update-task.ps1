# Run only after update_bot.py --initialize succeeds.
[CmdletBinding()]
param([switch]$Disable)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$taskName = 'Taki-OurNotes-AutoUpdate'
if ($Disable) {
    Disable-ScheduledTask -TaskName $taskName | Out-Null
    Write-Output 'Auto update disabled. The running bot was not stopped.'
    exit 0
}
$state = Join-Path $root 'data\updater\state.json'
if (-not (Test-Path -LiteralPath $state)) { throw 'Run update_bot.py --initialize first.' }
$basePython = & (Join-Path $root '.venv\Scripts\python.exe') -c 'import sys; print(sys._base_executable)'
if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve base Python.' }
$pythonw = Join-Path (Split-Path -Parent $basePython) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) { throw 'pythonw.exe not found.' }
$script = Join-Path $root 'scripts\update_bot.py'
$action = New-ScheduledTaskAction -Execute $pythonw -Argument ('-B -X utf8 "{0}" --once' -f $script) -WorkingDirectory $root
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$logon = New-ScheduledTaskTrigger -AtLogOn -User $user
$repeat = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(5)) -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($logon, $repeat) -Settings $settings -Principal $principal -Description 'Check main every 5 minutes; validate in isolation, switch or restore Taki only.' -Force | Out-Null
Write-Output 'Installed: Taki-OurNotes-AutoUpdate, every 5 minutes and at user logon.'
