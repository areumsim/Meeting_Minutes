@echo off
chcp 65001 >nul
title Meeting Schedule
cd /d "%~dp0..\.."

python run_meeting.py schedule --write-dashboard %*
set ERR=%ERRORLEVEL%

if not "%ERR%"=="0" (
    echo.
    echo  [ERROR] schedule failed. Check obsidian.vault_path in config.json.
    echo.
    pause
)
exit /b %ERR%
