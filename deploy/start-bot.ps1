# Windows PowerShell 5.1 requires a UTF-8 BOM to decode the Chinese messages below.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$env:PYTHONPATH = Join-Path $root "src"

# Managed mode shares the updater lock and checks for an existing instance.
if (Test-Path -LiteralPath (Join-Path $root "data\updater\state.json")) {
    & (Join-Path $root ".venv\Scripts\python.exe") -B -X utf8 (Join-Path $root "scripts\update_bot.py") --start
    exit $LASTEXITCODE
}

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "尚未初始化本地环境，请先运行 .\deploy\setup-local.ps1"
}
if (-not (Test-Path -LiteralPath ".env") -and (-not $env:QQ_APP_ID -or -not $env:QQ_APP_SECRET)) {
    throw "缺少 QQ 凭证，请在用户环境变量中设置 QQ_APP_ID、QQ_APP_SECRET，或创建 .env"
}

& $venvPython -m ournotes_bot.main bot
