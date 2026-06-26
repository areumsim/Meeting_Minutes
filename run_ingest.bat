@echo off
chcp 65001 >nul
title 오디오 수동 처리 (Ingest)
cd /d "%~dp0"
REM 특정 오디오 파일을 STT → 회의록 → Obsidian 노트로 처리
REM 사용법: run_ingest.bat <파일경로> [--type meeting|seminar|lecture] [--force]
REM 예시: run_ingest.bat "C:\Recordings\회의.m4a" --type meeting
echo.
if "%~1"=="" (
    echo  사용법: run_ingest.bat ^<파일경로^> [--type meeting^|seminar^|lecture] [--force]
    echo  예시  : run_ingest.bat "C:\Recordings\회의.m4a"
    echo.
    pause
    exit /b 1
)
python meeting_assistant.py ingest %*
if errorlevel 1 (
    echo.
    echo  [ERROR] ingest 실패. 파일 경로와 config.json 을 확인하세요.
    echo.
    pause
)
