@echo off
REM Double-click to download a copy of the live database as hkrd-fly.db, for analysis.
REM Reads only -- production is never written. The previous copy is kept as hkrd-fly.prev.db.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "ops\pull-db.ps1"
echo.
pause
