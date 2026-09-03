@echo off
REM Double-click to fetch race meetings and trials the database is missing.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "ops\catch-up.ps1"
echo.
pause
