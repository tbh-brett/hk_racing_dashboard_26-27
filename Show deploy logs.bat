@echo off
REM Double-click to find out why the deployed dashboard is not answering.
REM Reads only -- creates nothing, changes nothing, costs nothing.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "ops\logs.ps1"
echo.
pause
