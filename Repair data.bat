@echo off
REM Double-click to fix pace figures, comments on running, and tags.
REM It shows what is damaged first and asks before fetching anything.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "ops\repair.ps1"
echo.
echo ---------------------------------------------------------------
echo  That was the report. Nothing was changed.
echo.
set /p go=Repair it now?  Y to go ahead, anything else to stop:  
if /i not "%go%"=="Y" goto done
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "ops\repair.ps1" -Fix
:done
echo.
pause
