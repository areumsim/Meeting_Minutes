@echo off
chcp 65001 >nul
title Meeting Assistant
cd /d "%~dp0..\.."

if "%~1"=="" (
    python run_meeting.py status
) else (
    python run_meeting.py assistant %*
)

set ERR=%ERRORLEVEL%
if not "%ERR%"=="0" (
    echo.
    echo  [ERROR] assistant command failed. Check config.json and console output.
    echo.
    pause
)
exit /b %ERR%
