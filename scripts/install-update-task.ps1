# L3
# Input: -Disable; installation requires updater state, local Git/Python and the current Windows user.
# Output: Registers or disables the update task; installation records the Git executable.
# Pos: Explicit task-management entry described in L2.md.
# Effects/Dependencies: Windows Task Scheduler and data/updater/settings.json; settings may remain after installation failure. Disabling does not stop the bot.

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
$settingsFile = Join-Path $root 'data\updater\settings.json'
$gitCommand = Get-Command git -ErrorAction SilentlyContinue
$gitExecutable = if ($gitCommand) { $gitCommand.Source } elseif (Test-Path -LiteralPath $settingsFile) { (Get-Content -LiteralPath $settingsFile -Raw | ConvertFrom-Json).git_executable }
if (-not $gitExecutable -or -not (Test-Path -LiteralPath $gitExecutable -PathType Leaf)) { throw 'Git not found. Install Git or run this installer from a terminal with Git available.' }
$json = @{git_executable=$gitExecutable} | ConvertTo-Json
[System.IO.File]::WriteAllText($settingsFile, $json, (New-Object System.Text.UTF8Encoding($false)))
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
