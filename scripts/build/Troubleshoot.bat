@echo off
chcp 65001 >nul
title Meeting Minutes - Troubleshoot (console)
rem For normal use run "MeetingMinutes.bat" (background, no window).
rem This file runs the server with python.exe so errors/logs show in this window.
rem PYTHONUTF8=1 makes Python emit UTF-8 so Korean log messages display correctly.
rem NOTE: keep this file ASCII/English only. Korean text here breaks cmd parsing.

cd /d "%~dp0"
set "MM_DATA_DIR=%~dp0MeetingMinutesData"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if not exist "%~dp0python-embed\python.exe" (
    echo [ERROR] python-embed\python.exe not found.
    echo         Please re-extract the whole zip.
    pause
    exit /b 1
)

rem Starting here also STOPS a server that is already running in the background
rem (one instance at a time - it takes over port 8501), so the logs below belong
rem to this run. Exception: if a meeting is in progress it is left alone and the
rem existing window is opened instead.
echo Starting server with embedded Python...
echo (Ctrl+C stops the server. Closing this window with X may leave it running -
echo  use [Settings] - [Exit app] in the web page, or just run this file again.)
echo.
"%~dp0python-embed\python.exe" "%~dp0app\meeting_minutes_app\meeting_pipeline\run_ui_exe.py"
echo.
echo Server stopped.
pause
