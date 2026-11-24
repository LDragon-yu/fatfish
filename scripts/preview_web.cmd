@echo off
setlocal
cd /d "%~dp0.."
set "FATFISH_PORT=8081"
set "FATFISH_DATA_DIR=D:\fatfish\private\preview"
set "FATFISH_PROACTIVE=0"
set "ONEBOT_ENABLED=0"
set "PATH=C:\Users\ycl\AppData\Local\miniconda3\envs\fatfish\Library\bin;C:\Users\ycl\AppData\Local\miniconda3\envs\fatfish;%PATH%"
"C:\Users\ycl\AppData\Local\miniconda3\envs\fatfish\python.exe" -m fatfish
