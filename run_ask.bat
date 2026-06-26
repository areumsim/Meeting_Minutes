@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title Vault Q&A (Ask)
cd /d "%~dp0"
REM Obsidian Vault 기반 LLM Q&A (이전 회의록·논문 노트 검색 후 출처 인용 답변)
REM 사용법: run_ask.bat "질문 내용" [--max-notes 5] [--show-sources]
REM 예시: run_ask.bat "지난 회의에서 결정된 사항은?"
echo.
if "%~1"=="" (
    echo  사용법: run_ask.bat "질문 내용" [--max-notes 5] [--show-sources]
    echo  예시  : run_ask.bat "지난 회의에서 결정된 사항은?"
    echo.
    set /p QUESTION="  질문을 입력하세요: "
    if "!QUESTION!"=="" (
        pause
        exit /b 1
    )
    python meeting_assistant.py ask "!QUESTION!" --show-sources
) else (
    python meeting_assistant.py ask %*
)
if errorlevel 1 (
    echo.
    echo  [ERROR] ask 실패. vault 인덱스가 빌드되었는지 run_reindex.bat 를 먼저 실행하세요.
    echo.
    pause
)
