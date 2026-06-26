@echo off
chcp 65001 >nul
title 회의 일정 관리
cd /d "%~dp0"
REM 회의 일정 정리·충돌 점검·대시보드 갱신
REM 볼트 경로는 config.json 의 obsidian.vault_path 에서 읽음
echo.
python plan_schedule.py --write-dashboard %*
if errorlevel 1 (
    echo.
    echo  [ERROR] plan_schedule.py 실패. config.json 의 obsidian.vault_path 를 확인하세요.
    echo.
    pause
)
