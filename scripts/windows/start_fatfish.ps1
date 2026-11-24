[CmdletBinding()]
param(
    [string]$Root = "",
    [string]$NapCatDir = "",
    [string]$Python = "",
    [switch]$NoNapCat,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"

if (-not $Root) {
    $Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}
Set-Location $Root

function Resolve-FatfishPython {
    param([string]$Root, [string]$Explicit)
    $candidates = @()
    if ($Explicit) { $candidates += $Explicit }
    if ($env:FATFISH_PYTHON) { $candidates += $env:FATFISH_PYTHON }
    $candidates += (Join-Path $Root ".venv\Scripts\python.exe")
    $conda = Get-Command conda -ErrorAction SilentlyContinue
    if ($conda) {
        $base = & $conda.Source info --base 2>$null
        if ($base) { $candidates += (Join-Path $base "envs\fatfish\python.exe") }
    }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) { return (Resolve-Path $candidate).Path }
    }
    return $null
}

$python = Resolve-FatfishPython -Root $Root -Explicit $Python
if (-not $python) {
    throw "找不到 Python。请创建 .venv，或用 conda 创建名为 fatfish 的环境，或用 -Python / FATFISH_PYTHON 指定 python.exe。"
}

# conda 环境的 ffmpeg 在 Library\bin；让 core 进程能直接找到
$pythonDir = Split-Path $python
if ((Split-Path $pythonDir -Leaf) -ieq "Scripts") { $envRoot = Split-Path $pythonDir } else { $envRoot = $pythonDir }
foreach ($sub in @("Library\bin", "Scripts")) {
    $dir = Join-Path $envRoot $sub
    if ((Test-Path $dir) -and ($env:PATH -notlike "*$dir*")) { $env:PATH = "$dir;$env:PATH" }
}
if (-not (Test-Path (Join-Path $Root ".env"))) {
    Write-Warning "没有 .env，请先运行 python scripts\setup_secrets.py，并设置 OWNER_QQ / ONEBOT_ACCESS_TOKEN。"
}

if (-not $NoNapCat) {
    if (-not $NapCatDir) { $NapCatDir = $env:FATFISH_NAPCAT_DIR }
    if (-not $NapCatDir) {
        throw "请用 -NapCatDir 指定 NapCat 目录，或设置环境变量 FATFISH_NAPCAT_DIR。"
    }
    if (-not (Test-Path $NapCatDir)) { throw "NapCat 目录不存在：$NapCatDir" }

    $launcher = Get-ChildItem -Path $NapCatDir -Filter "napcat.bat" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $launcher) {
        $launcher = Get-ChildItem -Path $NapCatDir -Filter "launcher.bat" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    }
    if (-not $launcher) { throw "在 $NapCatDir 里找不到 napcat.bat 或 launcher.bat。" }

    Write-Host "启动 NapCat：$($launcher.FullName)"
    Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "`"$($launcher.FullName)`"" -WorkingDirectory $launcher.DirectoryName
}

Write-Host "启动 fatfish core：$python -m fatfish"
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "`"$python`" -m fatfish" -WorkingDirectory $Root

$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8080/api/health" -TimeoutSec 1
        if ($response.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
}

if ($ready) {
    Write-Host "WebChat：http://127.0.0.1:8080"
    Write-Host "OneBot 反向 WS：ws://127.0.0.1:8080/onebot/ws"
    if (-not $NoBrowser) { Start-Process "http://127.0.0.1:8080" }
} else {
    Write-Warning "WebChat 还没起来，请看 core 窗口的报错。"
}
