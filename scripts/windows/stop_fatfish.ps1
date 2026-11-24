[CmdletBinding()]
param(
    [string]$NapCatDir = ""
)

$ErrorActionPreference = "Continue"

$coreProcs = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and $_.CommandLine -match "-m\s+fatfish"
}
foreach ($proc in $coreProcs) {
    Write-Host "停止 core PID $($proc.ProcessId)"
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
}

if (-not $NapCatDir) { $NapCatDir = $env:FATFISH_NAPCAT_DIR }
if ($NapCatDir) {
    $napProcs = Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and $_.CommandLine -like "*$NapCatDir*"
    }
    foreach ($proc in $napProcs) {
        Write-Host "停止 NapCat/QQ PID $($proc.ProcessId)"
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "未指定 NapCatDir；如果 NapCat/QQ 窗口还在，请手动关闭。"
}
