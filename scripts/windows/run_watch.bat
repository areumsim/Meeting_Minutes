@echo off
chcp 65001 >nul
title Audio Folder Watch
cd /d "%~dp0..\.."

python run_meeting.py watch %*
set ERR=%ERRORLEVEL%

if not "%ERR%"=="0" (
    echo.
    echo  [ERROR] watch failed. Check vault_watcher.watch_folders in config.json.
    echo.
    pause
)
exit /b %ERR%
