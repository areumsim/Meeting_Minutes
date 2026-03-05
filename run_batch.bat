@echo off
chcp 65001 >nul
title Meeting Minutes - Batch
cd /d "%~dp0"
echo.
python run_batch.py %*
if errorlevel 1 (
    echo.
    where python >nul 2>&1
    if errorlevel 1 (
        echo  [ERROR] Python not found.
        echo  Install Python 3.9+: https://www.python.org/downloads/
    ) else (
        echo  [ERROR] Script failed. Check run_py.log for details.
    )
    echo.
    pause
)
