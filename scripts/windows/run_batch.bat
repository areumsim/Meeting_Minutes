@echo off
chcp 65001 >nul
title Meeting Minutes - Batch
cd /d "%~dp0..\.."

python run_meeting.py batch %*
set ERR=%ERRORLEVEL%

if not "%ERR%"=="0" (
    echo.
    where python >nul 2>&1
    if errorlevel 1 (
        echo  [ERROR] Python not found.
        echo  Install Python 3.9+ from python.org.
    ) else (
        echo  [ERROR] Batch command failed. Check data\logs\run_py.log or console output.
    )
    echo.
    pause
)
exit /b %ERR%
