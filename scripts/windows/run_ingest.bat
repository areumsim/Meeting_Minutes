@echo off
chcp 65001 >nul
title Meeting Minutes - Ingest
cd /d "%~dp0..\.."

if "%~1"=="" (
    echo.
    echo  Usage: run_ingest.bat ^<audio_path^> [--type meeting^|seminar^|lecture^|memo] [--force]
    echo  Example: run_ingest.bat "C:\Recordings\meeting.m4a" --type meeting
    echo.
    exit /b 1
)

python run_meeting.py ingest %*
set ERR=%ERRORLEVEL%

if not "%ERR%"=="0" (
    echo.
    echo  [ERROR] ingest failed. Check file path and config.json.
    echo.
    pause
)
exit /b %ERR%
