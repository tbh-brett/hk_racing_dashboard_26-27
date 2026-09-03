@echo off
REM Double-click this to open the dashboard.
REM -ExecutionPolicy Bypass is why this file exists: Windows refuses to run
REM .ps1 files by default, and that stops the script dead. Passing it here
REM applies to this one run only and changes nothing about the machine.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "ops\start.ps1"
echo.
echo The dashboard has stopped. Close this window.
pause
