@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_fatfish.ps1" %*
pause
endlocal
