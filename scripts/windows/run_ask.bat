@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title Vault Ask
cd /d "%~dp0..\.."

if "%~1"=="" (
    echo.
    echo  Usage: run_ask.bat "question" [--max-notes 5] [--show-sources]
    echo.
    set /p QUESTION="  Question: "
    if "!QUESTION!"=="" (
        exit /b 1
    )
    python run_meeting.py ask "!QUESTION!" --show-sources
) else (
    python run_meeting.py ask %*
)

set ERR=%ERRORLEVEL%
if not "%ERR%"=="0" (
    echo.
    echo  [ERROR] ask failed. Run scripts\windows\run_reindex.bat first if the vault index is missing.
    echo.
    pause
)

exit /b %ERR%
