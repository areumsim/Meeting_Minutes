@echo off
chcp 65001 >nul
title Plan Watcher
cd /d "%~dp0..\.."

python run_meeting.py plan-watcher %*
set ERR=%ERRORLEVEL%

if not "%ERR%"=="0" (
    echo.
    echo  [ERROR] plan watcher failed. Check obsidian.vault_path in config.json.
    echo.
    pause
)
exit /b %ERR%
