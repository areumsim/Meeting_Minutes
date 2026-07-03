@echo off
chcp 65001 >nul
title Vault Reindex
cd /d "%~dp0..\.."

python run_meeting.py reindex %*
set ERR=%ERRORLEVEL%

if not "%ERR%"=="0" (
    echo.
    echo  [ERROR] reindex failed. Check obsidian.vault_path in config.json.
    echo.
    pause
)
exit /b %ERR%
