# Windows PowerShell 5.1 requires a UTF-8 BOM to decode the Chinese messages below.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$env:PYTHONPATH = Join-Path $root "src"

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "[1/4] 创建 Python 虚拟环境..."
    py -3 -m venv .venv
}

Write-Host "[2/4] 安装项目和 QQ SDK..."
& $venvPython -c "import ournotes_bot, botpy, aiohttp, PIL"
if ($LASTEXITCODE -ne 0) {
    & $venvPython -m pip install --disable-pip-version-check setuptools
    if ($LASTEXITCODE -ne 0) { throw "安装 setuptools 失败" }
    & $venvPython -m pip install --disable-pip-version-check --no-build-isolation -e .
    if ($LASTEXITCODE -ne 0) { throw "安装项目依赖失败" }
}

if (-not (Test-Path -LiteralPath ".env")) {
    Write-Host "[3/4] 创建本地配置 .env..."
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
} else {
    Write-Host "[3/4] 保留已有 .env 配置。"
}

Write-Host "[4/4] 同步 Ournotes 数据..."
& $venvPython -m ournotes_bot.main sync

Write-Host ""
Write-Host "本地环境准备完成。"
Write-Host "请用记事本打开 .env，填写 QQ_APP_ID 和 QQ_APP_SECRET。"
Write-Host "填写后运行：.\deploy\start-bot.ps1"
