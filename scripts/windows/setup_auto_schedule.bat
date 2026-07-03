@echo off
chcp 65001 >nul
cd /d "%~dp0..\.."

set TASK=MeetingMinutes_AutoProcess
set SCRIPT=%~dp0run_auto_process.bat

schtasks /create /tn "%TASK%" /tr "\"%SCRIPT%\"" /sc HOURLY /mo 1 /st 08:00 /f
set ERR=%ERRORLEVEL%

if "%ERR%"=="0" (
  echo.
  echo [OK] %TASK% registered. It runs hourly.
  echo Run now: schtasks /run /tn "%TASK%"
  echo Delete : schtasks /delete /tn "%TASK%" /f
) else (
  echo.
  echo [ERROR] Failed to register the task. Run this file as Administrator.
)
echo.
pause
exit /b %ERR%
