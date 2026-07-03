@echo off
chcp 65001 >nul
title Vault Audio Email
cd /d "%~dp0..\.."

python run_meeting.py vault-audio --notify email %*
set ERR=%ERRORLEVEL%

if not "%ERR%"=="0" (
    echo.
    echo  [ERROR] vault-audio failed. Check obsidian.vault_path and email settings.
    echo.
    pause
)
exit /b %ERR%
