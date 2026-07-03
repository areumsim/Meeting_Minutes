@echo off
chcp 65001 >nul
title Meeting Minutes - Unified Launcher
cd /d "%~dp0"
python run_meeting.py %*
if errorlevel 1 (
    echo.
    where python >nul 2>&1
    if errorlevel 1 (
        echo  [ERROR] Python not found.
        echo  Install Python 3.9+ from python.org.
    ) else (
        echo  [ERROR] Command failed. Check data\logs\run_py.log or console output.
    )
    echo.
    pause
)
