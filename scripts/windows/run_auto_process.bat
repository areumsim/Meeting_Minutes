@echo off
chcp 65001 >nul
title Meeting Auto Process
cd /d "%~dp0..\.."

python run_meeting.py auto-process %*
exit /b %ERRORLEVEL%
