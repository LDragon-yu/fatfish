@echo off
setlocal
set "ROOT=%~dp0..\.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_fatfish.ps1" -Root "%ROOT%" %*
if errorlevel 1 pause
endlocal
