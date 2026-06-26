@echo off
chcp 65001 >nul
title 계획 회의 사전 리서치 워처
cd /d "%~dp0"
REM 볼트의 00_Meetings 폴더를 감시하며 예정 회의 사전 자료를 자동 생성
REM 볼트 경로는 config.json 의 obsidian.vault_path 에서 읽음
echo.
python plan_watcher.py %*
if errorlevel 1 (
    echo.
    echo  [ERROR] plan_watcher.py 실패. config.json 의 obsidian.vault_path 를 확인하세요.
    echo.
    pause
)
