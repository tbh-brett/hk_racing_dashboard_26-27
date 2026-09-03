@echo off
REM Double-click this to put the dashboard on the internet.
REM
REM -ExecutionPolicy Bypass is why this file exists: Windows refuses to run
REM .ps1 files by default, and that stops the script dead. Passing it here
REM applies to this one run only and changes nothing about the machine.
REM
REM Unlike the other three, this one costs money and reaches the outside
REM world. So it says what it is about to do and waits, rather than starting
REM the moment the window opens.
cd /d "%~dp0"
echo.
echo   Putting the dashboard on the internet
echo   -------------------------------------
echo.
echo   This creates a Fly.io machine, about US$5-7 a month, and a 1 GB
echo   volume, then copies your database onto it.
echo.
echo   You will be asked for two things from your Cloudflare R2 key file:
echo     - Access Key ID
echo     - Secret Access Key
echo   The secret is not shown on screen as you paste it. That is deliberate.
echo.
echo   It then prints a DASHBOARD PASSWORD, once. Save it before pressing
echo   Enter. Nothing stores it anywhere you can read it back, and it is the
echo   only thing standing in front of your betting ledger.
echo.
echo   A browser window may open so you can sign in to Fly.io.
echo.
echo   Safe to run again if it stops partway. Every step checks whether it
echo   has already been done.
echo.
set /p go=Start?  Y to go ahead, anything else to stop:  
if /i not "%go%"=="Y" goto stopped
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "ops\deploy.ps1"
goto done
:stopped
echo.
echo   Stopped. Nothing was created and nothing was charged.
:done
echo.
pause
