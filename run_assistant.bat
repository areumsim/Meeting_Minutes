@echo off
chcp 65001 >nul
title 회의 비서 (Meeting Assistant)
cd /d "%~dp0"
REM 볼트 경로는 config.json 의 obsidian.vault_path 에서 읽음
echo.
if "%~1"=="" (
    python meeting_assistant.py status
) else (
    python meeting_assistant.py %*
)
if errorlevel 1 (
    echo.
    echo  [ERROR] meeting_assistant.py 실패. config.json 을 확인하세요.
    echo.
    pause
)
