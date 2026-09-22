@echo off
REM Double-click to fetch the week's tipster videos and put their tips on the dashboard.
REM Runs on this PC because YouTube blocks the server. Best after 20:00 two days before a meeting, or the day before.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "ops\tips.ps1"
echo.
pause
