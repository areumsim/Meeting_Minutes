@echo off
chcp 65001 >nul
title Meeting Minutes - Web UI
cd /d "%~dp0"
echo.

python run_ui.py %*

if errorlevel 1 (
    echo.
    where python >nul 2>&1
    if errorlevel 1 (
        echo  [ERROR] Python not found.
        echo  Install Python 3.9+: https://www.python.org/downloads/
    ) else (
        echo  [ERROR] Script failed. Check logs for details.
    )
    echo.
    pause
)

rem 창이 닫히거나 Ctrl+C 후 혹시 남은 프로세스 정리
taskkill /f /fi "WINDOWTITLE eq Meeting Minutes - Web UI" >nul 2>&1
